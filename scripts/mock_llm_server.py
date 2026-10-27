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
import json
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


# ── 假装会做意图分类（纯关键词规则，确定性输出）───────────────
# 它不聪明，但足够让整条链路真跑起来；真正"聪明"的部分由真模型提供，
# 这里只负责让链路的形状可验证。
# 顺序即优先级，这一点很实际："根据公司的请假**制度**，帮我生成请假申请"
# 同时命中"制度"（知识查询）和"请假"（要动作）。要动作更"重"，
# 所以 task_execution 必须排在 knowledge_query 前面。
_INTENT_RULES: list[tuple[tuple[str, ...], str, float]] = [
    (("请假", "申请", "生成", "提交"), "task_execution", 0.90),
    (("报销", "制度", "标准", "年假", "流程", "政策", "规定"), "knowledge_query", 0.92),
    (("费用", "预算", "超支", "统计", "各部门", "支出"), "data_analysis", 0.88),
    (("你好", "您好", "谢谢", "再见"), "chitchat", 0.95),
]


def _intent_payload(question: str, *, marker_text: str) -> dict:
    """假装分类。

    ⚠️ 两个入参必须分开，这是个真踩过的坑：
      · 分类只能看**用户这句话**（question）；
      · 触发标记要在**整个对话**（marker_text）里找，因为修复轮里
        最后一条消息变成了"请只输出 JSON"，标记只存在于早期消息中。

    如果偷懒拿整个对话去分类，system prompt 里的"报销""制度""请假"这些词
    会把每一条用户输入都匹配成 knowledge_query —— 提示词自带的那套词汇
    会压倒真正的用户意图。真实系统里同样如此：**别把提示词喂给分类器**。
    """
    if "[[BAD_CONFIDENCE]]" in marker_text:
        # 故意返回百分比写法：验证客户端的"宽进严出"归一化，且不该触发修复
        return _intent_dict("knowledge_query", 92)
    if "[[BAD_ENUM]]" in marker_text:
        # 故意编一个不存在的类别：客户端必须拒绝并触发修复
        return _intent_dict("refund_request", 0.9)
    for keywords, intent, confidence in _INTENT_RULES:
        if any(keyword in question for keyword in keywords):
            return _intent_dict(intent, confidence)
    # 兜底给一个**低置信度**结果：正好用来演示"低于门槛就转为澄清"
    return _intent_dict("out_of_scope", 0.55)


def _intent_dict(intent: str, confidence: float) -> dict:
    return {
        "intent": intent,
        "confidence": confidence,
        "reason": f"mock 规则命中：{intent}",
        "slots": {},
    }


def _completion(content: str, model: str, prompt_text: str) -> JSONResponse:
    """统一的 OpenAI 兼容响应信封。"""
    return JSONResponse(
        {
            "id": f"chatcmpl-mock-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            # token 数是**编的**（按字符粗略折算）。
            # 阶段 08 做成本优化时，要换成更接近真实 tokenizer 的估算。
            "usage": {
                "prompt_tokens": max(1, len(prompt_text) // 2),
                "completion_tokens": max(1, len(content) // 2),
                "total_tokens": max(1, len(prompt_text) // 2) + max(1, len(content) // 2),
            },
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    user_text = _last_user_message(body)
    model = body.get("model", "mock-model")

    # 触发标记在**整个对话**里找，不只看最后一条 user 消息。
    # 因为修复重试时最后一条消息是"请只输出 JSON"，只找最后一条会漏掉标记，
    # "修复依然失败 → 耗尽"这种场景就演示不出来了。
    full_text = " ".join(str(m.get("content", "")) for m in body.get("messages", []))
    # 修复指令里一定带着 JSON Schema，用它识别"这是修复轮"
    in_repair_turn = "JSON Schema" in full_text

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

    # ── 结构化输出模式：客户端要的是 JSON ──
    if (body.get("response_format") or {}).get("type") == "json_object":
        if "[[RAW_TEXT]]" in full_text and not in_repair_turn:
            # 第一轮故意返回散文，验证客户端的修复路径真的会被触发；
            # 修复轮则返回合法 JSON，验证"修复能成功"
            return _completion("好的，我认为这句话是在询问公司制度。", model, user_text)
        encoded = json.dumps(_intent_payload(user_text, marker_text=full_text), ensure_ascii=False)
        return _completion(encoded, model, user_text)

    if "[[BADBODY]]" in user_text:
        # 200 但结构不对：验证客户端会不会把"格式漂移"讲清楚
        return JSONResponse({"unexpected": "shape"})

    return _completion(_pick_reply(user_text), model, user_text)


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
