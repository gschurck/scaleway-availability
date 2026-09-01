from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.collector import AvailabilityCollector
from app.config import Settings, get_settings
from app.dashboard import DashboardService
from app.db import Database
from app.scaleway import ScalewayClient
from app.scheduler import configure_scheduler
from app.web import register_template_helpers, router

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = Database(settings)
    collector = AvailabilityCollector(settings, database, ScalewayClient(settings))
    bootstrap_task: asyncio.Task[object] | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal bootstrap_task
        await database.create_schema()
        if settings.enable_scheduler and settings.scw_secret_key:
            if not await collector.current_hour_is_collected():
                bootstrap_task = asyncio.create_task(
                    collector.collect(), name="initial-availability-collection"
                )
        yield
        if bootstrap_task and not bootstrap_task.done():
            bootstrap_task.cancel()
            await asyncio.gather(bootstrap_task, return_exceptions=True)
        await database.dispose()

    app = FastAPI(
        title=settings.app_name,
        description="Historical availability for Scaleway Elastic Metal offers",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    register_template_helpers(templates)
    app.state.settings = settings
    app.state.database = database
    app.state.collector = collector
    app.state.dashboard = DashboardService(settings)
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.include_router(router)
    if settings.enable_scheduler and settings.scw_secret_key:
        app.state.crons = configure_scheduler(app, collector, database)
    return app


app = create_app()
