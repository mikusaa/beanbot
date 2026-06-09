FROM ghcr.io/astral-sh/uv:python3.14-trixie AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    bison \
    flex \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.14-slim-trixie

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY beanbot ./beanbot

ENV PATH="/app/.venv/bin:${PATH}"
CMD ["python", "-m", "beanbot.app"]
