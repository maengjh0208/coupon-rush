# PROJECT.md

## 개요

FastAPI 기반 선착순 쿠폰 발급 시스템. 동시성 제어 방식을 단계적으로 적용하고 부하 테스트로 수치화해 비교한다.

## 스택

- FastAPI (async), uvicorn
- Python 3.14, uv
- PostgreSQL 18, SQLAlchemy 2.1 async Core (테이블도 Core `Table`로 정의) + asyncpg
- Redis (3단계부터)
- Docker Compose: 컨테이너별 `cpus`, `mem_limit`로 자원 고정
- k6: 호스트에서 실행

## 도메인 규칙

- 쿠폰마다 총 재고(`total_quantity`)와 시작 시각(`starts_at`)이 있다.
- 시작 시각 이전에는 발급하지 않는다.
- 한 사용자는 쿠폰 하나당 한 장만 받을 수 있다.
- 사용자 인증은 하지 않는다. `user_id`는 요청 바디로 받는다.

## API

| 메서드 | 경로 | 응답 |
|---|---|---|
| POST | `/coupons/{id}/issue` 바디 `{user_id}` | 201 성공 / 404 쿠폰 없음 / 409 + `code`(`NOT_STARTED`, `SOLD_OUT`, `ALREADY_ISSUED`) |
| GET | `/users/{user_id}/issues` | 사용자의 발급 내역 |

쿠폰 생성 API는 두지 않는다. 측정마다 초기화·시드 스크립트로 데이터를 준비한다.

## 데이터 모델

```
coupon(id, name, total_quantity, issued_quantity, starts_at)
coupon_issue(id, coupon_id, user_id, issued_at)
```

1단계에서는 `coupon_issue(coupon_id, user_id)` UNIQUE 제약을 일부러 두지 않는다 (중복 발급 재현용).

## 동시성 방식 전환

API는 하나로 두고, 서버 기동 시 환경변수(`ISSUE_STRATEGY`)로 발급 구현체를 고른다. 측정 조건과 k6 스크립트를 모든 방식이 공유하기 위함이다. 구현체가 두 개가 되는 시점에 도입한다.

## 프로젝트 구조 · 빌드 · 실행

```
src/coupon_rush/main.py     FastAPI 앱, lifespan(엔진), get_conn, 엔드포인트
src/coupon_rush/tables.py   SQLAlchemy Core 테이블 정의 (metadata)
tests/conftest.py           테스트 엔진(drop/create_all), client(TRUNCATE + get_conn override), coupon_id seed
scripts/reset_db.py         개발 DB 초기화 + 쿠폰 1개 시드
experiments/                개념 확인용 일회성 실험 스크립트
docker/postgres/init/       최초 볼륨 생성 시 실행되는 SQL (테스트 DB 생성)
```

모든 명령은 컨테이너 안에서 실행한다. venv는 이미지의 `/opt/venv`에 있다.

```bash
docker compose up -d --build                         # 의존성(pyproject/uv.lock) 변경 시 --build 필수
docker compose exec app python scripts/reset_db.py   # 개발 DB 초기화·시드 (데이터 전부 삭제)
docker compose run --rm app pytest                   # 테스트 (coupon_rush_test DB 사용)
curl -i -X POST localhost:8000/coupons/1/issue -H 'Content-Type: application/json' -d '{"user_id": 7}'
```

- 앱은 `uvicorn --reload`로 뜬다. 부하 측정 시에는 `--reload`를 뺀다.
- `docker/postgres/init/`은 빈 볼륨에서만 실행된다. 바꿨으면 `docker compose down -v`.

## 문서

- `docs/problems.md`: 해결할 문제 목록과 상태
- `docs/specs_plans/`: 해결한 문제별 요구사항 상세·구현 계획·결정·측정 결과
