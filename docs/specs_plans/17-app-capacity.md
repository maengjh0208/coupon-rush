# 17. app 처리량 상한: uvloop/httptools, 워커 2개

## 문제

06 측정에서 RATE 700부터 app CPU(`cpus: 1`, 워커 1)가 90~95%로 포화되고 postgres는 40% 근처에서 여유가 있었다. app이 먼저 막히면 DB 쪽 개선(문제 6의 락 구간 단축 등)을 측정할 수 없다. 요청당 app CPU는 약 1 CPU / 610 req/s ≈ 1.6ms였다.

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 순서 | A(요청당 CPU 줄이기) → B(CPU 더 쓰기) | 같은 CPU 조건에서 효율부터 확인한다. 비효율인 채로 scale-out하면 비효율도 N배가 된다 |
| A | `uvicorn[standard]` (uvloop + httptools), bench command에 `--loop uvloop --http httptools` 명시 | 기본 `auto`는 패키지가 없으면 조용히 순수 Python으로 떨어진다. 명시하면 없을 때 시작이 실패하고, 측정 조건이 설정 파일에 드러난다 |
| B | `--workers 2` + app `cpus: 2` | 워커는 프로세스라 GIL이 따로 있어 CPU를 N개까지 쓴다. `cpus`를 같이 올려야 의미가 있다. 커넥션은 워커 수 × 풀(15) = 30으로 `max_connections` 100 안 |
| 프로파일링 (py-spy) | 하지 않음 | 1.6ms의 내역보다 다음 병목 확인이 우선이었다 |
| 테스트 | 새 테스트 없음 | 앱 동작은 바뀌지 않는다. 기존 테스트 통과로 확인 |

## 측정

조건: 06과 같다 (postgres `cpus: 2` / 1g, 풀 5+10, k6 `constant-arrival-rate` 30s, `maxVUs=1000`, 재고 100000, 3회). 바뀐 것은 표에 적었다. CPU는 `docker stats` 눈대중.

| 날짜 | 구성 | 결과 폴더 | RATE 1000 | RATE 1500 | app / postgres CPU |
|---|---|---|---|---|---|
| 11-06 17:14 | 워커 1, CPU 1 | `20261106-171438-c8f7dde` | 673 (639~682) | - | 94%+ / 40~50% |
| 11-06 17:25 | + uvloop/httptools | `20261106-172529-d88380c` | 676 (584~691) | - | (기록 없음) |
| 11-10 22:25 | 워커 1, CPU 1 | `20261110-222501-0f0a5ae` | 996 (986~1000), 포화 안 됨 | - | (기록 없음) |
| 11-10 22:34 | 워커 2, CPU 2 | `20261110-223401-0f0a5ae` (커밋 전, 아래 참고) | 1000 (907~1000) | **1097 (1091~1115)**, p50 0.88s | 100% / 200%, 100% / 200% |

모든 실행에서 HTTP 실패 0%, 발급 row 수 == `issued_quantity`.

### RATE 1500 중 `pg_stat_activity` 한 장 (2워커)

```
active               Lock    transactionid   16
active               LWLock  LockManager     12
idle                 Client  ClientRead       3
idle in transaction  Client  ClientRead       1
```

## 해석

- **uvloop/httptools는 10%를 넘는 효과가 없었다.** 같은 날 측정 변동이 약 10%라 그 안쪽은 구분할 수 없다. 요청당 CPU의 대부분은 루프·파서 밖(FastAPI, pydantic, SQLAlchemy + asyncpg)에 있다고 추정한다 (확인 안 함).
- **2워커에서는 app이 병목이 아니다.** app과 postgres 모두 CPU를 절반만 쓰는데 VU 1000개가 묶이고 p50이 0.88s다. CPU가 남는데 지연이 길면 계산이 아니라 대기다.
- **대기는 쿠폰 row의 행 락이다 (한 장 기준).** 32개 커넥션 중 28개가 락 대기(`transactionid` 16 + 락 관리자 경합 `LockManager` 12)였다. 락을 쥔 것으로 보이는 트랜잭션 1개는 `idle in transaction`, 즉 app이 다음 문장(INSERT, COMMIT)을 보내기를 기다리고 있었다. 락 보유 시간에 app 왕복이 포함된다는 뜻이다.
- **문제 6의 전제가 이제 관찰된다.** app 병목이 사라지자 "락 보유 시간이 처리량 상한을 정한다"가 드러났다. 약 1100 req/s면 트랜잭션 하나가 약 0.9ms 락을 쥐는 셈이다. 문제 7로 이어진다.
- 1워커의 오늘 상한은 측정하지 않았다(RATE 1000에서 포화 안 됨). 그래서 2워커의 개선 폭은 숫자로 말하지 않는다. 같은 1워커 코드가 11-06 673에서 11-10 996 이상으로 바뀐 것은 문제 18이다.

## 알게 된 것

- 워커는 스레드가 아니라 프로세스다. 메모리(전역 변수, `app.state`, 커넥션 풀)를 공유하지 않는다. 상태를 공유하려면 프로세스 밖 저장소(DB, Redis)가 필요하다. uvicorn은 multiprocessing **spawn**으로 워커를 띄워서 `docker compose top`에 `uvicorn`이 아니라 `spawn_main`으로 보이고, `resource_tracker` 프로세스가 하나 더 있다.
- `pg_stat_activity`는 스냅샷이다. 한 장은 "이런 일이 있다"까지만 말한다. "주로 이렇다"를 말하려면 여러 장을 모아 센다 (샘플링).
- 측정 전 커밋을 세 번 놓쳤다. 사람 주의력보다 `bench.sh`에서 dirty tree를 거부하는 편이 확실하다.
