"""问答接口。

这一层只做四件事，一件都不多：
  1. 声明入参出参的 schema（校验交给 FastAPI 自动做）
  2. 声明依赖（谁提供服务、谁提供配置）
  3. 调 service 拿业务结果
  4. 把结果 + 请求上下文拼成对外响应

**没有业务逻辑，没有 try/except，没有模型调用** ——
这是"api 层不许下沉业务"这条约束的具体样子。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_chat_service
from app.core.context import request_id_var
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import ChatService

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, summary="一问一答")
async def chat(
    payload: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    result = await service.answer(payload)

    # request_id 是"请求上下文"，由 api 层负责盖章，业务层不碰它
    return ChatResponse(
        request_id=request_id_var.get(),
        session_id=payload.session_id,
        answer=result.answer,
        stage=result.stage,
        model=result.model,
        # 注意 .value：Intent 是 str 混合枚举，直接丢进去在 Python 3.11+ 上
        # str(Intent.X) 会得到 "Intent.X"，但格式化和 JSON 序列化又走另一套规则 ——
        # 这种微妙的差异不值得依赖，显式取 .value 最省心。
        intent=result.intent.value if result.intent else None,
        confidence=result.confidence,
        reason=result.reason,
        repairs=result.repairs,
        total_tokens=result.total_tokens,
    )
