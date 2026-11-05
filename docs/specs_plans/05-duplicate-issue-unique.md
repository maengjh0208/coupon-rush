# 05. 중복 발급: UNIQUE 제약

## 문제

같은 user가 동시에 요청하면 쿠폰을 여러 장 받았다 (10회 동시에 4~10장, [02](02-race-reproduction.md)). 중복 확인(EXISTS) → INSERT가 check-then-act라 모든 요청이 "아직 안 받음"을 본다. 문제 4의 FOR UPDATE는 이걸 막지 못한다. 락은 잡은 뒤의 읽기만 보호하는데 EXISTS는 락보다 앞에 있다.

## 요구사항 (이번 범위)

- 동시 요청에서도 (coupon, user)당 발급 row 1개.
- 거절된 요청이 올린 `issued_quantity +1`은 취소된다 (카운터 == 발급 row 수).
- 응답은 기존과 같다: 409 `{"code": "ALREADY_ISSUED"}`.

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 중복 방지 수단 | `UniqueConstraint("coupon_id", "user_id")` | "user당 1장"을 앱의 `if`가 아니라 DB 규칙으로 표현한다. 확인과 쓰기가 INSERT 한 문장 안에서 원자적이라 틈이 없다. 재고를 어디에 두든(DB row, 3단계의 Redis) 그대로 유지된다. EXISTS를 FOR UPDATE 뒤로 옮기는 방법은 문장 순서·락·격리 수준에 기댄 우연이라 제외 ([04](04-over-issue-for-update.md)) |
| 위반 감지 | INSERT 후 `IntegrityError` 잡기 | 위반이 에러로 오고 트랜잭션이 aborted 상태가 되어, 반쪽 커밋이 구조적으로 어렵다. `ON CONFLICT DO NOTHING`은 에러가 아니라서 정상 return하면 COMMIT되고, 앞서 올린 카운터 +1이 확정된다 (아래 확인) |
| 롤백 방식 | 거절을 도메인 예외 `IssueRejected(code)`로 던지고, `exception_handler`에서 409로 변환 | 예외가 `get_conn`의 `yield`로 전달되어 `async with engine.begin()`이 ROLLBACK한다. "거절 = 예외 = 롤백" 규칙이 `get_conn` 한 곳에 있고, 핸들러는 트랜잭션을 모른다. 문제 6에서 구현체가 늘어도 각자 raise만 한다. 핸들러에서 `conn.rollback()`은 트랜잭션 책임이 두 곳으로 갈라지고, 문장 순서 바꾸기는 문제를 SOLD_OUT 경로로 옮길 뿐이다 |
| 기존 409 경로 (EXISTS, SOLD_OUT) | 같이 `raise IssueRejected`로 통일 | 지금은 쓰기 전에 끝나서 return이어도 결과가 같지만, 거절 경로마다 규칙이 다르지 않게 한다 |
| EXISTS 사전 확인 | 유지 (빠른 거절용) | 순차 중복(새로고침, 재클릭)을 락 대기와 UPDATE → 롤백 없이 걸러낸다. 정합성은 UNIQUE, 성능은 사전 확인 |
| `except IntegrityError` 범위 | 넓게 유지 (제약 이름으로 좁히지 않음) | 지금 도달 가능한 위반은 UNIQUE 하나뿐이다 (없는 쿠폰은 `.one()`에서 먼저 실패). 없는 쿠폰은 문제 14에서 명시적으로 404 처리하고, 그 테스트가 FK 위반이 ALREADY_ISSUED로 둔갑하는 회귀를 잡는다. 실무에서도 예외 클래스 단위로 잡는 경우가 많고, 제약 이름 매핑은 한 테이블에 UNIQUE가 여럿이라 구분이 필요할 때 쓴다 |
| 테스트 | 기존 xfail 테스트를 스펙으로 사용 + 카운터 정합성 가드 테스트 추가 | 기존 테스트는 row 수만 본다. 가드 테스트는 방식과 무관하게 결과(`issued_quantity == 1`)를 검사한다 |

## 진행 과정

1. UNIQUE만 추가 → 동시 중복 테스트가 xfail이 아니라 FAILED (`IntegrityError`). `raises=AssertionError` 덕분에 "DB는 막지만 앱이 409로 번역하지 못함"으로 실패 종류가 바뀐 게 드러났다.
2. `IssueRejected` + exception handler + `except IntegrityError` → XPASS(strict) → 마커 제거.
3. 나머지 409를 raise로 통일 (리팩터, 테스트 그대로 통과).
4. 가드 테스트 추가. 처음부터 통과하므로 뮤테이션으로 실패할 수 있는지 확인했다.

## 확인한 것

**롤백 순서** (FastAPI 0.143.1, Starlette 1.8.0). `engine.begin()` 자리에 출력용 가짜를 넣어 확인:

```
[handler] raise IssueRejected
[begin] 예외 IssueRejected 감지 -> ROLLBACK
[exception_handler] 409 응답 생성
```

FastAPI는 엔드포인트 예외를 yield 의존성의 `yield` 지점으로 던져 넣는다(`athrow`). `async with`가 예외로 끝나서 `__aexit__`가 rollback하고, 예외는 다시 올라가 exception handler가 응답을 만든다. 핸들러 안에서 `IssueRejected`를 잡아 return하면 COMMIT이 된다.

**except에서 잡고 return해도 카운터는 맞았다 (우연).** UPDATE +1 → 중복 INSERT 실패 → except에서 삼키고 정상 종료 → 카운터 0. Postgres는 aborted 트랜잭션에 대한 COMMIT을 에러 없이 ROLLBACK으로 처리한다. 결과는 맞지만 의도가 코드에 없고, except 뒤에 쿼리를 하나 더 보내면 `InFailedSQLTransactionError`가 난다.

**가드 테스트 뮤테이션.** INSERT를 `ON CONFLICT DO NOTHING RETURNING id` + `None`이면 `return JSONResponse(409)`로 바꾸면:

```
assert 5 == 1   (가드 테스트만 실패, 기존 7개는 전부 통과)
```

row는 1개지만 거절된 요청의 +1이 커밋됐다. 10이 아니라 5인 건 나머지가 락 전의 EXISTS에서 걸러졌기 때문이다.

**위반 종류 구분** (asyncpg). 필요해지면 이 값으로 좁힌다:

| 위반 | `e.orig.sqlstate` | `e.orig.__cause__.constraint_name` |
|---|---|---|
| UNIQUE | `23505` | 제약 이름 (`unique_coupon_issue_coupon_id_user_id`) |
| FK | `23503` | `coupon_issue_coupon_id_fkey` 형태 |

## 결과

- pytest 8 passed: 동시 같은 user 10회 → row 1개, `issued_quantity == 1`.

## 측정: FOR UPDATE + UNIQUE

### 조건

- 커밋 `e8363c4` (문제 14까지 반영), 결과 `bench-results/20261105-171133-e8363c4/`
- 나머지 조건은 [03](03-load-test-environment.md)과 같다: app `cpus: 1` / 512m / 워커 1, postgres `cpus: 2` / 1g / READ COMMITTED, 풀 `pool_size=5` + `max_overflow=10`, k6 `constant-arrival-rate` 30s, `preAllocatedVUs=500`, 재고 100000, 조건마다 3회
- CPU는 측정 중 `docker stats`를 눈으로 본 대략값 (기록 아님)

### 결과 (중앙값, 괄호는 3회 범위)

| RATE | 처리량 (req/s) | p50 | p95 | p99 | max | dropped | app / postgres CPU |
|---|---|---|---|---|---|---|---|
| 100 | 100 | 2.92ms | 10.5ms (5.9~185) | 176ms (14~581) | 392ms | 0 | 20% / 6% |
| 300 | 300 | 1.79ms | 97.7ms (8.6~194) | 336ms (69~462) | 668ms | 0 | 40% / 8% |
| 500 | 497 (494~500) | 1.53ms | 567ms (46~770) | 810ms (114~1230) | 1.42s | 0 (0~96) | 60% / 20% |
| 700 | **686 (645~692)** | 71.5ms (8~96) | 832ms (696~2390) | 1.32s (1.22~3.08) | 2.59s | 250 (247~1613) | 90% / 30% |
| 1000 | **708 (656~756)** | 1.30s | 1.96s (1.93~2.75) | 2.47s (2.36~3.56) | 4.51s | 7858 (6580~9675) | 92% / 35% |

- 모든 실행에서 HTTP 실패 0%, 발급 row 수 == `issued_quantity`.

### 이전 측정과 비교 (포화 구간)

| | 락 없음 ([03](03-load-test-environment.md)) | FOR UPDATE ([04](04-over-issue-for-update.md)) | FOR UPDATE + UNIQUE |
|---|---|---|---|
| RATE 700 처리량 / p50 | 700 / 2.4ms | 546 / 1.64s | 686 / 72ms |
| RATE 1000 처리량 | 729 | 430 | 708 |
| postgres CPU (700~1000) | 60~70% | 40~70% | 30~35% |

### 원인: EXISTS가 인덱스를 타게 됐다

UNIQUE 이전에는 `(coupon_id, user_id)` 인덱스가 없어서 EXISTS가 매번 seq scan이었다. bench의 user는 전부 새 user라 "없음"을 확인하려면 테이블 끝까지 봐야 하고, 테이블은 측정 중에 계속 커진다 (30초에 1~2만 row). 요청당 비용이 시간에 따라 늘어나는 구조였다.

개발 DB(발급 row 23421개)에서 같은 EXISTS를 비교 (`EXPLAIN ANALYZE`, 오버헤드 포함이라 상대 비교만):

```
인덱스 사용: Index Only Scan using unique_coupon_issue_coupon_id_user_id   0.19ms
인덱스 끔:   Seq Scan, Rows Removed by Filter: 11961                       4.2ms
```

### 해석

- **FOR UPDATE의 상한 하락이 거의 사라졌다.** 포화 구간 처리량이 기준선 근처로 돌아왔다(686 / 708 vs 700 / 729). app CPU가 다시 90%를 넘어서, 병목이 기준선처럼 app이다.
- **가설 (확인 안 함): 느린 쿼리는 락이 있을 때 전체 처리량을 깎는다.** EXISTS는 락 밖에 있는데도 락 버전이 크게 좋아졌다. postgres CPU 2개를 커넥션 최대 15개가 나눠 쓰므로, 다른 요청들의 seq scan이 CPU를 차지하면 락을 쥔 요청의 UPDATE·INSERT·COMMIT이 늦게 실행되어 락 보유 시간이 늘어난다. 락이 없으면 느린 쿼리는 그 요청의 지연만 늘리지만, 락이 있으면 직렬 구간을 늘려 처리량 상한을 깎는다. 문제 4의 "app 이벤트 루프 대기가 락 보유 시간을 늘린다"와 같은 구조가 DB 쪽에서도 일어난 것.
- **문제 6의 비교는 모든 방식에 UNIQUE가 있는 상태에서 한다.** 그래야 차이가 동시성 방식에서만 온다.
- **저부하 꼬리는 이번 측정으로 비교하지 않는다.** RATE 100 p99가 14~581ms, 500 p99가 114~1230ms로 실행마다 크게 흔들리고, 300·500의 p99는 FOR UPDATE 때(217ms, 725ms)보다 오히려 크다. 같은 조건에서 이 정도 차이는 코드보다 측정 중 다른 요인(호스트 프로세스, autovacuum·checkpoint, 인덱스 갱신 등)을 의심한다. 문제 16으로 분리했다.

## 알게 된 함정

- 사전 확인은 순차 요청만 막는다. 일반 서비스에서 "앞에서 다 검사"가 충분했던 건 같은 키의 동시 요청이 드물어서다. 선착순에서는 버튼 연타, 재시도, 오픈 순간 새로고침 때문에 동시 중복이 정상 경로다. 이때 500은 클라이언트 재시도를 불러 부하를 키운다.
- `get_conn`이 `engine.begin()`이면 핸들러의 정상 return은 COMMIT이다. 쓰기 후 거절 경로는 반드시 예외로 빠져나가야 한다.
- 처음부터 통과하는 테스트는 절대 실패하지 않는 테스트일 수도 있다. 코드를 일부러 망가뜨려 실패하는지 확인한다.
