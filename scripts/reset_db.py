import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from coupon_rush.tables import metadata, coupon

TOTAL_QUANTITY = int(sys.argv[1]) if len(sys.argv) > 1 else 100


async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
        await conn.execute(
            insert(coupon).values(name="선착순 쿠폰", total_quantity=TOTAL_QUANTITY, starts_at=datetime.now(UTC))
        )

    await engine.dispose()


asyncio.run(main())
