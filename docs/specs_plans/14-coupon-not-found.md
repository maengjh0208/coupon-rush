# 14. 없는 쿠폰: 404 COUPON_NOT_FOUND

## 문제

없는 쿠폰에 발급 요청하면 500이 났다. 처음에는 INSERT의 FK 위반이 원인이었고, 문제 4 이후로는 FOR UPDATE SELECT의 `.one()`이 `NoResultFound`를 던졌다. API 명세는 404다. 500은 "서버 고장"이라 클라이언트가 재시도할 수 있으므로, 잘못된 요청은 4xx로 알려야 한다.

## 요구사항 (이번 범위)

- 없는 쿠폰 → 404 `{"code": "COUPON_NOT_FOUND"}`. 다른 에러와 같은 `{"code": ...}` 모양.
- 시작 시각 검사(`NOT_STARTED`)는 범위 밖. 문제 15로 추가했다.

## 결정과 이유

| 결정 | 선택 | 이유 |
|---|---|---|
| 존재 확인 위치 | FOR UPDATE SELECT를 `.one_or_none()`으로 바꾸고 `None`이면 404 | 쿼리가 늘지 않는다. 락을 거는 읽기가 곧 존재 확인이라 확인과 사용 사이에 틈이 없다. 별도 `SELECT EXISTS(coupon)`는 왕복이 늘고 락 밖에서 읽은 값이라 다시 check-then-act가 된다 |
| 표현 방식 | 별도 예외 `CouponNotFound` + 전용 exception handler | "거절"(규칙상 줄 수 없음, 409)과 "없음"(대상 없음, 404)은 다른 사건이라 예외 이름으로 구분한다. 예외로 빠져나가 "거절은 예외 = 롤백" 규칙도 그대로 따른다 |
| `HTTPException(404)` | 제외 | 응답이 `{"detail": ...}`로 다른 에러와 모양이 다르다. 발급 로직이 HTTP를 알게 되어 문제 6에서 구현체를 분리할 때 섞인다 |
| `IssueRejected`에 status 추가 | 제외 | "거절"에 "없음"이 섞여 의미가 흐려지고, 도메인 예외가 HTTP 상태 코드를 들고 다닌다 |
| 도메인 예외 계층 (`NotFound`, `Conflict` 등) | 미룸 | 실무에서 흔한 형태지만 예외가 두 개뿐이다 (YAGNI) |
| 검사 순서 (EXISTS가 존재 확인보다 앞) | 그대로 | 없는 쿠폰에도 EXISTS를 한 번 보내지만, FK 때문에 결과는 항상 false라 응답이 틀릴 일이 없다. 드문 경로의 쿼리 1번 |

## 결과

- pytest 9 passed. 추가 테스트: `client`만 받는 상태(TRUNCATE로 쿠폰 없음)에서 `/coupons/999/issue` → 404 + 본문 검사.

## 확인한 것

- **FK 위반은 이제 도달할 수 없다.** 처음부터 없는 쿠폰은 FOR UPDATE에서 `CouponNotFound`로 먼저 빠져나간다. FOR UPDATE 이후 INSERT 전에 쿠폰이 지워지는 경우는 FOR UPDATE 락이 커밋 전까지 DELETE를 기다리게 한다. 그래서 문제 5의 넓은 `except IntegrityError`가 FK 위반을 `ALREADY_ISSUED`로 바꾸는 일은 일어나지 않고, 순서가 바뀌는 회귀는 이 테스트가 본문(`ALREADY_ISSUED` ≠ `COUPON_NOT_FOUND`)으로 잡는다.

## 알게 된 함정

- **라우터의 404와 우리 404는 상태 코드가 같다.** 테스트 URL 오타(`/coupon/` vs `/coupons/`)로 라우트가 매칭되지 않아 FastAPI 기본 404 `{"detail": "Not Found"}`가 나왔다. 상태 코드만 검사했다면 구현 없이도 통과했을 것이다. 같은 상태 코드가 여러 출처에서 나올 수 있으면 본문으로 출처를 구분한다. `{"detail": "Not Found"}`는 핸들러까지 가지 않았다는 신호다.
- **pytest는 stdout을 가로챈다.** print는 테스트가 실패할 때 `Captured stdout call`에만 나온다. 바로 보려면 `-s`. 위 오타 때는 핸들러가 실행되지 않아서 `-s`로도 안 나왔을 것이다. `make pytest ARGS="-s -k <이름>"`.
- **`ASGITransport`는 앱의 처리되지 않은 예외를 응답(500)으로 바꾸지 않고 테스트로 올려 보낸다** (`raise_app_exceptions=True`). 그래서 red가 `assert 500 == 404`가 아니라 `NoResultFound`로 보인다.
