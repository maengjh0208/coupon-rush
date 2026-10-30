import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Annotated

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import insert, select, update, exists
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

from coupon_rush.tables import coupon_issue, coupon


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"])
    yield
    await app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)


class IssueRejected(Exception):
    def __init__(self, code: str):
        self.code = code


class CouponNotFound(Exception):
    pass


@app.exception_handler(IssueRejected)
async def issue_rejected_handler(request: Request, exc: IssueRejected) -> JSONResponse:
    return JSONResponse(status_code=409, content={"code": exc.code})


@app.exception_handler(CouponNotFound)
async def coupon_not_found_handler(request: Request, exc: CouponNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"code": "COUPON_NOT_FOUND"})


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.engine.begin() as conn:
        yield conn


class IssueRequest(BaseModel):
    user_id: int


@app.post(
    "/coupons/{coupon_id}/issue",
    status_code=201,
)
async def issue_coupon(
        coupon_id: int,
        body: IssueRequest,
        conn: Annotated[AsyncConnection, Depends(get_conn)],
):
    # 빠른 거절용 (락 전에 대부분의 중복을 걸러냄). 동시 중복의 최종 방어는 UNIQUE 제약
    already_issued = (
        await conn.execute(
            select(exists().where(coupon_issue.c.coupon_id == coupon_id, coupon_issue.c.user_id == body.user_id))
        )
    ).scalar_one()

    if already_issued:
        raise IssueRejected("ALREADY_ISSUED")

    row = (
        await conn.execute(
            select(coupon.c.issued_quantity, coupon.c.total_quantity).where(coupon.c.id == coupon_id).with_for_update()
        )
    ).one_or_none()

    if row is None:
        raise CouponNotFound()

    issued_quantity, total_quantity = row

    if issued_quantity >= total_quantity:
        raise IssueRejected("SOLD_OUT")

    await conn.execute(
        update(coupon).where(coupon.c.id == coupon_id).values(issued_quantity=issued_quantity + 1)
    )

    try:
        await conn.execute(
            insert(coupon_issue).values(coupon_id=coupon_id, user_id=body.user_id)
        )
    except IntegrityError as e:
        raise IssueRejected("ALREADY_ISSUED") from e
