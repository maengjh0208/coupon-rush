from fastapi import FastAPI

app = FastAPI()


@app.post(
    "/coupons/{coupon_id}/issue",
    status_code=201,
)
async def issue_coupon(coupon_id: int):
    return None
