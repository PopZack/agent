"""依赖装配点。

把所有"构造一个东西"的动作集中在这里：
  · 路由函数只声明"我需要什么"，不关心"怎么造出来的"；
  · 有这层函数壳，测试才能用 dependency_overrides 干净地换上假实现
    （tests/test_chat_api.py 里换的就是 get_llm_client）。

依赖链是一棵单向的树，不允许回头：
    ChatService → IntentService → LLMClient → Settings
调用方永远不知道自己拿到的东西下面还有几层。
"""

from functools import lru_cache

from app.core.config import get_settings
from app.llm.client import LLMClient
from app.services.chat_service import ChatService
from app.services.intent_service import IntentService


@lru_cache
def get_llm_client() -> LLMClient:
    """模型客户端必须进程内复用，不能每次请求新建。

    和 ChatService 不同，它是**有状态**的：内部持有 httpx 连接池。
    每个请求新建一个，就等于每个请求重做一次 DNS 解析和 TCP/TLS 握手 ——
    一次任务里调十几次模型时，这些开销会远超模型本身的计算时间。
    """
    return LLMClient(get_settings())


@lru_cache
def get_intent_service() -> IntentService:
    return IntentService(get_llm_client(), get_settings())


@lru_cache
def get_chat_service() -> ChatService:
    return ChatService(get_intent_service(), get_settings())


async def close_llm_client() -> None:
    """进程退出时主动关掉连接池。

    不关会怎样：连接要等操作系统超时回收（可能几十秒）。表现是——
    容器滚动发布时旧实例迟迟不退、被探测判定为"卡住"；
    测试里则表现为一堆未关闭资源的告警。这就是 JD 说的"资源清理"。
    """
    # cache_info() 只读缓存状态，不会触发构造 —— 从没建过就别为了关它而建一个
    if get_llm_client.cache_info().currsize == 0:
        return
    await get_llm_client().aclose()
    get_llm_client.cache_clear()
