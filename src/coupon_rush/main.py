import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Annotated

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import insert, select, update, exists
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

from coupon_rush.tables import coupon_issue, coupon


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"])
    yield
    await app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)


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
    already_issued = (
        await conn.execute(
            select(exists().where(coupon_issue.c.coupon_id == coupon_id, coupon_issue.c.user_id == body.user_id))
        )
    ).scalar_one()

    if already_issued:
        return JSONResponse(status_code=409, content={"code": "ALREADY_ISSUED"})

    issued_quantity, total_quantity = (
        await conn.execute(
            select(coupon.c.issued_quantity, coupon.c.total_quantity).where(coupon.c.id == coupon_id).with_for_update()
        )
    ).one()

    if issued_quantity >= total_quantity:
        return JSONResponse(status_code=409, content={"code": "SOLD_OUT"})

    await conn.execute(
        update(coupon).where(coupon.c.id == coupon_id).values(issued_quantity=issued_quantity + 1)
    )

    await conn.execute(
        insert(coupon_issue).values(coupon_id=coupon_id, user_id=body.user_id)
    )
