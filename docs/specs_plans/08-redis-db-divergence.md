# 08. Redis와 DB 재고 불일치

## 문제

문제 7 C에서 재고를 Redis로 옮기면서 수량 판단이 Redis 한 곳에만 남았다. DB는 수량을 묻지 않고 INSERT한다. Redis 값이 틀리면 막을 곳이 없다.

| 시나리오 | 결과 |
|---|---|
| DECR 후 app 장애·COMMIT 실패 | 재고 유실 → 과소 발급 |
| Redis 키 유실 | 전부 SOLD_OUT (fail closed) |
| 오래된 스냅샷 복구 | 재고가 과거로 돌아감 → **초과 발급** (fail open, 가장 위험) |

## 재현 (11-18, 수정 전 코드 `03ecb77`)

1. `make reset-db QTY=10` → `redis-cli SAVE` → 사용자 1–5 발급 (Redis 5, DB 5행)
2. `docker compose kill redis && docker compose start redis`. `restart`/`stop`은 SIGTERM이라 Redis가 종료 전에 저장해서 재현되지 않는다. SIGKILL이어야 실제 장애와 같다.
3. 재시작 후 재고 **10**. `dump.rdb`는 `SAVE` 시점 값이고, 이후 DECR 5번은 기본 save 조건(`3600 1 300 100 60 10000`)을 못 채워 메모리에만 있었다.
4. 사용자 6–15 전부 201, 16에서 SOLD_OUT. `coupon_issue` **15행 > total_quantity 10**.

원천은 발급 row인데, 코드는 파생값(Redis)을 기준으로 판단하고 있었다.

## 선택지

| | 방식 | 한계 |
|---|---|---|
| A | Redis 내구성 (AOF `everysec`) | 유실 창을 약 1초로 줄일 뿐. failover 복제 지연, 백업 복구, 실수 FLUSH는 못 막는다 |
| B | DB 기준 재계산 (`total_quantity - COUNT(*)`) | 언제 돌릴지가 어렵다. 오래된 값은 키가 있어서 감지가 안 되고, 트래픽 중에는 진행 중 발급과 경합한다 |
| C | DB를 최종 방어선으로 (`issued_quantity` 조건부 UPDATE) | 위반이 불가능해진다. 대가로 당첨자는 다시 hot row를 지나간다 |

**C를 골랐다.** A·B는 확률을 줄이고 C는 불가능하게 만든다. 초과 발급은 돈 문제라 "가끔"도 안 된다. Redis는 빠른 거절(gate), DB는 원천(source of truth)으로 두는 실무의 흔한 구조다.

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| `issued_quantity` | 되살림 (`server_default="0"`) | 07에서 hot row를 피하려고 지웠지만, 수량을 원자적으로 막을 곳이 DB에 필요하다. 07의 "원천은 발급 row, 의심되면 재계산" 원칙은 B의 한계 때문에 버린다 |
| 순서 | EXISTS → DECR → **UPDATE → INSERT** → COMMIT | INSERT를 먼저 하면 FK 검사의 `FOR KEY SHARE`가 hot row에 동시에 몰려 MultiXact 경합이 생긴다 (07 A: 1097 → 470). UPDATE가 먼저면 이미 `FOR NO KEY UPDATE`를 쥐고 있어서 KEY SHARE 비용이 없다 |
| DB가 매진으로 거절할 때 | Redis에 **보상 INCR 안 함** | 이 거절은 Redis가 실제보다 많이 갖고 있었다는 뜻이다. INCR하면 Redis 재고가 영원히 줄지 않아 모든 요청이 hot row로 간다. 안 하면 거절될 때마다 하나씩 줄어 스스로 수렴한다 |
| Redis를 DB 값으로 SET | 안 함 | 한 번에 맞출 수 있지만, 동시에 진행 중인 요청의 롤백과 경합해 과소 발급이 될 수 있다. 자연 수렴으로 충분하다 |
| 보상 구분 방식 | `reserve_stock`이 `CouponSoldOut`만 다시 던지고 나머지 예외는 INCR | 규칙은 "매진이면 재고를 돌려주지 않는다". 문자열 비교가 아니라 타입으로 구분한다. 중복(`DuplicateCouponIssue`)·장애·취소는 재고를 안 썼으니 INCR |
| 에러 응답 | `AppError` 부모에 `status_code: int`, `code: str` 주석만, 서브클래스는 클래스 속성, 핸들러 하나 | 상수는 인스턴스마다 다르지 않으니 클래스 속성. 부모에 기본값을 두지 않아 빠뜨리면 바로 드러난다 |

## 진행

1. red: Redis 재고 1(오래된 값), DB 재고 0 → 두 번째 사용자가 SOLD_OUT이어야 한다. `201 == 409`로 실패 확인.
2. green: `issued_quantity` 컬럼 + 조건부 UPDATE, `rowcount == 0`이면 SOLD_OUT.
3. red: 같은 시나리오에서 거절 후 Redis 재고가 0이어야 한다. 보상 INCR 때문에 `1 != 0`.
4. green: `reserve_stock`에서 `except CouponSoldOut: raise`를 `except BaseException` 위에 둔다 (위에서부터 매칭).
5. 리팩터: `IssueRejected(code)` → `AppError` 계층. 도중에 `JSONResponse(code=...)`(없는 인자 → `TypeError`, 에러 응답 전부 500)와 `super.__init__`(괄호 누락)을 거쳤다.

pytest 9 passed. 커밋 `43aa19e`.

## 재현 (수정 후, 11-18)

QTY 10, 같은 kill 절차. 사용자 1은 500(아래 참고)이라 실제 발급 4장, Redis 6 → 재시작 후 **10**.

| 요청 | 결과 | Redis |
|---|---|---|
| 사용자 6 | 500 (재시작 후 첫 요청) | 10 |
| 사용자 7–12 | 201 | 10 → 5 |
| 사용자 13–16 | **409 SOLD_OUT** (DB가 거절) | 4 → **0** (자연 수렴) |

`coupon_issue` 10행 == `issued_quantity` 10 == `total_quantity` 10. 초과 발급 없음.

재시작 직후 첫 요청의 500은 app 연결 풀에 남은 끊긴 연결 때문이다 (`redis.exceptions.ConnectionError: Connection closed by server.`). DECR 전에 실패해서 재고는 어긋나지 않는다 → 문제 21.

## 측정

조건: 07 C와 같음 (app 워커 2 / `cpus: 2` / 512m, postgres `cpus: 2` / 1g, redis `cpus: 1` / 256m, 풀 5+10, k6 `constant-arrival-rate` 30s, `preAllocatedVUs=500`, `maxVUs=1000`, 3회). 코드 `43aa19e`, 수량 1000 측정은 `bench.sh`에 `QTY` 변수를 추가한 커밋 전 상태. CPU는 `docker stats` 눈대중.

| 수량 | RATE | 결과 폴더 | 처리량 | p50 | 201 | app / postgres CPU |
|---|---|---|---|---|---|---|
| 07 C 참고 (Redis만, 11-16) | 2000 | `20261116-213038-6d57978` | 1434 (1407~1559) | 18~20ms | 전부 | 200%+ / 60~70% |
| 100000 | 2000 | `20261118-174602-43aa19e` | **768 (738~801)** | 1.19~1.29s | 전부 | 여유 / 여유 |
| 1000 | 2000 | `20261118-175709-43aa19e` | **1743 (1622~1902)** | 67~472ms | 1000 | **200%+** / 여유 |

모든 실행에서 발급 row 수 + Redis 재고 == 수량. 수량 1000에서 k6 `http_req_failed` 약 98%는 409 SOLD_OUT을 실패로 센 것이고, HTTP 오류가 아니다.

## 해석

- **수량 > 요청 수면 hot row 상한으로 돌아간다.** 수량 100000이면 30초 요청(최대 6만)이 전부 당첨자라 Redis가 걸러내는 게 없다. 모든 요청이 쿠폰 row 행 락에 줄을 서고, CPU는 남는데 지연이 1초대다. 07의 증상과 같다 → 문제 22.
- **수량 << 요청 수면 C의 비용은 거의 없다.** 1000건만 행 락을 지나가고 나머지는 Redis에서 SOLD_OUT으로 끝난다. 병목은 다시 app CPU다 (문제 20). 실제 선착순 쿠폰은 이쪽이다.
- 수량 1000의 처리량(1743)이 07 C(1434)보다 높은 건 C가 빨라서가 아니다. 98%가 SOLD_OUT 경로(UPDATE·INSERT·쓰기 COMMIT 없음)라 요청당 일이 적다. 요청 구성이 달라서 직접 비교할 수 없다.
- 수량 1000의 3회가 1622 → 1704 → 1902로 단조 증가했다. 원인은 확인하지 않았다 (문제 18과 같은 종류의 흔들림일 수 있다).
- 수량 100000의 768이 DB 행 락만 쓰던 17의 1097보다 낮다. 날짜와 RATE(1500 vs 2000)가 달라 비교 근거로 쓰지 않는다.

## 알게 된 것

- Redis는 메모리가 원본이고 디스크는 가끔 찍는 스냅샷이다. SIGKILL·OOM이면 마지막 스냅샷 이후 쓰기가 사라지고, 값이 **과거로** 돌아간다. 키가 사라지는 것보다 위험하다. 틀렸다는 신호가 없기 때문이다.
- 파생값(캐시·카운터)은 gate로 쓰고, 위반하면 안 되는 불변식은 원천(DB) 한 곳에서 원자적으로 막는다.
- 보상은 "실패하면 되돌린다"가 아니라 "자원을 안 썼으면 되돌린다"다. Redis가 틀려서 난 실패를 보상하면 틀린 값이 고정된다.
- PostgreSQL 행 락은 4단계다. FK 검사(`FOR KEY SHARE`)와 키가 아닌 컬럼 UPDATE(`FOR NO KEY UPDATE`)는 서로 막지 않는다 (9.3+). 비용은 막힘이 아니라 공유 락 기록(MultiXact)에서 생긴다. InnoDB는 FK 검사가 S 락이라 부모 UPDATE를 막는다.
- 성능 예측은 부하 시나리오에 달려 있다. "당첨자만 hot row를 지난다"는 맞았지만, bench 수량이 100000이라 당첨자가 곧 전체였다. 측정 조건에 수량을 함께 적는다.

## 범위 밖

- DECR 후 app 장애로 인한 재고 유실(과소 발급): C로 막히지 않는다. 필요하면 매진 후 `total_quantity - issued_quantity`로 재계산한다.
- Redis 키 유실 → 전부 SOLD_OUT: fail closed라 안전하지만 판매가 멈춘다. 키가 없을 때 DB에서 다시 채우는 경로는 아직 없다.
- Redis 재시작 후 첫 요청 500 → 문제 21.
- 수량이 큰 쿠폰의 hot row 상한 → 문제 22.

## 결정

**문제 8 완료.** 오래된 스냅샷 복구에도 초과 발급이 일어나지 않는다. 일반적인 선착순(수량 << 요청)에서는 처리량 비용이 드러나지 않고 병목은 app CPU로 남는다.
