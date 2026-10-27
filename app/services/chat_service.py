"""问答业务逻辑。

阶段 02b-1 起，它真的会调模型了 —— 而且**只调一次**：先识别意图。
注意它依然不知道 HTTP 的存在：不 import fastapi、不碰 request_id、不碰状态码。

意图分出来之后真正"干活"的部分（RAG 检索、SQL 查询、多步执行）
都还没建，所以这里用**明确的分支 + 诚实的占位说明**表达出来。
不假装已经做完了 —— 这个分支结构本身就是后面几个阶段的施工图。
"""

import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.chat import ChatRequest
from app.schemas.intent import Intent, IntentResult
from app.services.intent_service import IntentService

logger = logging.getLogger(__name__)

# 每个意图将来由谁接手 —— 写死在代码里，一眼看出施工进度
_NEXT_STAGE = {
    Intent.KNOWLEDGE_QUERY: "阶段 04（RAG 企业知识库）会在这里检索制度文档并给出引用",
    Intent.DATA_ANALYSIS: "阶段 06（业务工具）会在这里生成 SQL 并查询模拟业务数据",
    Intent.TASK_EXECUTION: "阶段 07（Harness 权限与人工确认）会在这里执行多步任务",
    Intent.OUT_OF_SCOPE: "这类问题超出企业助手范围，不需要后续阶段",
}


@dataclass(slots=True)
class ChatResult:
    """service 的内部结果对象，不是对外契约。

    request_id 依然不在这里 —— 它属于请求上下文，由 api 层盖章。
    """

    answer: str
    stage: str
    model: str | None = None
    intent: Intent | None = None
    confidence: float | None = None
    reason: str | None = None
    repairs: int = 0
    total_tokens: int = 0


class ChatService:
    def __init__(self, intents: IntentService, settings: Settings) -> None:
        self._intents = intents
        self._settings = settings

    async def answer(self, payload: ChatRequest) -> ChatResult:
        logger.info(
            "chat_received",
            extra={"event": "chat_received", "session_id": payload.session_id},
        )

        intent, response, repairs = await self._intents.classify(payload.message)

        answer = self._render(intent)
        logger.info(
            "chat_answered",
            extra={
                "event": "chat_answered",
                "intent": intent.intent.value,
                "confidence": intent.confidence,
                "repairs": repairs,
                "total_tokens": response.total_tokens,
            },
        )

        return ChatResult(
            answer=answer,
            stage="02-intent",
            model=response.model,
            intent=intent.intent,
            confidence=intent.confidence,
            reason=intent.reason,
            repairs=repairs,
            total_tokens=response.total_tokens,
        )

    def _render(self, result: IntentResult) -> str:
        """按意图分流。

        **注意第一个分支**：置信度低于阈值时不硬猜，转为向用户澄清。
        这就是 JD 里"对 AI 输出设置质量门槛，不满足时触发重试/澄清/降级"的最小实现 ——
        宁可多问一句，也不要在没把握的时候乱猜然后走错整条流程。
        """
        threshold = self._settings.intent_confidence_threshold

        if result.confidence < threshold:
            return (
                f"我不太确定你的意思（置信度 {result.confidence:.2f} < "
                f"{threshold:.2f}，我的判断是「{result.intent.value}」）。\n"
                "能否补充一点信息？比如你是在问制度规定，还是想查具体业务数据？"
            )

        match result.intent:
            case Intent.CHITCHAT:
                return "你好，我是企业智能助手。你可以问我公司制度、业务数据，或让我处理具体事务。"
            case Intent.OUT_OF_SCOPE:
                return "这个问题不在我能处理的企业事务范围内，我就不猜了。"
            case _:
                hint = _NEXT_STAGE.get(result.intent, "")
                return (
                    f"已识别意图：{result.intent.value}（置信度 {result.confidence:.2f}）\n"
                    f"判断依据：{result.reason}\n"
                    f"→ {hint}（该阶段尚未实现）"
                )
