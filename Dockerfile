FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV DATABASE_URL=sqlite+aiosqlite:////data/availability.db

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev

RUN mkdir -p /data && chmod 0777 /data

EXPOSE 8000

CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && exec uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
