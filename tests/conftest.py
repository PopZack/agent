import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_service
from app.core.config import get_settings


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
