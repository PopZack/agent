"""数据库表结构（SQLAlchemy 2.0 风格）。

三张表的设计理由，逐张说：

┌─ users ─────────────────────────────────────────────┐
│ 为什么要用户表：阶段 03 的交付标准是"支持多用户"。    │
│ 没有它，"会话属于谁"就无从谈起，多租户隔离（阶段 07  │
│ 的权限体系）也没有挂靠点。                            │
└─────────────────────────────────────────────────────┘

┌─ sessions ──────────────────────────────────────────┐
│ 会话 = 一段连续对话的容器。                           │
│ title 由首条消息截断生成 —— 列表页要用，用户要能一眼  │
│ 认出"哪段对话是哪段"。                                │
└─────────────────────────────────────────────────────┘

┌─ messages ──────────────────────────────────────────┐
│ 对话历史。role 直接用协议的四种（system/user/         │
│ assistant/tool），**存协议原样**而不是翻译成我们内部  │
│ 的枚举 —— 历史要能原封不动回灌给模型（阶段 03b 的     │
│ 核心动作），任何翻译层都是将来出 bug 的地方。          │
│                                                      │
│ intent/confidence/tokens 冗余在消息上而不是单独一张   │
│ "分析表"：它们和这条 assistant 消息是 1:1 的，拆表的  │
│ 代价（多一次 JOIN）大于收益。                          │
└─────────────────────────────────────────────────────┘

三个通用决策：
  · 主键用 UUID 而不是自增 int —— id 会进 URL 和日志，
    自增 id 会把"你有多少用户/消息"这个商业数据泄露给外界。
  · created_at 全部带时区（timestamptz）。不带时区的时间戳
    是定时炸弹：服务器时区一变，全部历史时间都错。
  · 软删除（deleted_at）而不是硬删除 —— 对话历史是审计材料
    （JD："数据保留、归档、删除和审计策略"），误删要能救回。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SAEnum

from app.schemas.intent import Intent


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # display_name 给人看；将来接企业 SSO 时这里换成 SSO 的用户标识
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list[ChatSession]] = relationship(back_populates="user")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="新会话")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 软删除：deleted_at 为 NULL 表示未删除
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
    messages: Mapped[list[ChatMessageRow]] = relationship(
        back_populates="session",
        # 消息必须按时间序读，顺序错了整段对话就乱了
        order_by="ChatMessageRow.created_at",
    )


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    # 存协议原样的 role 字符串：见模块 docstring 的理由
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 意图分析结果（只对 assistant 消息有意义，其余为 NULL）
    intent: Mapped[Intent | None] = mapped_column(SAEnum(Intent), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # 工具轨迹、request_id 等结构化附加信息。JSONB 而不是 JSON：
    # PG 里 JSONB 可索引、可查询，JSON 只是存了个字符串
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


# 复合索引：会话列表页最热的查询是"某会话按时间取最近 N 条"。
# 只有 session_id 单列索引的话，取最近 N 条还要额外排序；
# (session_id, created_at) 复合索引让 PG 直接按索引序读到即返回。
Index("ix_chat_messages_session_created", ChatMessageRow.session_id, ChatMessageRow.created_at)
