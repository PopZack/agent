"""应用配置。

为什么不用 os.getenv 到处读：
  1. 类型校验：DEBUG 这种字段会在启动时被转成 bool，写错了启动就报错（fail fast），
     而不是运行到一半才发现 "false" 被当成真值。
  2. 集中声明：想知道这个服务有哪些配置、默认值是什么，看这一个文件就够。
  3. .env 自动加载：本地开发不用手工 export。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env 里有别的项目残留变量也不至于启动失败
    )

    app_name: str = "Enterprise Agent"
    app_version: str = "0.1.0"

    log_level: str = "INFO"
    log_json: bool = False

    # 阶段 02 才会真正调用，这里先声明，保证 .env.example 与代码字段一一对应
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""


@lru_cache
def get_settings() -> Settings:
    """进程内只构造一次，同时让测试可以用 get_settings.cache_clear() 重置。"""
    return Settings()
