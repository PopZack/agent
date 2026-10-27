"""结构化输出（chat_structured）的测试。

这组测试全部针对"模型不听话"这个核心麻烦：
返回围栏、返回散文、返回非法枚举、修一次还错、置信度写成百分比。
每一条都对应一个真实的线上故障 —— 它们不是假想的边界情况。
"""

import json

import httpx
import pytest

from app.core.exceptions import UpstreamError
from app.llm.client import ChatMessage
from app.schemas.intent import Intent, IntentResult
from tests.fakes import (
    intent_json,
    make_client,
    make_settings,
    ok_body,
    scripted_client,
    text_client,
)

MESSAGES = [ChatMessage("user", "帮我查一下差旅报销标准")]


# ── 1. 顺利路径：一次就成，repairs = 0 ─────────────────────
@pytest.mark.anyio
async def test_valid_json_parses_without_repair():
    calls: list[int] = []
    client = text_client(intent_json("knowledge_query", 0.9), calls=calls)

    result = await client.chat_structured(MESSAGES, IntentResult)

    parsed = result.data
    assert isinstance(parsed, IntentResult)
    assert parsed.intent is Intent.KNOWLEDGE_QUERY
    assert parsed.confidence == 0.9
    assert result.repairs == 0
    assert len(calls) == 1  # 没有多余的调用 = 没有多余的花费


# ── 2. 围栏清洗：模型把 JSON 包在 ```json 里，不该被判为失败 ──
@pytest.mark.anyio
async def test_markdown_fence_is_stripped():
    fenced = "```json\n" + intent_json("chitchat", 0.95) + "\n```"
    client = text_client(fenced)

    result = await client.chat_structured(MESSAGES, IntentResult)

    assert result.data.intent is Intent.CHITCHAT
    assert result.repairs == 0  # 清洗掉就够了，不需要花一次修复调用


# ── 3. 散文前缀：模型先寒暄一句再给 JSON ─────────────────────
@pytest.mark.anyio
async def test_prose_prefix_is_tolerated():
    noisy = "好的，我来分析这句话的意图：\n" + intent_json("data_analysis", 0.8)
    client = text_client(noisy)

    result = await client.chat_structured(MESSAGES, IntentResult)

    assert result.data.intent is Intent.DATA_ANALYSIS
    assert result.repairs == 0


# ── 4. 修复路径：第一次非法枚举，第二次对了 ──────────────────
@pytest.mark.anyio
async def test_repair_succeeds_and_feeds_back_the_error():
    bad = intent_json("refund_request", 0.9)  # 这个类别不存在
    good = intent_json("knowledge_query", 0.88)
    seen: list[httpx.Request] = []

    # 想看修复轮到底发了什么，得自己接管 transport
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok_body(bad if len(seen) == 1 else good))

    client = make_client(handler)
    result = await client.chat_structured(MESSAGES, IntentResult)

    assert result.data.intent is Intent.KNOWLEDGE_QUERY
    assert result.repairs == 1
    assert len(seen) == 2

    # 关键：修复轮的消息里必须带上"它自己的错输出"和"确切的错误原因"，
    # 否则模型只能原样再错一遍
    repair_messages = json.loads(seen[1].content)["messages"]
    assert len(repair_messages) == 3  # 原提问 + 模型的错输出 + 修复指令
    assert repair_messages[1]["role"] == "assistant"
    assert "refund_request" in repair_messages[1]["content"]
    assert "JSON Schema" in repair_messages[2]["content"]  # schema 被贴给它了
    assert "refund_request" in repair_messages[2]["content"]  # 错误信息也在里面


# ── 5. 修复必须有界：修不好就明确失败，不能无限烧钱 ──────────
@pytest.mark.anyio
async def test_repair_is_bounded_and_fails_loudly():
    calls: list[int] = []
    bad = intent_json("refund_request", 0.9)
    client = text_client(bad, calls=calls)

    with pytest.raises(UpstreamError) as excinfo:
        await client.chat_structured(MESSAGES, IntentResult, max_repairs=1)

    assert len(calls) == 2  # 首次 + 1 次修复 = 上限，一次都没多
    assert excinfo.value.details["schema"] == "IntentResult"
    assert excinfo.value.details["repairs"] == 1
    assert "refund_request" in excinfo.value.details["last_error"]
    assert "refund_request" in excinfo.value.details["raw"]  # 原始输出留着排查用


# ── 6. 宽进严出：模型把 0.9 写成 92 ──────────────────────────
@pytest.mark.anyio
async def test_percentage_confidence_is_normalized_without_repair():
    client = text_client(intent_json("knowledge_query", 92))

    result = await client.chat_structured(MESSAGES, IntentResult)

    assert result.data.confidence == 0.92
    assert result.repairs == 0  # 归一化挡在门口，没浪费修复调用


# ── 7. 完全不返回 JSON（散文）也要走进修复，而不是崩在解析上 ──
@pytest.mark.anyio
async def test_pure_prose_triggers_repair_then_succeeds():
    client = scripted_client(["我认为这是在询问公司制度。", intent_json("knowledge_query", 0.85)])

    result = await client.chat_structured(MESSAGES, IntentResult)

    assert result.data.intent is Intent.KNOWLEDGE_QUERY
    assert result.repairs == 1


# ── 8. JSON mode 可关：关掉后请求体里不该出现 response_format ──
@pytest.mark.anyio
async def test_json_mode_can_be_disabled():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ok_body(intent_json()))

    client = make_client(handler, llm_use_json_mode=False)
    await client.chat_structured(MESSAGES, IntentResult)

    payload = json.loads(seen[0].content)
    assert "response_format" not in payload

    # 反过来：开着的时候必须带上，这是让服务端配合的关键
    seen.clear()
    client2 = make_client(handler, llm_use_json_mode=True)
    await client2.chat_structured(MESSAGES, IntentResult)
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


# ── 9. 容忍的边界：超过百分比区间的值必须失败，不能被"归一化"蒙混掉 ──
def test_schema_rejects_absurd_confidence():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IntentResult.model_validate(
            {"intent": "knowledge_query", "confidence": 150.0, "reason": "x"}
        )


def test_schema_accepts_percentage_and_fraction_forms():
    assert (
        IntentResult.model_validate(
            {"intent": "chitchat", "confidence": 90, "reason": "x"}
        ).confidence
        == 0.9
    )
    assert (
        IntentResult.model_validate(
            {"intent": "chitchat", "confidence": 0.9, "reason": "x"}
        ).confidence
        == 0.9
    )


def test_settings_defaults_are_sane():
    settings = make_settings()
    assert settings.llm_max_repairs >= 1  # 没有修复能力的话结构化输出会很脆
    assert 0 < settings.intent_confidence_threshold < 1
