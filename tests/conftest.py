import os

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_chat_service, get_intent_service, get_llm_client
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
    # lru_cache 做单例的代价：测试之间必须显式清缓存，否则上一个测试
    # 建出来的真客户端会被下一个测试复用，"我明明覆盖了依赖却没生效"就是这么来的
    get_settings.cache_clear()
    get_llm_client.cache_clear()
    get_intent_service.cache_clear()
    get_chat_service.cache_clear()

    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


# ── 阶段 03a：真 PostgreSQL 测试夹具 ──────────────────────
# 放在 conftest 而不是某个测试文件里：pytest 只把 conftest 的夹具
# 暴露给全部测试。放在 test_db.py 里的话，test_repository.py 就找不到它。

TEST_DB_URL = os.environ.get(
    "AGENT_TEST_DATABASE_URL",
    "postgresql+asyncpg://agent:agent@127.0.0.1:5432/agent_test",
)


async def _pg_available(url: str) -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    try:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_ready() -> str:
    """确认测试库可用，返回 URL；不可用则整组 skip（而不是 fail）。"""
    import asyncio

    if not asyncio.run(_pg_available(TEST_DB_URL)):
        pytest.skip(
            f"测试库不可用（{TEST_DB_URL}）—— 需要先 docker compose up -d 并建 agent_test 库"
        )
    return TEST_DB_URL


@pytest.fixture
async def db(pg_ready):
    """每个测试一张干净的表：建表 → 测试 → 删表。

    不删数据、直接删表：测试之间零残留，也不用小心翼翼清每张表。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base

    engine = create_async_engine(pg_ready, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
