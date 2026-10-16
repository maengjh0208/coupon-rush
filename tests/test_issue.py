import pytest
from httpx import ASGITransport, AsyncClient

from coupon_rush.main import app


@pytest.mark.anyio
async def test_issue_coupon_returns_201():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/coupons/1/issue", json={"user_id": 1})

    assert response.status_code == 201
