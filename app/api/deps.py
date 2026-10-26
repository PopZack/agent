"""依赖装配点。

把所有"构造一个东西"的动作集中在这里，好处有两个：
  · 路由函数只声明"我需要什么"，不关心"怎么造出来的"；
  · 阶段 03 换成"从连接池取数据库会话"、阶段 07 换成"注入 Harness 运行时"时，
    改动只发生在这一层，路由不用动。
"""

from functools import lru_cache

from app.services.chat_service import ChatService


@lru_cache
def get_chat_service() -> ChatService:
    """无状态服务，进程内单例即可。

    之所以要包一层函数而不是直接在路由里 ChatService()：
    FastAPI 的 dependency_overrides 是按**函数对象**覆盖的，
    有这一层，测试里就能干净地换上假实现。（tests/test_chat_api.py 里有例子）
    """
    return ChatService()
