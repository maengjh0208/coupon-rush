# 21. Redis 재시작 직후 첫 요청 500: 고치지 않기로 결정

## 문제

Redis가 재시작되면 app 연결 풀에 남은 연결은 이미 끊긴 상태다. 재시작 후 첫 요청이 그 연결로 `DECR`를 보내고 응답을 읽다가 `redis.exceptions.ConnectionError: Connection closed by server.`로 500이 난다. 다음 요청부터는 새 연결을 맺어서 정상이다.

## 재현 (11-25)

조건: 로컬 docker compose, app 워커 1 (`--reload`), redis:8, redis-py 8.1.0, QTY 10, 1회.

| 순서 | 결과 |
|---|---|
| user 1 | 201 |
| `docker compose kill redis && docker compose start redis`, PING 응답 확인 | |
| user 2 | **500** |
| user 3, 4 | 201 |

traceback: `reserve_stock`의 `redis.decr` → `execute_command` → `call_with_retry` → `read_response` → `Connection closed by server.` 재시도 루프 안에서 났는데 한 번도 재시도하지 않았다.

## 원인: `from_url`은 재시도가 0회

```
from_url  retries = 0   NoBackoff
Redis()   retries = 10  ExponentialWithJitterBackoff
```

`Redis.__init__`의 기본 `retry`는 클라이언트가 풀을 직접 만들 때만 연결에 들어간다. `from_url`은 풀을 먼저 만들어서 넘기고, 풀을 넘기면 풀의 설정(재시도 없음)을 쓴다. 그래서 끊긴 연결로 보낸 첫 명령이 그대로 실패한다.

## 선택지

| | 방식 | 판단 |
|---|---|---|
| A | 고치지 않는다. 재시작 직후 몇 건의 실패를 감수한다 | **선택**. 아래 이유 |
| B | Redis `ConnectionError`를 `AppError`로 바꿔 503을 응답 | 500(버그)과 503(잠시 뒤 재시도)을 구분해 준다. 지금은 클라이언트가 이 구분을 쓰지 않아서 보류 |
| C | `retry=Retry(...)`로 재시도 | 아래 1, 2 때문에 쓰지 않는다 |

## 결정과 이유

1. **`DECR`는 멱등이 아니다.** 이번 실패는 명령을 보낸 **뒤** 응답을 읽다가 났다. 서버가 죽어 있었으니 실행되지 않았지만, 네트워크가 잠깐 끊긴 경우라면 Redis는 이미 감소시켰고 응답만 잃었을 수 있다. 그때 재시도하면 이중 차감이 되어 재고가 조용히 줄어든다(덜 발급됨). DB 조건부 UPDATE(문제 8)는 초과 발급만 막고 덜 발급되는 경우는 막지 못한다. 눈에 보이는 500이 보이지 않는 재고 유실보다 낫다.
2. **backoff 동안 DB 연결을 쥔다.** `DECR`는 DB 트랜잭션 안에서 SELECT 두 번을 마친 뒤 실행된다. Redis가 몇 초 동안 죽어 있으면 모든 요청이 backoff로 기다리면서 DB 커넥션을 쥐고 있게 되고, DB 풀까지 고갈된다. Redis 장애가 DB 장애로 번진다.
3. **얻는 것이 작다.** 실패하는 요청은 많아야 재시작 시점에 풀에 남아 있던 연결 수만큼이다. `DECR`가 실행되지 않았으니 재고도 어긋나지 않는다. Redis가 SIGKILL될 정도의 장애라면 스냅샷으로 재고가 과거로 돌아가는 문제(문제 8)가 훨씬 크다. 이번 재현에서도 재시작 후 재고가 스냅샷 값 10으로 돌아갔다.

결정에 따라, 테스트하려고 뽑아 둔 `create_redis()`는 되돌린다 (YAGNI). 코드 변경 없음, 측정 없음.

## 알게 된 것

- redis-py는 생성 방식에 따라 재시도 기본값이 다르다. `from_url`이나 `connection_pool=`을 넘기면 재시도 0회, `Redis(host=...)`로 만들면 재시도 10회. 생성 방식만 바꿔도 동작이 조용히 바뀐다. 실제 값은 `conn.retry.get_retries()`로 확인한다.
- 재시도는 멱등인 명령에만 안전하다. `GET`·`SET`·`PING`은 안전하고, `INCR`·`DECR`·`LPUSH`는 응답을 잃은 뒤 재시도하면 두 번 적용된다. 응답을 읽는 단계의 실패는 "실행됐는지 모름"이다.
- 재시도는 공짜가 아니다. 기다리는 동안 쥐고 있는 자원(DB 커넥션, 워커)이 장애를 다른 곳으로 옮긴다. 재시도를 넣을 때는 그 요청이 무엇을 쥔 채로 기다리는지 먼저 본다.
- 연결 상태를 먼저 확인하는 `health_check_interval`은 `PING`(멱등)만 다시 보내니 이중 차감 위험이 없다. 다만 N초 넘게 쓰지 않은 연결에만 동작해서, 부하 중 재시작에는 효과가 없다.
