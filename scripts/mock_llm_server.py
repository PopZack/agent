"""模拟 OpenAI 兼容模型服务 —— 学习与测试专用，绝不接真实模型。

为什么需要它：
  · 重试、超时、限流这些机制，**不在真跑一遍的情况下无法验证**。
    只写断言"应该重试了"是自欺欺人，得有个东西真的返回 429 才行。
  · 学习阶段不该烧真金白银去调模型，也不该让测试依赖外网。
  · 阶段 05 的 Agent 执行循环要反复跑几十次模型调用，确定性非常重要 ——
    这个假服务返回固定内容，循环测试才可复现。

启动：
    python scripts/mock_llm_server.py            # 默认 127.0.0.1:8200
    python scripts/mock_llm_server.py --port 9000

然后在 .env 里：
    LLM_BASE_URL=http://127.0.0.1:8200/v1
    LLM_API_KEY=mock
    LLM_MODEL=mock-model

触发条件：在发给模型的**最后一条 user 消息**里放下面这些标记，就能复现故障场景。
    [[FLAKY_429_2]]   前 2 次调用返回 429，第 3 次成功   → 验证重试
    [[FLAKY_500_1]]   前 1 次返回 500，第 2 次成功
    [[ALWAYS_429]]    永远返回 429                       → 验证重试耗尽
    [[AUTH]]          返回 401                           → 验证"不重试，直接失败"
    [[NOTFOUND]]      返回 404
    [[BADBODY]]       返回 200 但结构不符合协议           → 验证上游格式漂移
    [[SLOW_5]]        先睡 5 秒再返回                     → 验证超时
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock LLM Server", version="0.1.0")

# 每个标记各自的调用计数 —— 用来实现"前 N 次失败"。
# 计数器不重置是有意的：重试会带着同样的标记再来一次，
# 第 3 次自然就过关了。
_CALL_COUNTS: dict[str, int] = {}

_FLAKY = re.compile(r"\[\[(FLAKY|ALWAYS)_(\d{3})_(\d+)\]\]")
_SLOW = re.compile(r"\[\[SLOW_(\d+)\]\]")


def _last_user_message(body: dict) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _pick_reply(user_text: str) -> str:
    """固定回复，保证测试可复现（不调用任何随机数）。"""
    return f"[mock-llm] 已收到：{user_text[:80]}"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    user_text = _last_user_message(body)
    model = body.get("model", "mock-model")
    started = time.perf_counter()

    # ── 触发"慢" ──
    if match := _SLOW.search(user_text):
        seconds = int(match.group(1))
        await asyncio.sleep(seconds)

    # ── 触发"固定失败" ──
    if "[[ALWAYS_429]]" in user_text:
        return _error(429, "rate limited by mock", model)

    if "[[AUTH]]" in user_text:
        return _error(401, "invalid api key", model)

    if "[[NOTFOUND]]" in user_text:
        return _error(404, "model not found", model)

    # ── 触发"前 N 次失败" ──
    if match := _FLAKY.search(user_text):
        marker, status, times = match.group(0), int(match.group(2)), int(match.group(3))
        _CALL_COUNTS[marker] = _CALL_COUNTS.get(marker, 0) + 1
        if _CALL_COUNTS[marker] <= times:
            resp = _error(status, f"flaky failure {_CALL_COUNTS[marker]}/{times}", model)
            resp.headers["Retry-After"] = "1"
            return resp

    if "[[BADBODY]]" in user_text:
        # 200 但结构不对：验证客户端会不会把"格式漂移"讲清楚
        return JSONResponse({"unexpected": "shape"})

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return JSONResponse(
        {
            "id": f"chatcmpl-mock-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _pick_reply(user_text)},
                    "finish_reason": "stop",
                }
            ],
            # token 数是**编的**（按字符粗略折算）。
            # 阶段 08 做成本优化时，这里要换成更接近真实 tokenizer 的估算。
            "usage": {
                "prompt_tokens": max(1, len(user_text) // 2),
                "completion_tokens": 20,
                "total_tokens": max(1, len(user_text) // 2) + 20,
            },
            "mock_latency_ms": latency_ms,
        }
    )


def _error(status: int, message: str, model: str) -> JSONResponse:
    # 故意用 OpenAI 的真实错误体形状，让客户端解析路径和线上一致
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "mock_error", "model": model}},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mock-llm", "calls": _CALL_COUNTS}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="模拟 OpenAI 兼容模型服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
