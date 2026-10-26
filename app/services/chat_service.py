"""问答业务逻辑。

阶段 01 这里只是一个"回声"，但它的位置很重要：
它标记出**未来 LLM 调用、RAG 检索、Agent 执行循环会被塞进来的地方**。
阶段 02 打开这个文件，把 answer() 里的假回答换成真正的模型调用即可，
api 层和 schema 层一行都不用动 —— 这就是"定点替换"。
"""

import logging
from dataclasses import dataclass

from app.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ChatResult:
    """service 的返回类型：**内部**结果对象，不是对外契约。

    刻意不用 ChatResponse 当返回值：ChatResponse 里有 request_id，
    而 request_id 属于 Web 请求上下文，不该由业务逻辑负责。让 api 层去拼装，
    业务逻辑就保持"只关心业务"。
    """

    answer: str
    stage: str
    model: str | None = None


class ChatService:
    async def answer(self, payload: ChatRequest) -> ChatResult:
        # 注意这行日志：没有传 request_id，它是 Filter 自动补上的
        logger.info(
            "chat_received",
            extra={"event": "chat_received", "session_id": payload.session_id},
        )

        # ⛔ 阶段 02 替换点：
        #    阶段 03 之后，这里前面会插入会话历史读取，后面会插入结果落库。
        result = ChatResult(
            answer=f"[阶段01 骨架] 已收到你的问题：{payload.message}",
            stage="01-skeleton",
        )

        logger.info("chat_answered", extra={"event": "chat_answered", "stage": result.stage})
        return result
