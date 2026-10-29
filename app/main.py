"""AegisFlow 应用入口（阶段 0：项目骨架）。

阶段 0 只提供可运行的最小骨架：一个健康检查接口 + 当前运行模式回显，
用于验证"本地 Python 环境可运行"。真正的业务 API（事件 CRUD 等）在阶段 1 建立。
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.infrastructure.settings import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="AegisFlow —— 企业级 AI 事件调查与受控处置平台",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """健康检查：确认服务可用，并如实回显运行模式（mock / real）。"""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": __version__,
        "environment": settings.environment,
        "run_mode": settings.run_mode,
    }
