"""模型接入层测试。

这一组测试和阶段 01 的写法不同：用的是 httpx.MockTransport，
在 **HTTP 传输层**替换掉真实网络，而不是替换掉我们的代码。

为什么这比 mock 掉 LLMClient 更好：
  mock 掉 LLMClient 只能验证"调用方以为会发生的事"；
  替换 transport 则连 URL 拼接、请求头、序列化都一起验了 ——
  而"URL 到底拼成 .../v1/chat/completions 还是 .../chat/completions"
  恰恰是最容易错、又最难发现的细节之一（错了就是一个 404）。
"""

import json
import logging

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import UpstreamError
from app.llm.client import ChatMessage, LLMClient


def make_settings(**overrides) -> Settings:
    base = {
        "llm_base_url": "http://mock.local/v1",
        "llm_api_key": "test-key",
        "llm_model": "mock-model",
        "llm_temperature": 0.0,
        "llm_timeout_seconds": 2.0,
        "llm_max_retries": 3,
        # 退避压到毫秒级：测试不该真的睡几秒，否则一套测试跑下来要几分钟
        "llm_retry_base_seconds": 0.001,
        "llm_retry_max_seconds": 0.01,
    }
    base.update(overrides)
    return Settings(**base)


def ok_body(content: str = "好的", *, prompt_tokens: int = 11, completion_tokens: int = 22) -> dict:
    return {
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def make_client(handler, **overrides) -> LLMClient:
    return LLMClient(make_settings(**overrides), transport=httpx.MockTransport(handler))


# ── 1. 正常路径：请求长什么样、响应怎么解析 ────────────────
@pytest.mark.anyio
async def test_successful_call_shape_and_parsing():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok_body("你好"))

    client = make_client(handler)
    resp = await client.chat([ChatMessage("user", "你好")])

    assert resp.content == "你好"
    assert resp.attempts == 1
    assert resp.model == "mock-model"
    assert resp.finish_reason == "stop"
    assert resp.prompt_tokens == 11
    assert resp.total_tokens == 33
    assert resp.latency_ms >= 0
    assert len(seen) == 1

    # 把"一次模型调用到底发了什么"钉死在测试里 —— 这就是协议本身
    request = seen[0]
    assert str(request.url) == "http://mock.local/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["content-type"] == "application/json"
    payload = json.loads(request.content)
    assert payload == {
        "model": "mock-model",
        "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.0,
    }


# ── 2. 429（限流）要重试，并沿用服务端的 Retry-After ──────
@pytest.mark.anyio
async def test_retries_on_429_then_succeeds():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(
                429,
                headers={"Retry-After": "1"},
                json={"error": {"message": "rate limited"}},
            )
        return httpx.Response(200, json=ok_body("第三次才成功"))

    client = make_client(handler)
    resp = await client.chat([ChatMessage("user", "hi")])

    assert resp.content == "第三次才成功"
    assert resp.attempts == 3  # 前两次 429 + 第三次 200
    assert len(calls) == 3


# ── 3. 一直失败要放弃，且失败次数正好等于配置上限 ──────────
@pytest.mark.anyio
async def test_gives_up_after_max_retries():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client = make_client(handler, llm_max_retries=3)
    with pytest.raises(UpstreamError) as excinfo:
        await client.chat([ChatMessage("user", "hi")])

    assert excinfo.value.details["status"] == 500
    assert excinfo.value.details["attempts"] == 3
    assert len(calls) == 3  # 不会无限重试 —— 必须有上限，否则会白烧钱


# ── 4. 401 是配置问题，重试没有意义：必须"一次就放弃" ──────
@pytest.mark.anyio
async def test_auth_error_is_not_retried():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    client = make_client(handler)
    with pytest.raises(UpstreamError) as excinfo:
        await client.chat([ChatMessage("user", "hi")])

    assert len(calls) == 1  # ← 关键断言：一次都没多试
    assert "LLM_API_KEY" in excinfo.value.message  # 错误信息要能直接指导修复


# ── 5. 超时也走重试，耗尽后翻译成 UpstreamError ────────────
@pytest.mark.anyio
async def test_timeout_is_retried_then_raises():
    calls: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("simulated read timeout", request=request)

    client = make_client(handler, llm_max_retries=2)
    with pytest.raises(UpstreamError) as excinfo:
        await client.chat([ChatMessage("user", "hi")])

    assert "超时" in excinfo.value.message
    assert excinfo.value.details["attempts"] == 2
    assert len(calls) == 2


# ── 6. 上游格式漂移：要讲清楚，不能丢 KeyError ──────────────
@pytest.mark.anyio
async def test_unknown_response_shape_is_reported_clearly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = make_client(handler)
    with pytest.raises(UpstreamError) as excinfo:
        await client.chat([ChatMessage("user", "hi")])

    assert "不符合 OpenAI 兼容协议" in excinfo.value.message
    assert "unexpected" in excinfo.value.details["body"]


# ── 7. 没配 base_url 时，报错要能指导人怎么改 ──────────────
@pytest.mark.anyio
async def test_missing_base_url_gives_actionable_hint():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("配置都没填，不该发出任何请求")

    client = make_client(handler, llm_base_url="")
    with pytest.raises(UpstreamError) as excinfo:
        await client.chat([ChatMessage("user", "hi")])

    assert "未配置 LLM_BASE_URL" in excinfo.value.message
    assert "指" in excinfo.value.details["hint"] or ".env" in excinfo.value.details["hint"]


# ── 8. 重试节奏：Retry-After 优先、指数退避、有上限 ──────────
def test_retry_delay_policy():
    # 用较大的基数，让各段区间能看清；否则全被压到毫秒级看不清规律
    client = make_client(
        lambda request: httpx.Response(200),
        llm_retry_base_seconds=1.0,
        llm_retry_max_seconds=30.0,
    )
    delay = client._retry_delay

    assert delay(1, "2") == 2.0  # 服务端说了 Retry-After，就听它的
    assert 0.5 <= delay(1, None) <= 1.0  # 第 1 次：base(1.0) x 抖动[0.5,1.0]
    assert 1.0 <= delay(2, None) <= 2.0  # 第 2 次：2 x base
    assert 2.0 <= delay(3, None) <= 4.0  # 第 3 次：4 x base —— 指数增长
    assert 15.0 <= delay(10, None) <= 30.0  # 大到离谱时被上限截住
    assert 0.5 <= delay(1, "not-a-number") <= 1.0  # HTTP-date 解析不了就退回退避

    # 抖动必须真的在抖：如果实现里漏了随机数，同一档位每次都会算出同一个值，
    # 上面那些区间断言依然会通过 —— 所以要多取几次样本才能把它抓住。
    samples = [delay(2, None) for _ in range(20)]
    assert all(1.0 <= s <= 2.0 for s in samples)
    assert len(set(samples)) > 1


# ── 9. 重试必须留下日志，否则线上根本不知道发生过 ──────────
@pytest.mark.anyio
async def test_retry_is_logged(caplog):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json=ok_body())

    client = make_client(handler)
    with caplog.at_level(logging.INFO, logger="app.llm.client"):
        await client.chat([ChatMessage("user", "hi")])

    retries = [r for r in caplog.records if getattr(r, "event", "") == "llm_retry"]
    assert len(retries) == 1
    assert retries[0].error_code == "http_503"
    assert retries[0].attempt == 1
    assert retries[0].delay_ms >= 0

    oks = [r for r in caplog.records if getattr(r, "event", "") == "llm_call_ok"]
    assert len(oks) == 1
    assert oks[0].attempt == 2  # 日志里要能看出"这次是第几次尝试成功的"
