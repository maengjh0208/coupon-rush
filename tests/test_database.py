import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.anyio
async def test_connects_to_test_database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1

    await engine.dispose()
