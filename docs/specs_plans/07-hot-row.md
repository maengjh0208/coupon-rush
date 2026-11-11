# 07. hot row: 쿠폰 row 하나의 행 락이 처리량 상한을 정한다

## 문제

모든 발급 요청이 `coupon id=1` row 하나를 UPDATE한다. 행 락을 쥔 구간(UPDATE → app 왕복 → INSERT → app 왕복 → COMMIT)을 한 번에 한 트랜잭션만 지나간다. 2워커에서 약 1100 req/s, app·postgres CPU 모두 절반, 커넥션 대부분이 `Lock / transactionid` 대기였다 ([17](17-app-capacity.md)).

## 선택지

| | 방법 | 판단 |
|---|---|---|
| A. 락 구간 줄이기 | INSERT를 먼저, 조건부 UPDATE를 마지막에 둔다. 락 구간이 UPDATE → COMMIT이 된다 | 가장 작은 변경. DB만으로 어디까지 가는지 확인하려고 먼저 시도 |
| B. 줄 나누기 | 재고를 row N개로 쪼갠다 | 남은 row 고르기·매진 판정·집계가 복잡해진다. 제외 |
| C. 줄을 DB 밖으로 | 재고를 Redis로 옮기고 DB는 발급 row INSERT만 한다 | 실무의 일반 경로. 문제 8(Redis와 DB 불일치)이 따라온다. A 다음에 진행 |

## A: INSERT → UPDATE 순서 (시도 후 되돌림)

### 변경 (`42aca93`, `b161868`에서 revert)

- 발급 INSERT를 조건부 UPDATE 위로 옮겼다. 매진이면 `raise`로 INSERT까지 롤백된다.
- INSERT가 먼저 실행되면 없는 쿠폰의 FK 위반(`23503`)이 `except IntegrityError`에서 ALREADY_ISSUED로 둔갑한다. 기존 404 테스트가 잡았다. INSERT 전에 쿠폰 존재 확인을 추가해서 해결했다 (`sqlstate` 구분은 고르지 않음).

### 측정 (RATE 1500, 2워커 / app `cpus: 2`, 나머지는 [17](17-app-capacity.md)과 같음)

| 순서 | FK | 처리량 | postgres CPU | 결과 |
|---|---|---|---|---|
| UPDATE → INSERT ([17](17-app-capacity.md), 11-10, 3회) | 있음 | 1097 (1091~1115) | 100% / 200% | |
| INSERT → UPDATE (11-11, 3회, `20261111-131600-abf7f96`, 커밋 전 측정) | 있음 | **470 (436~569)** | 200% 포화 | |
| INSERT → UPDATE (11-11, 20초 1회, 개발 DB에서 FK만 DROP) | 없음 | 1057 | 기록 없음 | |

모든 실행에서 HTTP 실패 0%, 발급 row 수 == `issued_quantity`.

`pg_stat_activity` 5장 합계 (RATE 1500 실행 중):

| 대기 | FK 있음 | FK 없음 |
|---|---|---|
| `Lock / transactionid` | 103 | 131 |
| `LWLock / LockManager` | 19 | 0 |
| `LWLock / BufferContent` | 6 | 0 |
| `LWLock / MultiXactMemberSLRU` | 3 | 0 |
| `Lock / tuple` | 0 | 4 |
| `idle in transaction` (락 보유자가 app을 기다림) | 매 장 0~2 | 매 장 1~2 |

### 해석

- **처리량이 절반이 된 원인은 FK다.** FK만 빼면 1057로 돌아오고 MultiXact·BufferContent 대기가 사라진다. INSERT의 FK 검사는 참조하는 쿠폰 row에 `FOR KEY SHARE` 락을 건다. UPDATE가 먼저면 같은 트랜잭션이 이미 더 강한 락을 쥐고 있어서 추가 비용이 거의 없다. INSERT가 먼저면 모든 트랜잭션이 **동시에** 같은 row에 KEY SHARE를 건다. 공유 락 소유자가 여럿이면 Postgres는 MultiXact(락 소유자 목록)를 만들어 tuple 헤더에 기록하고, 소유자가 바뀔 때마다 새 목록을 만든다. 그래서 hot row의 페이지와 MultiXact 저장소에 쓰기가 몰렸다 (DB CPU 200%).
- **순서를 바꿔서 얻은 처리량은 없다.** FK 없이 1057로, 17의 1097과 오차 범위 안이다 (날짜가 다르고 1회 측정). 왕복 하나를 빼도 락 구간에는 UPDATE, app 왕복, COMMIT(WAL flush)이 남는다. 락 보유자는 여전히 `idle in transaction`으로 app을 기다린다.
- **결정: A를 되돌린다.** 순서를 바꾸려면 FK를 포기해야 하는데 얻는 것이 없다. DB 행 락으로 줄 세우는 방식의 상한은 이 구성에서 약 1100 req/s다. C로 간다.

### 알게 된 것

- 공유 락끼리는 서로 막지 않지만 공짜가 아니다. Postgres는 행 락을 메모리가 아니라 tuple 헤더에 기록한다. 같은 row에 동시 공유 락이 몰리면 MultiXact 생성과 페이지 쓰기 경합이 생긴다.
- FK는 자식 INSERT마다 부모 row에 락을 건다. 부모가 hot row면 FK가 경합 지점이 된다. 대규모 트래픽 서비스에서 hot 부모를 참조하는 FK를 빼는 이유다.
- **C에서 다시 나온다.** 재고를 Redis로 옮겨도 DB는 발급 row를 INSERT하고, FK가 있으면 모든 INSERT가 동시에 쿠폰 row에 KEY SHARE를 건다. FK를 유지할지는 C에서 측정해서 정한다.
- 실험에서 FK를 DROP한 뒤 `reset_db.py`(또는 내부에서 그걸 부르는 `bench.sh`)를 다시 실행하면 FK가 되살아난다. 실험 조건은 측정 직전에 확인한다.
- 측정용 변경(`bench.sh`의 `--quiet` 제거)을 성능 변경과 같은 커밋에 넣었더니 revert 때 함께 사라졌다. 성격이 다른 변경은 커밋을 나눈다.

## C: 재고를 Redis로

### 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 재고 차감 | `DECR coupon:{id}:stock`, 결과 < 0이면 `INCR`로 되돌리고 SOLD_OUT | 명령 하나라 원자적이다. 가장 작은 tracer bullet. 여러 명령을 묶어야 할 때(중복 확인까지 Redis로) Lua 스크립트를 쓴다 |
| `issued_quantity` | 컬럼 제거, 발급 수 = `coupon_issue` row 수 | DB에서 매 요청 +1을 하면 hot row UPDATE가 남는다. 원천을 발급 row 하나로 두면 Redis가 의심스러울 때 `total_quantity - COUNT(*)`로 재계산할 수 있다 (문제 8) |
| FK (`coupon_issue.coupon_id`) | 유지 | INSERT만 몰리는 구조에서 KEY SHARE 비용을 측정한 뒤 정한다 |
| 순서 | EXISTS → DECR → INSERT → COMMIT | 매진 뒤 요청은 Redis에서 거절되고 DB를 건드리지 않는다 |
| DECR 후 실패 | 중복(IntegrityError)은 `INCR`로 되돌림. app 장애·COMMIT 실패로 인한 유실은 범위 밖 | 문제 8 |
| TTL, 영속성 | 기본값 (TTL 없음, 이미지 기본 RDB) | 종료 시각이 아직 없다. 재고 키의 영속성은 믿지 않고 DB에서 재계산하는 것을 원칙으로 한다 (문제 8) |

### 진행

(진행 중)
