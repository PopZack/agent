"""依赖装配点。

依赖链是一棵单向的树，不允许回头：

    ChatService → IntentService → LLMClient → Settings
                → ToolRunner   → ToolRegistry → Tool
                → Settings

调用方永远不知道自己拿到的东西下面还有几层。
"""

from functools import lru_cache

from app.core.config import get_settings
from app.llm.client import LLMClient
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService
from app.services.tool_service import ToolRunner
from app.tools.business import build_default_tools
from app.tools.registry import ToolRegistry


@lru_cache
def get_llm_client() -> LLMClient:
    """模型客户端必须进程内复用：它内部持有 httpx 连接池，是有状态的。"""
    return LLMClient(get_settings())


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """注册表也是单例 —— 它就是"这台机器上到底有哪些工具"的唯一事实。

    阶段 06 会在这里把 MCP Server 动态发现的工具也注册进来；
    阶段 07 会按用户权限装配不同的工具集，那时这里会变成 build_for(user)。
    """
    return ToolRegistry(build_default_tools())


@lru_cache
def get_tool_runner() -> ToolRunner:
    return ToolRunner(get_tool_registry(), get_settings())


@lru_cache
def get_intent_service() -> IntentService:
    return IntentService(get_llm_client(), get_settings())


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(
        get_intent_service(),
        get_llm_client(),
        get_tool_registry(),
        get_tool_runner(),
        get_settings(),
    )


async def close_llm_client() -> None:
    """进程退出时主动关掉连接池。

    cache_info() 只读缓存状态、不会触发构造 —— 从没建过就别为了关它而建一个。
    """
    if get_llm_client.cache_info().currsize == 0:
        return
    await get_llm_client().aclose()
    get_llm_client.cache_clear()
