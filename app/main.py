"""应用入口。

启动：
    .venv/Scripts/python -m uvicorn app.main:app --reload
  或（Windows 下更稳，避免中文日志乱码）
    set PYTHONIOENCODING=utf-8 && .venv/Scripts/python -m uvicorn app.main:app --reload

接口文档：http://127.0.0.1:8000/docs
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import close_llm_client
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routers import chat, health, sessions
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import dispose_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭钩子。

    为什么 app_started 要挪到这里、而不是留在 create_app 末尾：
    create_app() 只是"把应用拼装出来"，测试里会被反复调用；
    真正"开始服务"是 lifespan 的 startup 阶段。
    日志写在拼装点，就会出现"测试跑一次就打一行 app_started"的噪音。
    """
    logger.info("app_started", extra={"event": "app_started"})
    yield
    await close_llm_client()
    await dispose_engine()  # 连接池也要关，否则容器退出时挂起
    logger.info("app_stopped", extra={"event": "app_stopped"})


def create_app() -> FastAPI:
    """工厂函数，而不是模块级直接建一个 app。

    区别很实际：
      · 测试里可以反复 create_app()，每个测试拿到干净实例、互不干扰；
      · import 这个模块时不会产生"建 app"的副作用（比如立刻去连数据库）。
    """
    settings = get_settings()
    setup_logging(level=settings.log_level, as_json=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="企业智能Agent助手 —— 阶段 02b-1：意图识别与结构化输出",
        lifespan=_lifespan,
    )

    # 顺序说明：add_middleware 是"后加的在更外层"，这里只有一个中间件，不涉及顺序问题。
    # 阶段 07 加权限、沙箱中间件时，顺序会变得敏感，到时候再细讲。
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)

    return app


app = create_app()
