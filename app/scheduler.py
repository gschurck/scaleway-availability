from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi_crons import Crons
from fastapi_crons.state.sqlalchemy import SQLAlchemyStateBackend

from app.collector import AvailabilityCollector
from app.db import Database

logger = logging.getLogger(__name__)


def configure_scheduler(
    app: FastAPI, collector: AvailabilityCollector, database: Database
) -> Crons:
    state_backend = SQLAlchemyStateBackend(database.engine)
    crons = Crons(app, state_backend=state_backend)

    @crons.cron("0 * * * *", name="collect_elastic_metal_availability")
    async def collect_elastic_metal_availability() -> None:
        try:
            await collector.collect()
        except Exception:
            logger.exception("scheduled availability collection failed")

    return crons
