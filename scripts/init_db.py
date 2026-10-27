"""建表脚本：开发/测试环境用。

为什么不用 Alembic（现在）：学习项目第一张迁移就是"建全部表"，
直接 SQL 更透明。等表结构开始**演进**（阶段 03b/04 要加列）时，
再引入 Alembic —— 那时"迁移"这个概念才真正有教学意义：
迁移存在的意义不是"建表"，而是"在已有数据的生产库上改表"。

用法：
    python scripts/init_db.py            # 建表（已存在则跳过）
    python scripts/init_db.py --drop     # 危险：先删后建（测试重置用）
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.db.models import Base
from app.db.session import dispose_engine, get_engine


async def init(drop: bool) -> None:
    engine = get_engine()
    try:
        if drop:
            # DROP ... CASCADE：表之间有外键，逐个删会报依赖；
            # 测试重置时按依赖级联删最省事
            async with engine.begin() as conn:
                await conn.execute(
                    text("DROP TABLE IF EXISTS chat_messages, chat_sessions, users CASCADE")
                )
            print("已删除全部表")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("表结构已就绪")
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--drop", action="store_true", help="先删除已有表（危险）")
    args = parser.parse_args()
    if "--drop" in sys.argv:
        print("⚠️  --drop 会清空全部数据，仅用于本地/测试重置")
    asyncio.run(init(drop=args.drop))
