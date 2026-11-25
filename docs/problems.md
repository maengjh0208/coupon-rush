# 해결할 문제 목록

문제만 적는다. 해결 방법은 문제를 시작할 때 같이 정하고 `docs/specs_plans/`에 남긴다.
순서와 목록은 앞 단계의 측정 결과에 따라 바뀔 수 있다.

상태: `대기` / `진행` / `완료`

## 1단계: 문제를 눈으로 확인하기

| # | 문제 | 상태 |
|---|---|---|
| 1 | 요청이 FastAPI에서 PostgreSQL까지 관통하는 최소 경로가 없다 (트레이서 불릿) | 완료 ([01](specs_plans/01-tracer-bullet.md)) |
| 2 | 락 없는 발급 로직에서 초과 발급·중복 발급·갱신 손실이 실제로 일어나는지 확인한 적이 없다 | 완료 ([02](specs_plans/02-race-reproduction.md)) |
| 3 | 조건이 고정된 부하 테스트 환경이 없어 측정값을 비교 근거로 쓸 수 없다 | 완료 ([03](specs_plans/03-load-test-environment.md)) |
| 14 | 없는 쿠폰에 발급 요청하면 500이 난다 (API 명세는 404). 문제 4 이후로는 FOR UPDATE의 `.one()`이 `NoResultFound`를 던진다 | 완료 ([14](specs_plans/14-coupon-not-found.md)) |
| 15 | 시작 시각(`starts_at`) 이전에도 발급된다 (도메인 규칙은 409 `NOT_STARTED`) | 완료 ([15](specs_plans/15-not-started.md)) |

## 2단계: DB만으로 막을 수 있는가

| # | 문제 | 상태 |
|---|---|---|
| 4 | 동시 요청에서 재고보다 많이 발급된다 | 완료 ([04](specs_plans/04-over-issue-for-update.md)) |
| 5 | 같은 사용자가 쿠폰을 두 장 이상 받는다 | 완료 ([05](specs_plans/05-duplicate-issue-unique.md)) |
| 6 | FOR UPDATE는 락을 쥔 채 왕복 3번(SELECT → UPDATE → INSERT) + COMMIT을 해서, 락 보유 시간이 처리량 상한을 정한다 ([04](specs_plans/04-over-issue-for-update.md), [05](specs_plans/05-duplicate-issue-unique.md) 참고) | 완료 ([06](specs_plans/06-conditional-update.md)) |
| 16 | 같은 조건에서 저부하(RATE 100~500) p99가 실행마다 크게 흔들려 꼬리 지연을 비교할 수 없다 (RATE 100 p99 14~581ms, [05](specs_plans/05-duplicate-issue-unique.md) 참고) | 대기 |
| 18 | 같은 코드·조건이라도 측정한 날(시각)이 다르면 포화 처리량이 크게 다르다 (05 코드 RATE 1000: 11-05 708 → 11-06 612, 같은 날에도 06 코드 546 → 608, [06](specs_plans/06-conditional-update.md) 참고) | 대기 |

## 3단계: DB가 병목이라면

| # | 문제 | 상태 |
|---|---|---|
| 17 | 처리량 상한을 app이 정한다. RATE 700부터 app CPU(`cpus: 1`, 워커 1)가 90~95%로 포화되고 postgres는 40% 근처에서 여유가 있다 ([06](specs_plans/06-conditional-update.md) 참고) | 완료 ([17](specs_plans/17-app-capacity.md)) |
| 7 | 모든 요청이 쿠폰 row 하나의 행 락을 줄 서서 지나가서(hot row), 락 보유 시간이 처리량 상한을 정한다. 2워커에서 약 1100 req/s, CPU는 app·postgres 모두 절반만 쓰고 커넥션 대부분이 락 대기 ([17](specs_plans/17-app-capacity.md) 참고) | 완료 ([07](specs_plans/07-hot-row.md)) |
| 20 | 재고를 Redis로 옮긴 뒤 처리량 상한을 다시 app CPU가 정한다. 2워커 / `cpus: 2`에서 약 1450 req/s, app CPU 200% 포화, postgres 60~70% (요청당 app CPU 약 1.4ms, [07](specs_plans/07-hot-row.md) 참고) | 대기 |
| 8 | 재고를 Redis로 옮기면 Redis와 DB의 상태가 어긋날 수 있다 (DECR 후 app 장애·COMMIT 실패로 재고 유실, Redis 재시작·오래된 스냅샷 복구·failover로 재고 값이 사라지거나 과거로 돌아감) | 완료 ([08](specs_plans/08-redis-db-divergence.md), DB 조건부 UPDATE를 최종 방어선으로) |
| 21 | Redis가 재시작되면 재시작 후 첫 요청이 500으로 실패한다. app 연결 풀에 남은 끊긴 연결을 그대로 써서 `redis.exceptions.ConnectionError: Connection closed by server`가 난다. DECR 전에 실패해서 재고는 어긋나지 않는다 (11-18 문제 8 kill 재현에서 2회 확인) | 완료 ([21](specs_plans/21-redis-restart-stale-connection.md), 고치지 않기로 결정: DECR 재시도는 이중 차감 위험) |
| 22 | DB를 최종 방어선으로 두면(문제 8) 발급에 성공하는 모든 요청이 다시 쿠폰 row의 행 락을 지나간다. 수량이 요청 수보다 크면 Redis가 걸러내는 요청이 없어서 처리량이 hot row 상한으로 돌아간다 (RATE 2000, 수량 100000, 2워커 / `cpus: 2`: 07 C의 약 1450에서 약 770 req/s로 떨어짐, app·postgres CPU 여유) | 대기 |
| 19 | 없는 쿠폰 id로 요청하면 `DECR`이 Redis에 `coupon:{id}:stock` 키를 새로 만들고 남긴다. 임의의 id로 요청하면 쓰레기 키가 제한 없이 쌓인다 ([07](specs_plans/07-hot-row.md) 참고) | 완료 ([07](specs_plans/07-hot-row.md), DECR 전에 쿠폰 존재 확인) |

## 4단계: 동기 처리의 한계

| # | 문제 | 상태 |
|---|---|---|
| 9 | 오픈 시각에 요청이 몰리면 동기 처리로는 응답 지연·실패가 커진다 (측정으로 확인 필요) | 대기 |
| 10 | 처리 중 실패하면 발급이 유실되거나, 재시도하면 중복 처리된다 | 대기 |
| 11 | 계속 실패하는 요청이 처리 흐름을 막는다 | 대기 |

## 5단계: 운영

| # | 문제 | 상태 |
|---|---|---|
| 12 | 시스템 내부에서 무슨 일이 일어나는지 볼 수단이 없다 | 대기 |
| 13 | 로컬 측정 결과가 실제 분리된 환경에서도 유효한지 모른다 | 대기 |
