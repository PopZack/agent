"""请求上下文中间件：整条链路的"起点"。

它做三件事，顺序不能乱：
  1. 拿/生成 request_id，塞进 ContextVar
  2. 放行到下游（路由 → service → …）
  3. 回来后补上响应头 X-Request-ID，并打一条访问日志

第 1 步必须在放行之前，否则下游打的日志里 request_id 全是默认值 "-"。
"""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.context import request_id_var

logger = logging.getLogger("app.access")


def _log_access(request: Request, status_code: int, start: float, *, failed: bool = False) -> None:
    payload = {
        "event": "http_request" if not failed else "http_request_failed",
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round((time.perf_counter() - start) * 1000, 1),
    }
    # 故意不 logger.exception：未捕获异常由 errors.py 的兜底 handler 负责记录堆栈，
    # 这里只记访问行。否则同一个错误会打两遍堆栈，把日志淹掉。
    if failed:
        logger.warning("http_request_failed", extra=payload)
    else:
        logger.info("http_request", extra=payload)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 上游（网关/前端）已经带了就沿用，这样跨服务也能串成同一条 trace
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming or uuid.uuid4().hex[:12]

        token = request_id_var.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            _log_access(request, 500, start, failed=True)
            # 注意：这里**故意不 reset** token。
            # 异常处理中间件（ServerErrorMiddleware）在我们**外层**，
            # 若此刻复位，外层兜底 handler 记的日志就会丢掉 request_id。
            # 不复位是安全的：每个请求跑在独立的 asyncio Task 里，
            # ContextVar 的副本随 Task 结束一起销毁，不会泄漏给下一个请求。
            raise

        response.headers["X-Request-ID"] = request_id
        _log_access(request, response.status_code, start)
        request_id_var.reset(token)  # 正常路径复位，恢复中间件进入前的上下文
        return response
