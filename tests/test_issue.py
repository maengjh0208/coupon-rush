import asyncio

import pytest

from sqlalchemy import select, func

from coupon_rush.tables import coupon_issue, coupon

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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="락 없음: 동시 요청 시 초과 발급 (문제 4에서 해결 예정)")
async def test_concurrent_requests_do_not_exceed_quantity(client, test_engine, create_coupon):
    total_quantity = 10
    coupon_id = await create_coupon(total_quantity=total_quantity)

    await asyncio.gather(*(
        client.post(f"/coupons/{coupon_id}/issue", json={"user_id": user_id}) for user_id in range(1, 101)
    ))

    async with test_engine.connect() as conn:
        issued_rows = (
            await conn.execute(select(func.count()).select_from(coupon_issue))
        ).scalar_one()

    assert issued_rows == total_quantity


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="락 없음: 읽은 값으로 덮어써서 갱신 손실 (문제 4에서 해결 예정)")
async def test_concurrent_requests_keep_issued_quantity_in_sync(client, test_engine, create_coupon):
    coupon_id = await create_coupon(total_quantity=1000)

    user_count = 100
    await asyncio.gather(*(
        client.post(f"/coupons/{coupon_id}/issue", json={"user_id": user_id}) for user_id in range(1, user_count + 1)
    ))

    async with test_engine.connect() as conn:
        issued_quantity = (
            await conn.execute(select(coupon.c.issued_quantity).where(coupon.c.id == coupon_id))
        ).scalar_one()

    assert issued_quantity == user_count


async def test_returns_already_issued_when_user_requests_twice(client, coupon_id):
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})

    assert response.status_code == 409
    assert response.json() == {"code": "ALREADY_ISSUED"}


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="락 없음: 동시 요청 시 같은 사용자 중복 발급 (문제 5에서 해결 예정)")
async def test_concurrent_requests_from_same_user_issue_only_once(client, test_engine, coupon_id):
    await asyncio.gather(*(
        client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1}) for _ in range(10)
    ))

    async with test_engine.connect() as conn:
        issued_rows = (
            await conn.execute(select(func.count()).select_from(coupon_issue).where(coupon_issue.c.user_id == 1,
                                                                                    coupon_issue.c.coupon_id == coupon_id))
        ).scalar_one()

    assert issued_rows == 1
