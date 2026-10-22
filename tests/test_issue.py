import pytest

from sqlalchemy import select

from coupon_rush.tables import coupon_issue


@pytest.mark.anyio
async def test_issue_coupon_returns_201(client, coupon_id):
    response = await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 1})
    assert response.status_code == 201


@pytest.mark.anyio
async def test_issue_creates_issue_row(client, test_engine, coupon_id):
    # 실행
    await client.post(f"/coupons/{coupon_id}/issue", json={"user_id": 7})

    # 검증: 어떤 쿠폰이 누구에게 발급됐는가
    async with test_engine.connect() as conn:
        rows = (await conn.execute(select(coupon_issue.c.coupon_id, coupon_issue.c.user_id))).all()
    assert rows == [(coupon_id, 7)]
