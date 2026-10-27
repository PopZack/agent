"""问答接口：一个普通版，一个流式版。

这一层只做四件事，一件都不多：声明 schema、声明依赖、调 service、拼响应。
**没有业务逻辑、没有 try/except（流式的除外，见说明）、没有模型调用。**

关于流式那个接口，有两个必须知道的点，都写在代码注释里：
  1. 响应头一旦发出去（200 + text/event-stream），就**再也改不了 HTTP 状态码**。
     所以流式里的错误只能作为一条 SSE 事件发出去，不能指望 500。
  2. nginx 默认会缓冲代理响应，能把"流式"攒成一坨再吐。要显式关掉。
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import get_chat_service
from app.core.context import request_id_var
from app.core.exceptions import AppError
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, summary="一问一答")
async def chat(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    result = await service.answer(payload)

    # request_id 是"请求上下文"，由 api 层盖章，业务层不碰它。
    # 注意 .value：Intent 是 StrEnum，显式取 .value 最省心，不依赖枚举的格式化行为。
    return ChatResponse(
        request_id=request_id_var.get(),
        session_id=payload.session_id,
        answer=result.answer,
        stage=result.stage,
        model=result.model,
        intent=result.intent.value if result.intent else None,
        confidence=result.confidence,
        reason=result.reason,
        repairs=result.repairs,
        total_tokens=result.total_tokens,
        tools_used=result.tools_used,
        tool_rounds=result.tool_rounds,
    )


def _sse(payload: dict) -> str:
    r"""一条 SSE 帧：`data: {json}` + 空行。

    空行是帧的分隔符，漏了它浏览器不会派发事件 —— 这是手写 SSE 最常踩的坑。
    ensure_ascii=False 让中文保持可读，否则会变成 \uXXXX（调试时完全没法看）。
    """
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream", summary="一问一答（流式 SSE）")
async def chat_stream(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse:
    # ⚠️ 在生成器**外面**先把 request_id 取出来。
    # 生成器是在响应开始之后才被迭代的，那时请求上下文未必还在；
    # 与其去赌 ContextVar 的生命周期，不如提前拿好 —— 便宜且永远正确。
    request_id = request_id_var.get()

    async def event_source() -> AsyncIterator[str]:
        try:
            async for chunk in service.answer_stream(payload):
                if chunk.kind == "delta":
                    yield _sse({"type": "delta", "text": chunk.text})
                    continue
                result = chunk.result
                yield _sse(
                    {
                        "type": "done",
                        "request_id": request_id,
                        "stage": result.stage if result else None,
                        "intent": result.intent.value if result and result.intent else None,
                        "confidence": result.confidence if result else None,
                        "tools_used": result.tools_used if result else [],
                        "tool_rounds": result.tool_rounds if result else 0,
                        "total_tokens": result.total_tokens if result else 0,
                        # 明确告诉前端：内容到底是真流式吐出来的，还是一次性给的。
                        # 前端据此决定要不要做打字机动画 —— 别让 UI 撒谎。
                        "streamed": True,
                    }
                )
        except AppError as exc:
            # 业务异常：响应头已经发出去了，只能作为事件通知前端
            logger.warning(
                "stream_app_error",
                extra={"event": "stream_app_error", "error_code": exc.code},
            )
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except Exception:
            logger.exception("stream_unhandled_error", extra={"event": "stream_unhandled_error"})
            yield _sse({"type": "error", "code": "internal_error", "message": "服务内部错误"})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx 等反向代理默认会缓冲响应体，把流式攒成整块 —— 这行是关掉它。
            # 少了它，本地测是流式、上线就"变成"非流式，是最经典的"本地好好的"事故。
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )
