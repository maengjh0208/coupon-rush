# 베이스 이미지
FROM python:3.14-slim

# 작업 디렉토리
WORKDIR /app

# 가상환경 경로 변경 (가상환경이 만들어지는 경로를 /opt/venv 으로 바꾸고 python, pytest 등이 사용할 수 있게 함. uv sync 보다 먼저 실행되어야 함)
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 캐시 마운트를 쓰면 하드링크가 불가능하니 복사 모드로 설치
ENV UV_LINK_MODE=copy

# uv 설치
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

# 의존성 파일 복사
COPY pyproject.toml uv.lock ./

# 의존성 설치 (캐시 폴더를 이미지 밖의 Docker 빌드 캐시 공간에 연결함. 이미지에는 캐시 남지 않음. 다음 빌드에 재사용됨)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# 프로젝트 복사
COPY README.md ./
COPY ./src ./src

# 프로젝트 설치
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked
