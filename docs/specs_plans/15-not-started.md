# 15. 시작 전 발급: 409 NOT_STARTED

## 문제

코드가 `starts_at`을 읽지 않아서 시작 시각 전에도 201로 발급됐다. 선착순 쿠폰에서 오픈 시각은 공정성 그 자체다. 시작 전에 API를 직접 부르는 사람이 재고를 가져가면, 오픈 시각에 맞춰 온 사람은 전부 SOLD_OUT을 받는다.

## 요구사항

- `now() < starts_at`이면 409 `{"code": "NOT_STARTED"}`.
- `starts_at == now()`이면 시작한 것으로 본다.
- 없는 쿠폰은 지금처럼 404.

## 선택지

| | 방식 | 판단 |
|---|---|---|
| A | 존재 확인 조회를 `SELECT starts_at <= now()`로 바꾸고, DECR 전에 거절 | **선택**. 왕복이 늘지 않고, 404 확인과 시작 확인을 한 쿼리에서 한다. Redis를 건드리기 전에 거절하니 보상이 필요 없다 |
| B | 조건부 UPDATE의 WHERE에 `starts_at <= now()` 추가 | `rowcount == 0`이 매진인지 시작 전인지 구분이 안 된다. DECR 뒤라 보상 INCR도 필요하다. 시작 시각은 발급 도중에 바뀌지 않아 원자성이 필요 없다 |
| C | `starts_at`을 Redis에 캐시 | DB 왕복이 병목이 아니다 (YAGNI). 동기화할 값만 늘어난다 |

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 현재 시각 | DB `now()`, 비교도 SQL 안에서 | app 인스턴스가 늘어나도 시계 하나로 판단한다. app 컨테이너의 TZ·시계 설정에 영향받지 않는다 |
| 경계값 | `starts_at <= now()` | "10시 오픈"이면 10:00:00.000부터 받는다 |
| 결과 해석 | `scalar_one_or_none()` → `None` 404, falsy 409 | 바깥 쿼리에 `FROM coupon`이 있어서 행이 없을 수 있다. `None`을 먼저 거르고, 나머지 falsy는 `not started`로 처리한다 |
| `is False` | 쓰지 않음 | 동일성 비교라 드라이버가 `False` 싱글턴이 아닌 falsy 값(예: `0`)을 주면 분기를 건너뛰고 **발급되는 쪽**으로 실패한다. `not`은 막는 쪽으로 실패한다 |
| 시간 저장 | 그대로 (`timestamptz`, aware datetime) | 국내 전용이어도 바꾸지 않는다. `timestamptz`는 시간대가 아니라 절대 시점을 저장한다. KST는 입력·표시 경계에서만 쓴다 (아래 참고) |

## 진행

1. fixture: `create_coupon(starts_at=None)` → 함수 안에서 `starts_at or datetime.now(UTC)`. 기본값에 `datetime.now(UTC)`를 직접 쓰면 정의할 때 한 번만 계산된다.
2. red: `starts_at`을 1시간 뒤로 둔 쿠폰 → `201 == 409`. 짧은 간격(+1초)은 느린 실행에서 flaky하고, app 시계(테스트)와 DB 시계(비교)의 차이도 흡수해야 해서 여유를 크게 줬다.
3. 처음 시도: `SELECT EXISTS(... id = :id AND starts_at <= now())`. 바깥 SELECT에 FROM이 없어서 항상 1행 true/false다. "없음"과 "시작 전"이 둘 다 false가 되어 세 상태를 구분할 수 없다.
4. green: `select(coupon.c.starts_at <= func.now()).where(coupon.c.id == coupon_id)`. 0행 / false / true.
5. 리뷰: `row is False` → `not started`, 변수 이름 `row` → `started`.

pytest 10 passed. 커밋 `7999d9b`. 측정 없음 (성공 경로의 쿼리 수가 같다).

## 알게 된 것

- `scalar_one()`은 0행이면 `NoResultFound`, `scalar_one_or_none()`은 `None`, 둘 다 2행 이상이면 `MultipleResultsFound`. `scalar()`는 2행 이상이어도 첫 행 값을 조용히 준다. `scalar*`는 행이 아니라 첫 컬럼 값이다.
- `scalar_one_or_none()`의 `None`은 "행 없음"과 "값이 NULL" 둘 다일 수 있다. `starts_at`이 `NOT NULL`이라 여기서는 구분이 필요 없다. nullable 컬럼이면 `one_or_none()`으로 행을 받아 구분한다.
- 시간 처리: 저장·비교는 절대 시점(`timestamptz`, `datetime.now(UTC)`)으로 하고, KST는 입력과 표시에서만 `ZoneInfo("Asia/Seoul")`로 붙인다. 국내 서비스에서 흔한 사고는 서머타임이 아니라, 컨테이너 기본 TZ(UTC)에서 naive `datetime.now()`나 PostgreSQL `timestamp`(without time zone)를 KST로 착각해서 9시간 어긋나는 것이다. 시각을 받는 API를 만들 때는 오프셋이 붙은 값만 받는다 (pydantic `AwareDatetime`).
- `x or default`는 `0`·빈 문자열이 유효한 값이면 버그다. 그때는 `x if x is not None else default`.
