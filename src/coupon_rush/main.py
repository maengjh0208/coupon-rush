import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Annotated

from fastapi import FastAPI, Request, Depends
from pydantic import BaseModel
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection

from coupon_rush.tables import coupon_issue


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
    await conn.execute(
        insert(coupon_issue).values(coupon_id=coupon_id, user_id=body.user_id)
    )
