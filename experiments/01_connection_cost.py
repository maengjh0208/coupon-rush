import asyncio
import os
import time

import asyncpg

DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
N = 100


async def new_connection_every_time():
    start = time.perf_counter()
    for _ in range(N):
        conn = await asyncpg.connect(DSN)  # TCP 연결 + 인증 + 세션 준비
        await conn.fetchval("SELECT 1")
        await conn.close()

    return time.perf_counter() - start


async def reuse_one_connection():
    start = time.perf_counter()
    conn = await asyncpg.connect(DSN)  # 딱 한번 연결
    for _ in range(N):
        await conn.fetchval("SELECT 1")

    await conn.close()
    return time.perf_counter() - start


async def main():
    new = await new_connection_every_time()
    reuse = await reuse_one_connection()

    print(f"매번 새 연결: {new:.3f}s ({new / N * 1000:.2f}ms/회)")
    print(f"연결 재사용: {reuse:.3f}s ({reuse / N * 1000:.2f}ms/회)")
    print(f"차이: {new / reuse:.1f}배")


asyncio.run(main())


# << 테스트 결과 >>
# 매번 새 연결: 0.297s (2.97ms/회)
# 연결 재사용: 0.010s (0.10ms/회)
# 차이: 30.4배
