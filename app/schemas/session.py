"""会话与消息的对外契约。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=60, description="不填则默认「新会话」")


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=60)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    message_count: int = 0


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    intent: str | None = None
    confidence: float | None = None
    total_tokens: int = 0
    meta: dict = Field(default_factory=dict)
    created_at: datetime
