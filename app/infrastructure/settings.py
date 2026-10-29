"""集中配置。

所有环境变量从 .env 读取；真实密钥只存在于本地 .env，不写进代码、不进仓库。
阶段 0 只定义最小配置面，后续阶段按需扩展（数据库、模型、预算等字段已预留）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录：app/infrastructure/settings.py -> parents[2]
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 应用 ──
    app_name: str = "AegisFlow"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "ci"] = "local"

    # ── 日志 ──
    log_level: str = "INFO"
    log_json: bool = False

    # ── 运行模式：real=真实模型，mock=确定性模拟 ──
    run_mode: Literal["real", "mock"] = "mock"

    # ── 数据库（MySQL）──
    database_url: str = "mysql+aiomysql://aegis:aegis@127.0.0.1:3306/aegisflow"
    database_echo: bool = False

    # ── 模型 ──
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 3

    # ── Harness 预算与超时 ──
    max_model_calls: int = 20
    max_tool_calls: int = 30
    max_investigation_rounds: int = 5
    tool_timeout_seconds: int = 10
    max_run_seconds: int = 300
    max_model_cost_usd: float = 0.5

    # ── 数据目录 ──
    data_dir: Path = BASE_DIR / "data"

    @property
    def seed_dir(self) -> Path:
        return self.data_dir / "seed"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def evals_dir(self) -> Path:
        return self.data_dir / "evals"


@lru_cache
def get_settings() -> Settings:
    """返回进程内单例配置（被缓存，便于测试用 get_settings.cache_clear() 重置）。"""
    return Settings()
