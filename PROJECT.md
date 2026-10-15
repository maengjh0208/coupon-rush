# PROJECT.md

## 개요

FastAPI 기반 선착순 쿠폰 발급 시스템. 동시성 제어 방식을 단계적으로 적용하고 부하 테스트로 수치화해 비교한다.

## 스택

- Python, FastAPI (async)
- PostgreSQL, SQLAlchemy 2.0 async (Core 위주, ORM은 테이블 정의 수준) + asyncpg
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

아직 없음. 첫 코드가 생기면 채운다.

## 문서

- `docs/problems.md`: 해결할 문제 목록과 상태
- `docs/specs_plans/`: 해결한 문제별 요구사항 상세·구현 계획·결정·측정 결과
