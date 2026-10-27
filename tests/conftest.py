import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_service
from app.core.config import get_settings


@pytest.fixture
def anyio_backend():
    """给异步测试指定后端。

    anyio 的 pytest 插件默认要**同时**跑 asyncio 和 trio 两套后端，
    而项目里没有（也不需要）trio，不指定就会以 'trio' 后端报错。
    这跟"用了哪个框架"无关，纯粹是插件默认值的问题。
    """
    return "asyncio"


@pytest.fixture
def app():
    """每个测试拿一个全新的 app。

    这里演示了 get_settings / get_chat_service 都用 lru_cache 的代价：
    缓存必须在测试前清掉，否则测试之间会互相污染（改了 .env 却读不到新值）。
    这是 lru_cache 做单例的通用注意事项。
    """
    get_settings.cache_clear()
    get_chat_service.cache_clear()

    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)
