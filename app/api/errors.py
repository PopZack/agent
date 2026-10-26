"""异常 → HTTP 响应的唯一翻译点。

为什么要有这层：业务层抛的是 AppError，客户端收到的是 HTTP 状态码 + 统一错误体，
中间必须有人翻译，而且**只能有一个地方翻译**。
散落在每个路由里的 try/except 会让错误体五花八门，前端没法写统一处理。
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    """统一错误体。前端只认 {error: {code, message, details}} 这一种形状，
    且只靠 code 做分支，永远不靠 message 做字符串匹配。"""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _safe_validation_details(exc: RequestValidationError) -> list[dict]:
    """只挑可 JSON 序列化的字段。

    ⚠️ 这是个真实的坑：pydantic 的 err["ctx"] 里可能塞着 ValueError 之类的对象，
    直接把 exc.errors() 丢进 JSONResponse，序列化时会炸在响应阶段，
    表现为"参数错误却返回 500"，非常难查。主动裁剪就避开了。
    """
    return [
        {
            "loc": list(err.get("loc", [])),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # 业务异常是"预期内的"，用 warning 级别，不打印堆栈
        logger.warning(
            "app_error",
            extra={
                "event": "app_error",
                "error_code": exc.code,
                "path": request.url.path,
                "status_code": exc.status_code,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        detail = {"fields": _safe_validation_details(exc)}
        return JSONResponse(
            status_code=422,
            content=_error_body("invalid_request", "请求参数不合法", detail),
        )

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 预期外的错误：error 级别 + 完整堆栈，进日志；
        # 但**绝不**把堆栈或异常原文返回给客户端（会泄露内部结构，JD 里"日志脱敏"讲的就是这个）
        logger.exception(
            "unhandled_error",
            extra={"event": "unhandled_error", "path": request.url.path, "status_code": 500},
        )
        return JSONResponse(status_code=500, content=_error_body("internal_error", "服务内部错误"))
