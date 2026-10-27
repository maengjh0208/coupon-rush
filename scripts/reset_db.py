import asyncio
import os
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from coupon_rush.tables import metadata, coupon


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(coupon).values(name="선착순 쿠폰", total_quantity=100, starts_at=datetime.now(UTC))
        )

    await engine.dispose()


asyncio.run(main())
