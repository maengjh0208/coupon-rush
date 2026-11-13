import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Annotated

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import insert, select, update, exists
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncConnection
import redis.asyncio as aioredis

from coupon_rush.tables import coupon_issue, coupon


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = create_async_engine(os.environ["DATABASE_URL"])
    app.state.redis = aioredis.from_url(os.environ["REDIS_URL"])
    await app.state.redis.ping()  # fail fast: from_url은 연결을 미리 맺지 않음

    yield

    # 종료 순서는 만든 순서의 역순으로 정리하는걸 습관화.
    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(lifespan=lifespan)


# Redis Key 정리
def redis_stock_key(coupon_id: int) -> str:
    return f"coupon:{coupon_id}:stock"


# ERROR 클래스 정리
class IssueRejected(Exception):
    def __init__(self, code: str):
        self.code = code


class CouponNotFound(Exception):
    pass


# Exception Handler 정리
@app.exception_handler(IssueRejected)
async def issue_rejected_handler(request: Request, exc: IssueRejected) -> JSONResponse:
    return JSONResponse(status_code=409, content={"code": exc.code})


@app.exception_handler(CouponNotFound)
async def coupon_not_found_handler(request: Request, exc: CouponNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"code": "COUPON_NOT_FOUND"})


# 의존성 정리
async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.engine.begin() as conn:
        yield conn


async def get_redis(request: Request) -> aioredis.Redis:
    # get_conn은 요청이 끝나면 트랜잭션 COMMIT/ROLLBACK 정리 작업을 해야해서 yield가 필요하지만
    # Redis 클라이언트는 요청마다 정리할 게 없음. 명령마다 풀에서 연결을 빌리고 바로 반환함.
    # 함수로 감싸는 이유는 테스트코드에서 app.dependency_overrides[get_redis] = ... 로 테스트용을 끼워넣기 위해서임.
    return request.app.state.redis


# DTO 정리
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

    result = await conn.execute(
        update(coupon)
        .where(coupon.c.id == coupon_id, coupon.c.total_quantity > coupon.c.issued_quantity)
        .values(issued_quantity=coupon.c.issued_quantity + 1)
    )
    if result.rowcount == 0:
        exists_coupon = (await conn.execute(select(exists().where(coupon.c.id == coupon_id)))).scalar_one()
        if exists_coupon:
            raise IssueRejected("SOLD_OUT")
        else:
            raise CouponNotFound()

    try:
        await conn.execute(
            insert(coupon_issue).values(coupon_id=coupon_id, user_id=body.user_id)
        )
    except IntegrityError as e:
        raise IssueRejected("ALREADY_ISSUED") from e
