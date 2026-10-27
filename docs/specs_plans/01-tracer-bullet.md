# 01. 트레이서 불릿: FastAPI → PostgreSQL 최소 경로

## 문제

요청이 FastAPI에서 PostgreSQL까지 관통하는 경로가 없었다. 동시성 문제를 재현하려면 먼저 실제로 실행되는 얇은 경로가 필요하다.

## 요구사항 (이번 범위)

- `POST /coupons/{coupon_id}/issue` 바디 `{user_id}` 요청 시 `coupon_issue`에 row 1개를 저장하고 201을 반환한다.
- 재고 확인, `issued_quantity` 증가, 시작 시각·중복 검사, 404/409는 범위 밖이다. 재고 로직은 문제 2에서 락 없이 작성해 경쟁 상태를 재현한다.

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 개발 환경 | Docker Compose(app + postgres), uv, venv는 `/opt/venv` | bind mount(`.:/app`)가 이미지 안의 venv를 가리지 않게 하기 위해 |
| 테스트 DB | 같은 Postgres 서버, 별도 DB `coupon_rush_test` (initdb 스크립트로 생성) | 실제 DB 동작(락, 제약)을 그대로 검증해야 하므로 SQLite·mock 대신 실제 Postgres |
| 스키마 생성 | `metadata.drop_all` + `create_all` | 스키마가 자주 바뀌는 단계. `create_all`은 기존 테이블을 건너뛰므로 drop을 먼저 한다. Alembic은 운영 데이터를 보존해야 할 때 도입 |
| 엔진·커넥션 주입 | lifespan에서 엔진 1개 생성, `Depends(get_conn)`로 요청당 `engine.begin()` 트랜잭션 | 요청마다 엔진을 만들면 커넥션이 풀 없이 늘어나 DB 한도를 넘는다 (아래 실험). 트랜잭션 경계 = 요청 1개 |
| 테스트 격리 | 테스트마다 `TRUNCATE ... RESTART IDENTITY CASCADE` | 트랜잭션 롤백 방식은 모든 요청이 한 커넥션을 공유해 동시성 재현·검증이 불가능하다. 문제 2부터 동시 요청 테스트가 필요하다 |
| 테스트 DB 보호 | URL의 DB 이름이 `_test`로 끝나지 않으면 assert 실패 | `drop_all`·`TRUNCATE`가 개발·운영 DB에 실행되는 사고 방지 |
| 개발 DB 준비 | `scripts/reset_db.py` (drop/create + 쿠폰 1개 시드, 트랜잭션 하나) | 측정마다 깨끗한 상태가 필요하다. Postgres는 DDL도 트랜잭션이라 실패 시 반쪽 상태가 남지 않는다 |
| 쿼리 작성 | SQLAlchemy Core (`Table`, `insert()`, `select()`) | 실행되는 SQL을 예측하기 쉽다. 락 방식 비교에 필요 |

## 실험: 커넥션 비용과 풀 (`experiments/`)

조건: 로컬 Docker, Postgres `max_connections=100`, 쿼리 `pg_sleep(2)`, 1회 실행. 상대 비교로만 해석한다.

- 커넥션 생성은 `SELECT 1`의 약 30배 비용 (0.297s vs 0.010s).
- 요청마다 엔진: 동시 50개는 커넥션 50개로 약 2s. 동시 200개는 99개에서 막혀 101개 실패 (`too many clients already`).
- 공유 엔진 `pool_size=5, max_overflow=0`: 동시 50개는 커넥션 5개로 약 20s. 동시 200개는 125개 실패 (`QueuePool limit ... timeout 30.00`). 30s 동안 처리 가능한 양은 5 × 30/2 = 75개.
- 결론: 풀은 DB를 보호하는 대신 대기를 만든다. 처리량 = 커넥션 수 ÷ 쿼리 시간. `pool_timeout` 기본 30s는 웹 요청에 길다 (문제 3에서 조건으로 고정).

## 결과

- 테스트 2개 통과: 201 반환, `coupon_issue` row 저장.
- 실제 서버(uvicorn)에 `curl` 요청 시 201, 개발 DB에 row 저장 확인.

## 알게 된 함정

- `ASGITransport`는 lifespan을 실행하지 않는다. 테스트는 `get_conn`을 override하므로 lifespan 경로는 실제 서버 실행으로만 검증된다.
- `conn.run_sync`는 함수를 받는다. SQL 실행은 `conn.execute`.
- 의존성 변경 후에는 `docker compose up --build`. venv는 bind mount 밖이라 코드 변경만 즉시 반영된다.
