"""数据库连接与会话管理。

两个决策，都是生产事故的来源，值得单独讲：

1. 连接池参数为什么是这几个数：
   pool_size=10、max_overflow=20 —— 单实例同时活跃的数据库连接不该超过 30。
   PostgreSQL 默认 max_connections=100，你的服务部署 4 个实例就是 120 个连接，
   直接把库打满。**连接池上限是容量规划的一部分**，不是随便填的数。
   pool_recycle=1800：PG 侧的空闲连接会被防火墙/负载均衡器悄悄掐断
   （云环境常见 30~60 分钟），不回收就会出现"连接好好的，一用就断"。
   pool_pre_ping：取连接前先 ping 一下，坏连接当场换掉，而不是把
   "connection reset" 抛给业务代码。

2. 为什么用 get_session() 生成器而不是全局 Session：
   Session 是**有状态**的（身份映射、未提交的事务），并发请求共用一个 Session
   会互相污染。每个请求一个 Session、用完即关，是唯一安全的用法。
   FastAPI 的 Depends + yield 正好提供"请求级生命周期"。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """进程内单例的引擎。测试里会替换掉它（见 conftest）。"""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=10,
            max_overflow=20,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话（FastAPI 依赖）。

    yield 前后包住整个请求：请求里抛异常，rollback 自动发生；
    正常走完，commit 生效。业务代码不需要手写事务边界 ——
    **事务边界是横切关注点**，由装配层统一管理比每个函数自己
    commit/rollback 可靠得多（漏写一个 rollback 就连接泄漏）。
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """进程退出时关掉连接池（lifespan 里调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_engine_for_tests() -> None:
    """测试专用：丢弃当前引擎，让下一次 get_engine() 读新配置。

    为什么不直接操作全局变量绕过这里：测试要切换 database_url
    （比如指向测试库），必须让单例重建。留一个显式入口，
    比让测试去摸模块私有变量干净。
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None
