# Container image for Google Cloud Run (see DEPLOYING.md). Config comes from env vars, not .env.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

RUN useradd -m -u 1000 user && mkdir /home/user/app && chown user:user /home/user/app
USER user
WORKDIR /home/user/app

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

COPY --chown=user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=user . .
RUN uv sync --frozen --no-dev && mkdir -p uploads

# Cloud Run sets PORT (8080).
CMD exec uv run --no-sync uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips "*"
