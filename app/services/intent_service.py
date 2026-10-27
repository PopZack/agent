"""意图识别服务。

它把"这句话是什么意思"这个业务问题，落到**一次结构化模型调用**上。
这一层很薄，就一个方法 —— 薄是对的：薄说明职责单一。
将来阶段 03 要带上会话历史、阶段 08 要换 Prompt 版本做对比评测，
改动都落在这一层或它的输入上，不会外溢。
"""

import logging
from typing import cast

from app.core.config import Settings
from app.llm.client import LLMClient, LLMResponse
from app.llm.prompts import PROMPT_VERSION, build_intent_messages
from app.schemas.intent import IntentResult

logger = logging.getLogger(__name__)


class IntentService:
    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    async def classify(self, question: str) -> tuple[IntentResult, LLMResponse, int]:
        """返回 (解析好的意图, 原始模型响应, 修复次数)。

        为什么把原始响应也交给上层：阶段 08 要做 Token 成本和延迟统计，
        这些数据只有 response 里有。让上层拿到它，比在 service 里再包一层统计更省事。
        """
        result = await self._llm.chat_structured(
            build_intent_messages(question),
            IntentResult,
            max_repairs=self._settings.llm_max_repairs,
        )
        # chat_structured 的 data 是 BaseModel，调用点知道具体是哪个 schema，这里做一次收窄
        parsed = cast(IntentResult, result.data)
        response = result.response

        logger.info(
            "intent_classified",
            extra={
                "event": "intent_classified",
                "intent": parsed.intent.value,
                "confidence": parsed.confidence,
                "repairs": result.repairs,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
            },
        )
        # Prompt 版本单独用 debug 记：它变化不频繁，但出问题时是第一个要查的东西
        logger.debug(
            "prompt_version_used",
            extra={"event": "prompt_version_used", "error_code": PROMPT_VERSION},
        )

        return parsed, response, result.repairs
