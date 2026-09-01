from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard import DashboardService, RankingFilters, Timeframe
from app.db import Database
from app.locations import category_label, region_label

router = APIRouter()


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_dashboard(request: Request) -> DashboardService:
    return request.app.state.dashboard


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncSession:
    async with database.session_factory() as session:
        yield session


def optional_positive_int(value: str | None, label: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be a whole number") from exc
    if parsed < 1:
        raise HTTPException(status_code=422, detail=f"{label} must be at least 1")
    return parsed


def optional_nonnegative_int(value: str | None, label: str) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be a whole number") from exc
    if parsed < 0:
        raise HTTPException(status_code=422, detail=f"{label} cannot be negative")
    return parsed or None


def parse_filters(
    timeframe: Annotated[Literal["7d", "30d", "all"], Query()] = "30d",
    region: Annotated[str | None, Query()] = None,
    zone: Annotated[str | None, Query()] = None,
    min_cores: Annotated[str | None, Query()] = None,
    min_ram_gb: Annotated[str | None, Query()] = None,
    disk_type: Annotated[str | None, Query()] = None,
    min_storage_gb: Annotated[str | None, Query()] = None,
    gpu: Annotated[Literal["any", "yes", "no"], Query()] = "any",
    max_hourly_price_eur: Annotated[str | None, Query()] = None,
    max_monthly_price_eur: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> RankingFilters:
    return RankingFilters(
        timeframe=timeframe,
        region=region or None,
        zone=zone or None,
        min_cores=optional_positive_int(min_cores, "Minimum cores"),
        min_ram_gb=optional_positive_int(min_ram_gb, "Minimum RAM"),
        disk_type=disk_type or None,
        min_storage_gb=optional_nonnegative_int(min_storage_gb, "Minimum storage"),
        gpu=gpu,
        max_hourly_price_eur=max_hourly_price_eur or None,
        max_monthly_price_eur=max_monthly_price_eur or None,
        category=category or None,
        include_inactive=include_inactive,
    )


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    filters: Annotated[RankingFilters, Depends(parse_filters)],
    session: Annotated[AsyncSession, Depends(get_session)],
    dashboard: Annotated[DashboardService, Depends(get_dashboard)],
) -> HTMLResponse:
    try:
        results = await dashboard.rankings(session, filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    categories = await dashboard.categories(session)
    filter_bounds = await dashboard.filter_bounds(session)
    latest_success = await dashboard.latest_success(session)
    now = datetime.now(UTC)
    stale = (
        latest_success is None
        or (now - latest_success).total_seconds()
        > request.app.state.settings.stale_after_hours * 3600
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "index.html",
        {
            "filters": filters,
            "results": results,
            "categories": categories,
            "filter_bounds": filter_bounds,
            "zones": request.app.state.settings.zones,
            "latest_success": latest_success,
            "stale": stale,
        },
    )


@router.get("/partials/rankings", response_class=HTMLResponse)
async def ranking_partial(
    request: Request,
    filters: Annotated[RankingFilters, Depends(parse_filters)],
    session: Annotated[AsyncSession, Depends(get_session)],
    dashboard: Annotated[DashboardService, Depends(get_dashboard)],
) -> HTMLResponse:
    try:
        results = await dashboard.rankings(session, filters)
    except ValueError as exc:
        return request.app.state.templates.TemplateResponse(
            request,
            "partials/filter_error.html",
            {"message": str(exc)},
            status_code=422,
        )
    response = request.app.state.templates.TemplateResponse(
        request,
        "partials/rankings.html",
        {"filters": filters, "results": results},
    )
    query = request.url.query
    response.headers["HX-Push-Url"] = f"/?{query}" if query else "/"
    return response


@router.get("/servers/{server_type_id}", response_class=HTMLResponse)
async def server_detail(
    request: Request,
    server_type_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    dashboard: Annotated[DashboardService, Depends(get_dashboard)],
    timeframe: Timeframe = "30d",
    region: str | None = None,
    zone: str | None = None,
) -> HTMLResponse:
    detail = await dashboard.server_detail(session, server_type_id, timeframe, region, zone)
    if detail is None:
        raise HTTPException(status_code=404, detail="Server type not found")
    server, stats, timeline, zone_histories = detail
    selected_region = region or (stats[0].region if stats else None)
    template_name = (
        "partials/server_availability.html"
        if request.headers.get("HX-Target") == "availability-data"
        else "server_detail.html"
    )
    return request.app.state.templates.TemplateResponse(
        request,
        template_name,
        {
            "server": server,
            "stats": stats,
            "timeline": timeline,
            "zone_histories": zone_histories,
            "timeframe": timeframe,
            "selected_region": selected_region,
            "selected_zone": zone,
        },
    )


@router.get("/healthz")
async def health(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    dashboard: Annotated[DashboardService, Depends(get_dashboard)],
) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503)
    latest = await dashboard.latest_success(session)
    stale = (
        latest is None
        or (datetime.now(UTC) - latest).total_seconds()
        > request.app.state.settings.stale_after_hours * 3600
    )
    return JSONResponse(
        {
            "status": "degraded" if stale else "ok",
            "database": "ok",
            "collector": "stale" if stale else "ok",
            "last_success": latest.isoformat() if latest else None,
        }
    )


def format_bytes(value: int) -> str:
    if value >= 1024**4:
        return f"{value / 1024**4:.1f} TiB"
    if value >= 1024**3:
        return f"{value / 1024**3:.0f} GiB"
    return f"{value / 1024**2:.0f} MiB"


def format_bandwidth(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:g} Gbps"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g} Mbps"
    return f"{value:g} bps"


def format_money(currency: str | None, nanos: int | None) -> str:
    if currency is None or nanos is None:
        return "—"
    amount = Decimal(nanos) / Decimal(1_000_000_000)
    symbol = "€" if currency == "EUR" else f"{currency} "
    return f"{symbol}{amount:,.2f}"


def register_template_helpers(templates: object) -> None:
    templates.env.filters["bytes"] = format_bytes
    templates.env.filters["bandwidth"] = format_bandwidth
    templates.env.globals["money"] = format_money
    templates.env.globals["region_label"] = region_label
    templates.env.globals["category_label"] = category_label
