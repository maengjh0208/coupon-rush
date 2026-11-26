import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from coupon_rush.main import redis_stock_key
from coupon_rush.tables import coupon_issue

pytestmark = pytest.mark.anyio


async def test_issue_coupon_returns_201(client, coupon_id):
    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})
    assert response.status_code == 201


async def test_issue_creates_issue_row(client, test_engine, coupon_id):
    # 실행
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 7})

    # 검증: 어떤 쿠폰이 누구에게 발급됐는가
    async with test_engine.connect() as conn:
        rows = (await conn.execute(select(coupon_issue.c.coupon_id, coupon_issue.c.user_id))).all()
    assert rows == [(coupon_id, 7)]


async def test_returns_sold_out_when_quantity_exhausted(client, create_coupon):
    coupon_id = await create_coupon(total_quantity=1)
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 2})

    assert response.status_code == 409
    assert response.json() == {"code": "SOLD_OUT"}


async def test_concurrent_requests_do_not_exceed_quantity(client, test_engine, create_coupon):
    total_quantity = 10
    coupon_id = await create_coupon(total_quantity=total_quantity)

    await asyncio.gather(
        *(client.post(f"/coupons/{coupon_id}/issue", json={"user_id": user_id}) for user_id in range(1, 101))
    )

    async with test_engine.connect() as conn:
        issued_rows = (await conn.execute(select(func.count()).select_from(coupon_issue))).scalar_one()

    assert issued_rows == total_quantity


async def test_returns_already_issued_when_user_requests_twice(client, coupon_id):
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    assert response.status_code == 409
    assert response.json() == {"code": "ALREADY_ISSUED"}


async def test_concurrent_requests_from_same_user_issue_only_once(client, test_engine, coupon_id):
    # 같은 user 동시에 10번 요청
    await asyncio.gather(*(client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1}) for _ in range(10)))

    async with test_engine.connect() as conn:
        issued_rows = (
            await conn.execute(
                select(func.count())
                .select_from(coupon_issue)
                .where(coupon_issue.c.user_id == 1, coupon_issue.c.coupon_id == coupon_id)
            )
        ).scalar_one()

    assert issued_rows == 1


async def test_returns_not_found_when_coupon_does_not_exist(client):
    response = await client.post("/coupons/999/issue", json={"user_id": 1})

    assert response.status_code == 404
    assert response.json() == {"code": "COUPON_NOT_FOUND"}


async def test_returns_sold_out_when_redis_stock_is_zero(client, redis, create_coupon):
    coupon_id = await create_coupon(total_quantity=10)
    await redis.set(redis_stock_key(coupon_id), 0)

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    assert response.status_code == 409
    assert response.json() == {"code": "SOLD_OUT"}


async def test_returns_sold_out_when_redis_stock_is_stale(client, redis, create_coupon):
    coupon_id = await create_coupon(total_quantity=1)
    first = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})
    assert first.status_code == 201

    key = redis_stock_key(coupon_id)
    await redis.set(key, 1)  # 오래된 스냅샷 복구 흉내

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 2})
    assert response.status_code == 409
    assert response.json() == {"code": "SOLD_OUT"}
    assert int(await redis.get(key)) == 0


async def test_returns_not_started_before_starts_at(client, create_coupon):
    coupon_id = await create_coupon(starts_at=datetime.now(UTC) + timedelta(hours=1))
    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})
    assert response.status_code == 409
    assert response.json() == {"code": "NOT_STARTED"}


async def test_returns_sold_out_when_stock_exhausted_even_if_already_issued(client, create_coupon):
    coupon_id = await create_coupon(total_quantity=1)
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})  # Redis 재고 1 → 0

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})
    assert response.json() == {"code": "SOLD_OUT"}
