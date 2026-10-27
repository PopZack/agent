"""业务异常体系。

为什么要有它：如果 service 层直接 raise HTTPException，就等于把"HTTP 状态码"
这个 Web 概念钉进了业务层 —— 以后换个调用入口（CLI、定时任务、消息队列），
业务层就没法复用了。

所以这里定义的是**与协议无关**的异常，每种异常自带三样东西：
  · code       给机器/前端用的稳定错误码（不要用 message 做判断）
  · status_code 出 HTTP 时才用它翻译成状态码，这个翻译在 api 层做
  · message    给人看的中文说明

注意命名：故意**不叫** ValidationError —— pydantic 已经占了这个名字，
同名会让读代码的人分不清是"参数校验失败"还是"业务校验失败"。
"""

from typing import Any


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    message: str = "服务内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class InvalidRequestError(AppError):
    """入参在业务上不合法（Pydantic 挡住的是格式，这里挡的是语义）。"""

    code = "invalid_request"
    status_code = 400
    message = "请求参数不合法"


class UnauthenticatedError(AppError):
    """带了 session_id（要写入会话）却没有身份标识。"""

    code = "unauthenticated"
    status_code = 401
    message = "缺少身份标识"


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    message = "资源不存在"


class UpstreamError(AppError):
    """下游依赖（模型 API、业务系统）出问题，不是我们的错，但要如实告诉调用方。"""

    code = "upstream_error"
    status_code = 502
    message = "上游服务异常"
