import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine
import redis.asyncio as aioredis

from coupon_rush.main import redis_stock_key
from coupon_rush.tables import metadata, coupon

TOTAL_QUANTITY = int(sys.argv[1]) if len(sys.argv) > 1 else 100


async def main():
    # DB setting
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        result = await conn.execute(
            insert(coupon)
            .values(name="선착순 쿠폰", total_quantity=TOTAL_QUANTITY, starts_at=datetime.now(UTC))
            .returning(coupon.c.id)
        )
        coupon_id = result.scalar_one()
    await engine.dispose()

    # Redis setting
    redis = aioredis.from_url(os.environ["REDIS_URL"])
    await redis.set(redis_stock_key(coupon_id), TOTAL_QUANTITY)
    await redis.aclose()


asyncio.run(main())
