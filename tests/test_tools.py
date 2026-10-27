"""工具系统测试：注册表、执行器的四道关、以及完整链路里的工具调用。

执行器是这一阶段的重点。它的四道关（白名单 → 解析 → 校验 → 超时）
每道都对应一类真实的模型犯错方式，所以每道都要有一条"模型真的这么犯了"的测试。
"""

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.client import LLMClient, ToolCall
from app.services.tool_service import ToolRunner
from app.tools.base import Tool, ToolError, ToolResult
from app.tools.business import (
    GetLeaveBalance,
    GetLeaveBalanceArgs,
    QueryDepartmentExpense,
    QueryDepartmentExpenseArgs,
    build_default_tools,
)
from app.tools.registry import ToolRegistry
from tests.fakes import (
    chat_service_with,
    intent_client,
    make_settings,
    ok_body,
    tool_call_json,
)

QUESTION = "查询2026-09各部门的费用情况"


class _EmptyArgs(BaseModel):
    """无参数工具的参数模型 —— 测试专用。"""


# ── 1. 注册表：重名必须启动时炸，而不是运行时悄悄用错 ──────
class _Dummy(Tool):
    name = "dup_tool"
    description = "x"
    args_model = None

    async def run(self, args):  # pragma: no cover
        return ToolResult(ok=True, content="")


def test_registry_rejects_duplicate_names():
    # 异常在构造时抛出，断言必须包住构造那一行
    with pytest.raises(ValueError, match="重复"):
        ToolRegistry([_Dummy(), _Dummy()])


def test_registry_rejects_nameless_tool():
    class NoName(Tool):
        name = ""
        description = "x"
        args_model = None

        async def run(self, args):  # pragma: no cover
            return ToolResult(ok=True, content="")

    with pytest.raises(ValueError, match="没有 name"):
        ToolRegistry([NoName()])


def test_registry_get_returns_none_for_unknown():
    registry = ToolRegistry(build_default_tools())
    assert registry.get("no_such_tool") is None  # 查不到是正常流量，不是异常


def test_schema_strips_titles():
    registry = ToolRegistry(build_default_tools())
    schema = registry.schema(["query_department_expense"])
    text = json.dumps(schema)
    assert '"title"' not in text  # title 纯占 token，模型不需要
    assert schema[0]["function"]["name"] == "query_department_expense"
    assert "month" in schema[0]["function"]["parameters"]["properties"]


def test_schema_whitelist_only_exposes_named():
    registry = ToolRegistry(build_default_tools())
    names = [t["function"]["name"] for t in registry.schema(["get_leave_balance"])]
    assert names == ["get_leave_balance"]


# ── 2. 业务工具本身 ─────────────────────────────────────
@pytest.mark.anyio
async def test_expense_tool_reports_over_budget():
    result = await QueryDepartmentExpense().run(QueryDepartmentExpenseArgs(month="2026-09"))
    payload = json.loads(result.content)
    assert result.ok
    # 超支的排前面（代码排序，不靠模型）
    assert payload["over_budget_departments"] == ["研发部", "销售部"]
    assert payload["departments"][0]["department"] == "研发部"
    assert payload["departments"][0]["over_budget"] == 32000


@pytest.mark.anyio
async def test_expense_tool_unknown_month_is_result_not_error():
    result = await QueryDepartmentExpense().run(QueryDepartmentExpenseArgs(month="2030-01"))
    # "查无数据"是正常查询结果（ok=False 的 ToolResult），不是异常
    assert result.ok is False
    assert result.error_code == "no_data"
    assert "可用月份" in result.content


@pytest.mark.anyio
async def test_leave_balance_tool_unknown_employee_raises_tool_error():
    with pytest.raises(ToolError, match="不存在"):
        await GetLeaveBalance().run(GetLeaveBalanceArgs(employee_id="E9999"))


# ── 3. 执行器四道关 ─────────────────────────────────────
def make_runner(tools=None, **overrides) -> ToolRunner:
    settings = make_settings(**overrides)
    registry = ToolRegistry(tools if tools is not None else build_default_tools())
    return ToolRunner(registry, settings)


@pytest.mark.anyio
async def test_gate1_unknown_tool_returns_available_list():
    runner = make_runner()
    result = await runner.run(ToolCall(id="c1", name="query_expense_v2", arguments="{}"))
    assert not result.ok
    assert result.error_code == "unknown_tool"
    # 把可用清单给模型，它下一轮才可能选对 —— 只说"不存在"它会再犯
    assert "query_department_expense" in result.content


@pytest.mark.anyio
async def test_gate2_invalid_json_arguments():
    runner = make_runner()
    result = await runner.run(
        ToolCall(id="c2", name="query_department_expense", arguments="{month: 2026-09}")
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments_json"
    assert "不是合法 JSON" in result.content


@pytest.mark.anyio
async def test_gate3_schema_validation_failure():
    runner = make_runner()
    result = await runner.run(
        ToolCall(
            id="c3",
            name="query_department_expense",
            arguments=json.dumps({"month": "2026/09"}),  # 格式不符
        )
    )
    assert not result.ok
    assert result.error_code == "invalid_arguments"
    # 错误信息里要带"实际给的是什么"
    assert "2026/09" in result.content


@pytest.mark.anyio
async def test_gate4_tool_timeout():
    class SlowTool(Tool):
        name = "slow_tool"
        description = "永远超时的工具"
        args_model = _EmptyArgs

        async def run(self, args):
            await asyncio.sleep(5)
            return ToolResult(ok=True, content="")  # pragma: no cover

    runner = make_runner([SlowTool()], tool_timeout_seconds=0.05)
    result = await runner.run(ToolCall(id="c4", name="slow_tool", arguments="{}"))
    assert not result.ok
    assert result.error_code == "tool_timeout"


@pytest.mark.anyio
async def test_tool_crash_is_contained():
    class CrashingTool(Tool):
        name = "crash_tool"
        description = "会崩的工具"
        args_model = _EmptyArgs

        async def run(self, args):
            raise RuntimeError("内部实现炸了")

    runner = make_runner([CrashingTool()])
    result = await runner.run(ToolCall(id="c5", name="crash_tool", arguments="{}"))
    # 工具的 bug 绝不能让整条链路崩掉
    assert not result.ok
    assert result.error_code == "tool_crashed"
    assert "内部错误" in result.content
    assert "RuntimeError" not in result.content  # 堆栈不进模型上下文


@pytest.mark.anyio
async def test_happy_path_runs_tool_and_returns_payload():
    runner = make_runner()
    result = await runner.run(
        ToolCall(
            id="c6",
            name="query_department_expense",
            arguments=json.dumps({"month": "2026-09"}),
        )
    )
    assert result.ok
    payload = json.loads(result.content)
    assert payload["month"] == "2026-09"
    assert result.duration_ms >= 0


# ── 4. 完整链路：模型请求工具 → 执行 → 回传 → 回答 ────────
def tool_flow_client(tool_name: str, arguments, answer: str) -> LLMClient:
    """意图轮给 JSON；工具轮给 tool_calls；拿到结果后的回答轮给文本。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json=ok_body(
                    json.dumps(
                        {
                            "intent": "data_analysis",
                            "confidence": 0.9,
                            "reason": "费用",
                            "slots": {},
                        },
                        ensure_ascii=False,
                    )
                ),
            )
        if payload.get("tools"):
            has_tool_result = any(m.get("role") == "tool" for m in payload.get("messages", []))
            if has_tool_result:
                # 模型已经拿到工具结果了，就该给最终回答 ——
                # 判断依据是**对话历史**，不是"请求带没带 tools"
                # （_run_tools 每一轮都带 tools，靠它分流会无限要工具）
                return httpx.Response(200, json=ok_body(answer))
            return httpx.Response(200, json=json.loads(tool_call_json(tool_name, arguments)))
        return httpx.Response(200, json=ok_body(answer))

    from tests.fakes import make_client

    return make_client(handler)


@pytest.mark.anyio
async def test_full_flow_calls_tool_and_answers():
    llm = tool_flow_client(
        "query_department_expense",
        {"month": "2026-09"},
        answer="研发部超支 32000 元。",
    )
    service = chat_service_with(llm)
    from app.schemas.chat import ChatRequest

    result = await service.answer(ChatRequest(message=QUESTION))

    assert result.tools_used == ["query_department_expense"]
    assert result.tool_rounds == 1
    assert "研发部" in result.answer
    assert result.total_tokens > 0


@pytest.mark.anyio
async def test_full_flow_survives_model_inventing_a_tool():
    """模型发明了不存在的工具 → 执行器拦下并把可用清单回传 → 模型改对了。"""
    # 状态机式假模型：意图轮给 JSON；工具轮先发明假工具名，看到失败回传后改对；
    # 拿到真工具结果后给最终回答。判断依据全部是**对话历史的内容**。
    state = {"invented": False, "corrected": False}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload.get("messages", [])
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json=ok_body(
                    json.dumps(
                        {"intent": "data_analysis", "confidence": 0.9, "reason": "x", "slots": {}},
                        ensure_ascii=False,
                    )
                ),
            )
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        if not tool_messages:
            state["invented"] = True
            return httpx.Response(
                200, json=json.loads(tool_call_json("query_expense_v2", {"month": "2026-09"}))
            )
        if any("没有名为" in str(m.get("content", "")) for m in tool_messages):
            # 看到白名单的失败回传，改用正确的工具名
            state["corrected"] = True
            return httpx.Response(
                200,
                json=json.loads(tool_call_json("query_department_expense", {"month": "2026-09"})),
            )
        return httpx.Response(200, json=ok_body("好的，根据数据：研发部超支。"))

    from tests.fakes import make_client

    llm = make_client(handler)
    service = chat_service_with(llm)
    from app.schemas.chat import ChatRequest

    result = await service.answer(ChatRequest(message=QUESTION))

    # 第一次失败 + 第二次成功，都留在轨迹里
    assert "query_expense_v2" in result.tools_used
    assert "query_department_expense" in result.tools_used
    assert result.tool_rounds == 2  # 两轮都真的执行了工具
    assert state["corrected"] is True  # 模型确实看到了失败原因并改对了


@pytest.mark.anyio
async def test_tool_rounds_are_bounded():
    """模型每轮都要工具 → 到达上限必须退出，不能无限烧钱。"""

    # 意图轮的 response_format 分流 + 工具轮永远给 tool_calls
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json=ok_body(
                    json.dumps(
                        {"intent": "data_analysis", "confidence": 0.9, "reason": "x", "slots": {}},
                        ensure_ascii=False,
                    )
                ),
            )
        # 这个测试刻意让模型"永远要工具"：即使历史里已有 tool 结果也继续要
        return httpx.Response(
            200, json=json.loads(tool_call_json("get_leave_balance", {"employee_id": "E1001"}))
        )

    from tests.fakes import make_client

    service = chat_service_with(make_client(handler), make_settings(max_tool_rounds=3))
    from app.schemas.chat import ChatRequest

    result = await service.answer(ChatRequest(message=QUESTION))

    assert result.tool_rounds == 3  # 恰好等于上限
    assert len(result.tools_used) == 3  # 每轮一次调用
    assert "已拿到" not in result.answer  # 没有装作成功


# ── 5. 流式 ─────────────────────────────────────────────
@pytest.mark.anyio
async def test_stream_yields_deltas_then_done():
    """流式接口的两类事件都要有：delta（内容）+ done（元数据）。"""

    # 意图轮 JSON；回答轮走 chat_stream —— MockTransport 同样能模拟流式响应
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json=ok_body(
                    json.dumps(
                        {"intent": "chitchat", "confidence": 0.95, "reason": "x", "slots": {}},
                        ensure_ascii=False,
                    )
                ),
            )
        assert payload.get("stream") is True  # 流式请求必须带 stream: true
        frames = (
            "".join(
                "data: "
                + json.dumps({"choices": [{"delta": {"content": piece}}]}, ensure_ascii=False)
                + "\n\n"
                for piece in ["你好，", "我是", "企业智能助手。"]
            )
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=frames.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )

    from tests.fakes import make_client

    service = chat_service_with(make_client(handler))
    from app.schemas.chat import ChatRequest

    chunks = []
    async for chunk in service.answer_stream(ChatRequest(message="你好")):
        chunks.append(chunk)

    deltas = [c for c in chunks if c.kind == "delta"]
    dones = [c for c in chunks if c.kind == "done"]
    assert len(dones) == 1
    assert "".join(d.text for d in deltas) == "你好，我是企业智能助手。"
    assert dones[0].result.intent.value == "chitchat"
    assert dones[0].result.confidence == 0.95


@pytest.mark.anyio
async def test_stream_skips_bad_frames_instead_of_dying():
    """坏帧（半截 JSON）必须被跳过并计数，而不是毁掉整条流。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if (payload.get("response_format") or {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json=ok_body(
                    json.dumps(
                        {"intent": "chitchat", "confidence": 0.95, "reason": "x", "slots": {}},
                        ensure_ascii=False,
                    )
                ),
            )
        frames = (
            "data: {broken json\n\n"
            "data: "
            + json.dumps({"choices": [{"delta": {"content": "完整内容"}}]}, ensure_ascii=False)
            + "\n\n"
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=frames.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )

    from tests.fakes import make_client

    service = chat_service_with(make_client(handler))
    from app.schemas.chat import ChatRequest

    text = []
    async for chunk in service.answer_stream(ChatRequest(message="你好")):
        if chunk.kind == "delta":
            text.append(chunk.text)

    assert "".join(text) == "完整内容"  # 坏帧跳过了，好帧还在


def test_sse_delta_parser_rules():
    from app.llm.client import _sse_delta

    assert _sse_delta("") is None  # 空行
    assert _sse_delta(": ping") is None  # 心跳注释
    assert _sse_delta("data: ") is None  # 空数据
    assert _sse_delta("data: [DONE]") == "[DONE]"
    assert _sse_delta("data: {broken") is None  # 半截 JSON
    assert _sse_delta('data: {"choices": [{"delta": {"content": "hi"}}]}') == "hi"
    # 合法但没有内容（只有 role）的帧 → None
    assert _sse_delta('data: {"choices": [{"delta": {"role": "assistant"}}]}') is None


# ── 6. 澄清路径在流式下也保持事件形状 ────────────────────
@pytest.mark.anyio
async def test_low_confidence_stream_still_emits_done():
    service = chat_service_with(intent_client("out_of_scope", 0.55))
    from app.schemas.chat import ChatRequest

    chunks = []
    async for chunk in service.answer_stream(ChatRequest(message="随便问一句")):
        chunks.append(chunk)

    # 澄清是本地拼的，但事件形状必须一致：delta + done
    assert chunks[0].kind == "delta"
    assert chunks[-1].kind == "done"
    assert "不太确定" in chunks[0].text
    assert chunks[-1].result.confidence == 0.55
