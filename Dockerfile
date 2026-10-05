FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy UV_NO_DEV=1 PYTHONUNBUFFERED=1

# Installing dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project


COPY . .
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-10000}"]