"""会话与消息的仓储层（Repository）。

仓储层的存在理由：service 层应该说"给我这个会话最近 20 条消息"，
而不应该说"SELECT ... WHERE session_id = ... ORDER BY created_at DESC LIMIT 20"。
SQL 细节集中在这一层，换 ORM、加读写分离、加缓存，都只动这里。

两个值得注意的实现细节：

1. 每个方法都带 user_id 条件 —— **多租户隔离写在仓储层**，
   而不是靠每个调用方记得加 WHERE。漏写一处就是越权访问。
   （阶段 07 的权限体系会在这个基础上再加"操作级"的检查。）

2. list_messages 用"倒序取、正序还"：ORDER BY created_at DESC LIMIT n
   取最近 N 条，再在 Python 里反转。直接 ORDER BY ASC LIMIT n 取的是
   **最早的** N 条 —— 这是分页语义上最经典的错误之一。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessageRow, ChatSession, User


class ChatRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── 用户 ────────────────────────────────────────────
    async def get_or_create_user(self, display_name: str) -> User:
        """按名字找用户，没有就建。

        学习项目没有登录体系，用名字当身份；阶段 07 换成真实鉴权时，
        这里改成"按 SSO 子创建"即可，调用方不用动。
        """
        result = await self._db.execute(select(User).where(User.display_name == display_name))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(display_name=display_name)
            self._db.add(user)
            await self._db.flush()  # flush 拿到主键，但不提交 —— 提交由 get_db_session 统一做
        return user

    # ── 会话 ────────────────────────────────────────────
    async def create_session(self, user_id: uuid.UUID, title: str) -> ChatSession:
        session = ChatSession(user_id=user_id, title=title)
        self._db.add(session)
        await self._db.flush()
        return session

    async def get_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> ChatSession | None:
        """带 user_id 条件：别人的会话查不到，等于不存在。"""
        result = await self._db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
                ChatSession.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_sessions(self, user_id: uuid.UUID) -> list[ChatSession]:
        result = await self._db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.deleted_at.is_(None))
            .order_by(ChatSession.created_at.desc())
        )
        return list(result.scalars())

    async def soft_delete_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """软删除。返回 False = 会话不存在或不属于该用户。"""
        result = await self._db.execute(
            update(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
                ChatSession.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
        )
        return result.rowcount > 0

    async def rename_session(
        self, session_id: uuid.UUID, user_id: uuid.UUID, title: str
    ) -> ChatSession | None:
        session = await self.get_session(session_id, user_id)
        if session is None:
            return None
        session.title = title
        await self._db.flush()
        return session

    # ── 消息 ────────────────────────────────────────────
    async def add_message(
        self,
        session_id: uuid.UUID,
        *,
        role: str,
        content: str,
        intent: str | None = None,
        confidence: float | None = None,
        total_tokens: int = 0,
        meta: dict | None = None,
    ) -> ChatMessageRow:
        row = ChatMessageRow(
            session_id=session_id,
            role=role,
            content=content,
            # intent 存的是 Intent 枚举成员；传字符串时转一下
            intent=intent,
            confidence=confidence,
            total_tokens=total_tokens,
            meta=meta or {},
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def list_messages(
        self, session_id: uuid.UUID, user_id: uuid.UUID, *, limit: int = 20
    ) -> list[ChatMessageRow] | None:
        """最近 limit 条消息，按时间正序返回。

        返回 None = 会话不存在或不属于该用户（和 get_session 一致的语义）；
        返回 [] = 会话存在但还没有消息。两种情况对调用方是不同的。
        """
        # 先验会话归属，再查消息 —— 越权检查不能省
        if await self.get_session(session_id, user_id) is None:
            return None
        result = await self._db.execute(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_at.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    async def count_messages(self, session_id: uuid.UUID) -> int:
        result = await self._db.execute(
            select(func.count())
            .select_from(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
        )
        return result.scalar_one()

    async def set_session_title_if_default(self, session_id: uuid.UUID, title: str) -> None:
        """首条消息后把"新会话"换成有信息量的标题（列表页要能认出对话）。"""
        await self._db.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.title == "新会话")
            .values(title=title[:60])
        )
