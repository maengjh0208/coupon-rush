import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from coupon_rush.tables import coupon, coupon_issue


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


# Redis Key
def redis_stock_key(coupon_id: int) -> str:
    return f"coupon:{coupon_id}:stock"


# ERROR 클래스
class AppError(Exception):
    status_code: int
    code: str


class CouponNotFound(AppError):
    status_code = 404
    code = "COUPON_NOT_FOUND"


class CouponSoldOut(AppError):
    status_code = 409
    code = "SOLD_OUT"


class DuplicateCouponIssue(AppError):
    status_code = 409
    code = "ALREADY_ISSUED"


class CouponNotStarted(AppError):
    status_code = 409
    code = "NOT_STARTED"


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code})


# 의존성 정리
async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    async with request.app.state.engine.begin() as conn:
        yield conn


async def get_redis(request: Request) -> aioredis.Redis:
    # get_conn은 요청이 끝나면 트랜잭션 COMMIT/ROLLBACK 정리 작업을 해야해서 yield가 필요하지만
    # Redis 클라이언트는 요청마다 정리할 게 없음. 명령마다 풀에서 연결을 빌리고 바로 반환함.
    # 함수로 감싸는 이유는 테스트코드에서 app.dependency_overrides[get_redis] = ... 로 테스트용을 끼워넣기 위해서임.
    return request.app.state.redis


# Context Manager
@asynccontextmanager
async def reserve_stock(redis: aioredis.Redis, key: str) -> AsyncIterator[None]:
    # 재고 1개 예약. 예외가 발생하면 재고를 되돌림.
    if await redis.decr(key) < 0:
        await redis.incr(key)
        raise CouponSoldOut()

    try:
        yield
    except CouponSoldOut:
        raise
    except BaseException:
        await redis.incr(key)
        raise


# DTO
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
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
):
    # 빠른 거절용 (매진 뒤 요청이 DB를 거치지 않게). 재고의 최종 판단은 DECR
    key = redis_stock_key(coupon_id)
    stock = await redis.get(key)
    if stock is not None and int(stock) <= 0:
        raise CouponSoldOut()

    result = await conn.execute(
        select(
            coupon.c.total_quantity,
            coupon.c.issued_quantity,
            (coupon.c.starts_at <= func.now()).label("started"),
        ).where(coupon.c.id == coupon_id)
    )
    coupon_row = result.one_or_none()
    if coupon_row is None:
        raise CouponNotFound()
    if not coupon_row.started:
        raise CouponNotStarted()

    if stock is None:
        await redis.set(key, coupon_row.total_quantity - coupon_row.issued_quantity, nx=True)

    # 빠른 거절용 (락 전에 대부분의 중복을 걸러냄). 동시 중복의 최종 방어는 UNIQUE 제약
    already_issued = (
        await conn.execute(
            select(exists().where(coupon_issue.c.coupon_id == coupon_id, coupon_issue.c.user_id == body.user_id))
        )
    ).scalar_one()

    if already_issued:
        raise DuplicateCouponIssue()

    # 쿠폰 재고 차감 및 쿠폰 발급
    async with reserve_stock(redis, key):
        # UPDATE를 INSERT보다 먼저: INSERT가 먼저면 FK 검사의 KEY SHARE가 hot row에 몰려 MultiXact 경합
        result = await conn.execute(
            update(coupon)
            .where(coupon.c.id == coupon_id, coupon.c.total_quantity > coupon.c.issued_quantity)
            .values(issued_quantity=coupon.c.issued_quantity + 1)
        )
        if result.rowcount == 0:
            raise CouponSoldOut()

        try:
            await conn.execute(insert(coupon_issue).values(coupon_id=coupon_id, user_id=body.user_id))
        except IntegrityError as e:
            raise DuplicateCouponIssue() from e
