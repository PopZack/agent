"""对外契约（API Schema）。

为什么请求和响应要用两个模型，而不是共用一个 ChatMessage：
  · 请求模型要"严"：限定长度、限定必填，把脏数据挡在门外（min_length=1 就是挡"空问题"）。
  · 响应模型要"稳"：它是给前端和未来的 MCP Client 依赖的契约，字段只能增不能改语义。
两个目标不同源，硬塞进一个类，改请求校验就容易连带破坏响应契约。
"""

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
    session_id: str | None = Field(
        default=None,
        max_length=64,
        description="会话 ID；阶段 03 接入数据库后由服务端下发，现在允许客户端自带",
    )


class ChatResponse(BaseModel):
    request_id: str = Field(description="贯穿整条链路的请求标识，排查问题时用它捞日志")
    session_id: str | None = None
    answer: str
    stage: str = Field(
        description="当前处在哪个学习阶段——骨架期刻意暴露出来，便于看出链路走到了哪一步",
    )
    model: str | None = Field(
        default=None,
        description="实际使用的模型名；阶段 01 还没接模型，为 None",
    )
