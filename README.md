# Metal Availability

A small public dashboard that records Scaleway Elastic Metal offer stock every hour and ranks configurations by historical availability. It uses the authenticated Scaleway API, FastAPI, server-rendered Jinja templates, HTMX, and SQLite.

## What it tracks

- Hourly Elastic Metal offers in the Paris, Amsterdam, and Warsaw regions.
- Strict availability: only an enabled offer with `stock=available` is available. `low`, `empty`, and disabled offers are not.
- Regional availability: percentages are calculated from zone-hours across every configured zone. If a server is available in only one of two successfully checked zones, that regional hour contributes 50%.
- The homepage lists each server configuration once. Its score combines all regions by default, or only the zones selected by the region and zone filters; 100% means every valid zone-hour in that scope was available.
- Hardware specifications, Scaleway commercial range/category, current stock, and both hourly and monthly advertised prices.
- Rolling 7-day, rolling 30-day, and all-history percentages. Missing collections and API failures are excluded rather than counted as downtime.

## Run locally

Python 3.12 and [uv](https://docs.astral.sh/uv/) are recommended.

```console
cp .env.example .env
# Put your Scaleway secret key in .env
uv sync
uv run --env-file .env alembic upgrade head
uv run --env-file .env uvicorn app.main:app --reload --workers 1
```

Open <http://localhost:8000>. The first collection starts on boot when the current hour has no data; later collections run at the top of every UTC hour.

The application does not load `.env` files itself. Use `uv run --env-file`, export variables in your shell, or provide them through your service/container environment.

Run checks with:

```console
uv run pytest
uv run ruff check .
```

## Docker

```console
export SCW_SECRET_KEY=your-secret-key
docker compose up --build -d
```

The Compose volume `availability-data` contains the SQLite database. The application intentionally runs one Uvicorn worker because the cron scheduler is embedded in the web process.

Back up the database by stopping the container briefly and copying the volume, or use SQLite's online backup command from inside the container. Historical data begins with the first successful collection; Scaleway does not provide an availability-history backfill.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCW_SECRET_KEY` | unset | Scaleway API token; collection is skipped when absent |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/availability.db` | SQLAlchemy database URL |
| `SCW_ZONES` | six current Elastic Metal zones | Comma-separated zones to poll |
| `ENABLE_SCHEDULER` | `true` | Enable the embedded hourly scheduler |
| `REQUEST_TIMEOUT_SECONDS` | `15` | Timeout for each API request |
| `REQUEST_RETRIES` | `3` | Attempts per zone collection |
| `STALE_AFTER_HOURS` | `2` | Age at which the UI reports stale data |

The scheduler management router supplied by `fastapi-crons` is deliberately not mounted, since the dashboard is public and read-only.

## Data and failure behavior

Every collection has per-zone status records. Successful zones are committed even if another zone fails. Failed zone checks are unknown and excluded from percentages; a missing offer in a successfully checked zone counts as unavailable. Derived regional observations are `available` when all successfully checked zones are available, `partial` when only some are available, and `unavailable` when none are available.

Collections are idempotent by UTC hour. Re-running an hour replaces that run's observations without duplicating history. Offers missing from a successful catalog response are marked inactive but are not recorded as unavailable, and their history remains queryable through the “Include inactive offers” filter.
