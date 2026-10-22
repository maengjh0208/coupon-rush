import os
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, insert
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from coupon_rush.main import app, get_conn
from coupon_rush.tables import metadata, coupon


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    url = os.environ["TEST_DATABASE_URL"]
    assert make_url(url).database.endswith("_test"), f"테스트 DB가 아님 {url}"

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def client(test_engine):
    async with test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE coupon_issue, coupon RESTART IDENTITY CASCADE"))

    async def override_get_conn():
        async with test_engine.begin() as conn:
            yield conn

    app.dependency_overrides[get_conn] = override_get_conn
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# client의 TRUNCATE 이후에 INSERT 되도록 client에 의존
@pytest.fixture
async def coupon_id(client, test_engine):
    async with test_engine.begin() as conn:
        result = await conn.execute(
            insert(coupon)
            .values(name="선착순 쿠폰", total_quantity=100, starts_at=datetime.now(UTC))
            .returning(coupon.c.id)
        )

    return result.scalar_one()

