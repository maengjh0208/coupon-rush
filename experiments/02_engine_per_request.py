import asyncio
import os
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ["DATABASE_URL"]


# 안좋은 예: 요청마다 엔진(=풀)을 새로 만든다.
async def handle_request_bad():
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_sleep(2)"))  # 2초 걸리는 쿼리라고 가정
    await engine.dispose()


# 좋은 예: 엔진 하나를 모든 요청이 공유한다.
shared_engine = create_async_engine(URL, pool_size=5, max_overflow=0)


async def handle_request_good():
    async with shared_engine.connect() as conn:
        await conn.execute(text("SELECT pg_sleep(2)"))


async def main():
    mode, concurrency = sys.argv[1], int(sys.argv[2])
    handler = handle_request_bad if mode == "bad" else handle_request_good

    start = time.perf_counter()
    results = await asyncio.gather(
        *(handler() for _ in range(concurrency)), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, Exception)]
    print(f"[{mode}] 동시 {concurrency}개, {time.perf_counter() - start:.1f}s, 실패 {len(errors)}개")
    if errors:
        print("첫 에러: ", repr(errors[0]))


asyncio.run(main())
