"""问答业务逻辑：意图识别 →（如需）工具调用 → 最终回答。

阶段 02b-2 起，一条请求最多经历 3 次模型调用，而且**顺序不能换**：

    1. 意图识别   结构化、非流式 —— 需要校验，所以不能流式
    2. 工具轮次   带 tools、非流式 —— 只在需要工具的意图下发生
    3. 回答轮次   不带 tools      —— 可以流式

**为什么把 2 和 3 拆成两次调用，而不是让模型"顺便把回答也写了"？**

  · 两次的约束是**相反**的：工具轮要严格（只输出调用、一个数字都不许编），
    回答轮要流畅（自然语言、照顾语气、该说"不知道"就说不知道）。
    一个 prompt 很难同时把这两件事都做到位 —— 硬塞在一起的结果通常是两头都软。
  · 回答轮不带 tools schema，prompt 短一截，**首字延迟直接受益**（流式体验）。
  · 代价：多一次调用，多一份 token 和延迟。**这个取舍必须是有意识做的**，
    不是顺手写出来的 —— 阶段 07 可以按任务复杂度决定要不要合并这两轮。

还有一条纪律贯穿全篇：**数值只信工具，不信模型**。模型负责把数据讲成人话，
计算、比较、判定这些确定性工作一律交给代码。
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.core.config import Settings
from app.llm.client import ChatMessage, LLMClient
from app.llm.prompts import build_answer_messages, build_tool_messages
from app.schemas.chat import ChatRequest
from app.schemas.intent import Intent
from app.services.intent_service import IntentService
from app.services.tool_service import ToolRunner
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 每种意图允许看到哪些工具 —— **工具白名单**。
# 只暴露相关的，模型的选择空间小了，选错的概率就小了；
# 顺带还省了 prompt token。这是权限控制的第一道闸，
# 阶段 07 会在此基础上再叠一层"这个用户有没有权限用"（那是另一个维度）。
_TOOLS_BY_INTENT: dict[Intent, tuple[str, ...]] = {
    Intent.DATA_ANALYSIS: ("query_department_expense",),
    Intent.TASK_EXECUTION: ("query_department_expense", "get_leave_balance"),
}


@dataclass(slots=True)
class ToolTrace:
    """一次工具调用的执行记录。

    为什么要留这个：出问题时用户会问"你到底做了什么"，运维会问"哪一步慢"，
    阶段 08 的评测会问"工具选择准确率多少"。三种诉求都要同一份数据。
    """

    name: str
    ok: bool
    error_code: str | None = None
    duration_ms: float = 0.0


@dataclass(slots=True)
class ChatResult:
    """service 的内部结果对象（不是对外契约）。"""

    answer: str
    stage: str
    model: str | None = None
    intent: Intent | None = None
    confidence: float | None = None
    reason: str | None = None
    repairs: int = 0
    total_tokens: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_rounds: int = 0


@dataclass(slots=True)
class StreamChunk:
    """流式接口的事件。

    流式需要**两类**事件，缺一不可：
      · delta —— 内容增量，前端实时渲染用
      · done  —— 结束时的元数据（意图、工具轨迹、耗时），落库、统计、审计用
    只吐内容不吐元数据，是最常见的流式接口设计缺陷：前端拿到了一堆字，
    却不知道这次到底识别成了什么意图、用没用工具。
    """

    kind: str  # "delta" | "done"
    text: str = ""
    result: ChatResult | None = None


@dataclass(slots=True)
class _Prepared:
    """两轮调用之间传递的中间状态。"""

    early: ChatResult | None = None  # 不必再调模型就已经有答案的情况（如低置信度澄清）
    messages: list[ChatMessage] = field(default_factory=list)
    intent: Intent | None = None
    confidence: float | None = None
    reason: str | None = None
    repairs: int = 0
    tokens: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_rounds: int = 0


class ChatService:
    def __init__(
        self,
        intents: IntentService,
        llm: LLMClient,
        registry: ToolRegistry,
        runner: ToolRunner,
        settings: Settings,
    ) -> None:
        self._intents = intents
        self._llm = llm
        self._registry = registry
        self._runner = runner
        self._settings = settings

    # ── 非流式入口 ──────────────────────────────────────
    async def answer(self, payload: ChatRequest) -> ChatResult:
        prepared = await self._prepare(payload)
        if prepared.early is not None:
            return prepared.early

        response = await self._llm.chat(prepared.messages)
        return self._finish(
            prepared,
            answer=response.content,
            model=response.model,
            extra_tokens=response.total_tokens,
        )

    # ── 流式入口 ────────────────────────────────────────
    async def answer_stream(self, payload: ChatRequest) -> AsyncIterator[StreamChunk]:
        prepared = await self._prepare(payload)

        if prepared.early is not None:
            # 澄清这类回答是本地拼出来的，没有"流"可言。仍然按流式的**形状**发出去
            # （一个 delta + 一个 done），这样前端不用为特例写两套逻辑。
            yield StreamChunk(kind="delta", text=prepared.early.answer)
            yield StreamChunk(kind="done", result=prepared.early)
            return

        parts: list[str] = []
        async for delta in self._llm.chat_stream(prepared.messages):
            parts.append(delta)
            yield StreamChunk(kind="delta", text=delta)

        # ⚠️ 已知欠账：流式路径下拿不到 token 用量，所以 total_tokens 只算了前两轮。
        # 原因：OpenAI 兼容协议默认**不在流里**返回 usage，需要显式传
        # stream_options={"include_usage": True}，而且我们的 SSE 解析器目前只取 content。
        # 阶段 08 做成本统计时必须补上这一块，否则流式请求的成本是瞎的。
        yield StreamChunk(
            kind="done",
            result=self._finish(
                prepared,
                answer="".join(parts),
                model=None,
                extra_tokens=0,
            ),
        )

    # ── 两轮共用的准备阶段 ──────────────────────────────
    async def _prepare(self, payload: ChatRequest) -> _Prepared:
        logger.info(
            "chat_received",
            extra={"event": "chat_received", "session_id": payload.session_id},
        )

        intent, response, repairs = await self._intents.classify(payload.message)
        prepared = _Prepared(
            intent=intent.intent,
            confidence=intent.confidence,
            reason=intent.reason,
            repairs=repairs,
            tokens=response.total_tokens,
        )

        # 质量门槛：置信度不够就转为澄清，**不硬猜**
        threshold = self._settings.intent_confidence_threshold
        if intent.confidence < threshold:
            prepared.early = self._make_result(
                prepared,
                answer=(
                    f"我不太确定你的意思（置信度 {intent.confidence:.2f} < {threshold:.2f}，"
                    f"我的判断是「{intent.intent.value}」）。\\n"
                    "能否补充一点信息？比如你是在问制度规定，还是想查具体业务数据？"
                ),
                model=response.model,
            )
            return prepared

        tool_names = _TOOLS_BY_INTENT.get(intent.intent, ())
        tail = [ChatMessage("user", payload.message)]

        if tool_names:
            tail, trace, rounds, tool_tokens = await self._run_tools(payload.message, tool_names)
            prepared.tools_used = [t.name for t in trace]
            prepared.tool_rounds = rounds
            prepared.tokens += tool_tokens
            for item in trace:
                logger.info(
                    "tool_trace",
                    extra={
                        "event": "tool_trace",
                        "tool_name": item.name,
                        "tool_ok": item.ok,
                        "error_code": item.error_code,
                        "round": rounds,
                    },
                )

        prepared.messages = build_answer_messages(tail)
        return prepared

    async def _run_tools(
        self, question: str, tool_names: tuple[str, ...]
    ) -> tuple[list[ChatMessage], list[ToolTrace], int, int]:
        """跑工具轮次，返回 (给回答轮的消息尾巴, 轨迹, 轮次数, 消耗token)。

        循环有两道闸，缺一不可：
          · max_tool_rounds —— 模型可能反复要工具，形成烧钱循环
          · 只有模型**不再请求工具**时才退出
        这和阶段 05 的 Agent 执行循环是同一个形状，只是这里还没有规划、
        没有状态机、没有检查点 —— 那些是阶段 05 要补的。
        """
        messages = build_tool_messages(question)
        trace: list[ToolTrace] = []
        tokens = 0
        # rounds 记的是"实际执行了工具的轮次"，不是"循环走到第几轮" ——
        # 最后一轮模型只给文本不再要工具时，那一轮不该被计入。
        rounds = 0
        max_rounds = self._settings.max_tool_rounds

        for round_index in range(1, max_rounds + 1):
            response = await self._llm.chat(
                messages,
                tools=self._registry.schema(tool_names),
            )
            tokens += response.total_tokens

            if not response.has_tool_calls:
                break

            # assistant 的这条消息必须把 tool_calls **原样**塞回历史，
            # 否则后面那条 tool 消息的 tool_call_id 就找不到配对，
            # 服务端会直接报 400。
            messages.append(
                ChatMessage(
                    "assistant",
                    response.content or "",
                    tool_calls=[call.to_history() for call in response.tool_calls],
                )
            )
            rounds = round_index  # 这一轮真的执行了工具，才计入
            for call in response.tool_calls:
                result = await self._runner.run(call)
                trace.append(
                    ToolTrace(
                        name=call.name,
                        ok=result.ok,
                        error_code=result.error_code,
                        duration_ms=result.duration_ms,
                    )
                )
                messages.append(ChatMessage("tool", result.content, tool_call_id=call.id))
        else:
            logger.warning(
                "tool_rounds_exhausted",
                extra={"event": "tool_rounds_exhausted", "round": rounds},
            )

        # 去掉工具轮的 system prompt —— 回答轮会换上自己的那一份。
        # 两轮用不同 system prompt，是本阶段最容易理解错的一点：
        # system prompt 不是"角色设定"一锤定音的，它可以、也应该随**阶段任务**变。
        return messages[1:], trace, rounds, tokens

    def _finish(
        self,
        prepared: _Prepared,
        *,
        answer: str,
        model: str | None,
        extra_tokens: int,
    ) -> ChatResult:
        prepared.tokens += extra_tokens
        return self._make_result(prepared, answer=answer, model=model)

    def _make_result(self, prepared: _Prepared, *, answer: str, model: str | None) -> ChatResult:
        logger.info(
            "chat_answered",
            extra={
                "event": "chat_answered",
                "intent": prepared.intent.value if prepared.intent else None,
                "confidence": prepared.confidence,
                "repairs": prepared.repairs,
                "round": prepared.tool_rounds,
                "total_tokens": prepared.tokens,
            },
        )
        return ChatResult(
            answer=answer,
            stage="02b-2-tools",
            model=model,
            intent=prepared.intent,
            confidence=prepared.confidence,
            reason=prepared.reason,
            repairs=prepared.repairs,
            total_tokens=prepared.tokens,
            tools_used=prepared.tools_used,
            tool_rounds=prepared.tool_rounds,
        )
