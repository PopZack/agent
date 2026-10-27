"""会话管理接口（阶段 03a）。

这一组接口回答三个问题：
  · 我的会话有哪些            → GET  /sessions
  · 某个会话聊了什么          → GET  /sessions/{id}/messages
  · 不要这个会话了            → DELETE /sessions/{id}

注意 user_id 从哪来：请求头 X-User-Name。学习项目没有登录体系，
但**接口形状**要按"有鉴权"的样子设计 —— user_id 是依赖注入给的，
将来换成 JWT 解析，这层的代码一行不用改。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import ChatRepository
from app.db.session import get_db_session
from app.schemas.session import MessageOut, SessionCreate, SessionOut, SessionRename

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def get_current_user_id(
    x_user_name: Annotated[str | None, Header(description="学习项目的临时身份")] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> uuid.UUID:
    """从请求头解析用户。生产环境这里是 JWT/SSO 的位置。

    故意走"仓储层找用户"而不是直接造一个：让身份解析也过一遍
    真实的数据路径，测试才能覆盖"用户不存在"的分支。
    """
    if not x_user_name:
        # 401 而不是 422：这是"没登录"，不是"参数格式错"
        raise HTTPException(status_code=401, detail="缺少 X-User-Name 请求头")
    repo = ChatRepository(db)
    user = await repo.get_or_create_user(x_user_name)
    return user.id


async def get_repo(db: Annotated[AsyncSession, Depends(get_db_session)]) -> ChatRepository:
    return ChatRepository(db)


async def get_optional_user_id(
    x_user_name: Annotated[str | None, Header(description="学习项目的临时身份")] = None,
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> uuid.UUID | None:
    """与 get_current_user_id 的区别：没有身份时返回 None 而不是 401。

    /chat 用它：无会话模式（不传 session_id）不需要身份，保持阶段 01/02
    的行为不变；一旦要写入会话（传了 session_id），没有身份才是 401。
    """
    if not x_user_name:
        return None
    repo = ChatRepository(db)
    user = await repo.get_or_create_user(x_user_name)
    return user.id


@router.get("", response_model=list[SessionOut], summary="我的会话列表")
async def list_sessions(
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    repo: Annotated[ChatRepository, Depends(get_repo)],
) -> list[SessionOut]:
    sessions = await repo.list_sessions(user_id)
    return [
        SessionOut(
            id=s.id,
            title=s.title,
            created_at=s.created_at,
            message_count=await repo.count_messages(s.id),
        )
        for s in sessions
    ]


@router.post("", response_model=SessionOut, summary="新建会话")
async def create_session(
    payload: SessionCreate,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    repo: Annotated[ChatRepository, Depends(get_repo)],
) -> SessionOut:
    session = await repo.create_session(user_id, payload.title or "新会话")
    return SessionOut(
        id=session.id, title=session.title, created_at=session.created_at, message_count=0
    )


@router.get("/{session_id}/messages", response_model=list[MessageOut], summary="会话消息")
async def list_messages(
    session_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    repo: Annotated[ChatRepository, Depends(get_repo)],
    limit: int = 50,
) -> list[MessageOut]:
    messages = await repo.list_messages(session_id, user_id, limit=limit)
    if messages is None:
        # 404：会话不存在**或不是你的** —— 对外统一说法，不泄露"它存在"这个事实
        raise HTTPException(status_code=404, detail="会话不存在")
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            intent=m.intent.value if m.intent else None,
            confidence=m.confidence,
            total_tokens=m.total_tokens,
            meta=m.meta,
            created_at=m.created_at,
        )
        for m in messages
    ]


@router.patch("/{session_id}", response_model=SessionOut, summary="重命名会话")
async def rename_session(
    session_id: uuid.UUID,
    payload: SessionRename,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    repo: Annotated[ChatRepository, Depends(get_repo)],
) -> SessionOut:
    session = await repo.rename_session(session_id, user_id, payload.title)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionOut(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        message_count=await repo.count_messages(session.id),
    )


@router.delete("/{session_id}", status_code=204, summary="删除会话（软删除）")
async def delete_session(
    session_id: uuid.UUID,
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
    repo: Annotated[ChatRepository, Depends(get_repo)],
) -> None:
    deleted = await repo.soft_delete_session(session_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
