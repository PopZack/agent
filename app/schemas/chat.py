"""对外契约（API Schema）。

为什么请求和响应要用两个模型，而不是共用一个 ChatMessage：
  · 请求模型要"严"：限定长度、限定必填，把脏数据挡在门外（min_length=1 就是挡"空问题"）。
  · 响应模型要"稳"：它是给前端和未来的 MCP Client 依赖的契约，字段只能增不能改语义。
两个目标不同源，硬塞进一个类，改请求校验就容易连带破坏响应契约。
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "message": "帮我查一下公司的差旅报销制度，并告诉我住宿标准。",
                    "session_id": "s-1001",
                }
            ]
        }
    )

    message: str = Field(
        min_length=1,
        max_length=4000,
        description="用户问题原文",
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        description="会话 ID（POST /sessions 创建后获得）；不传则为无会话模式，不落库",
    )


class ChatResponse(BaseModel):
    request_id: str = Field(description="贯穿整条链路的请求标识，排查问题时用它捞日志")
    session_id: str | None = None
    answer: str
    stage: str = Field(description="当前处在哪个学习阶段，便于看出链路走到了哪一步")
    model: str | None = Field(default=None, description="实际使用的模型名")

    # ── 阶段 02b-1 新增（**只在末尾追加，不改已有字段**）──
    # 这就是"响应模型要稳"的具体做法：新需求只加字段，
    # 已经依赖这个契约的前端 / MCP Client 不会被破坏。
    intent: str | None = Field(default=None, description="识别出的意图类别")
    confidence: float | None = Field(default=None, description="意图置信度 0~1")
    reason: str | None = Field(default=None, description="模型的判断依据")
    repairs: int = Field(default=0, description="为拿到合法结构化输出额外重问了几次")
    total_tokens: int = Field(default=0, description="本次调用消耗的 token 总量")

    # ── 阶段 02b-2 新增 ──
    tools_used: list[str] = Field(default_factory=list, description="本次实际调用了哪些工具")
    tool_rounds: int = Field(default=0, description="工具轮次用掉了几轮")
