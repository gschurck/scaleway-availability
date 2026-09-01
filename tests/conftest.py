from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        scw_secret_key="test-secret",
        scw_zones="fr-par-1,fr-par-2",
        enable_scheduler=False,
        request_retries=1,
        app_env="test",
    )


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings)
    await db.create_schema()
    yield db
    await db.dispose()


def offer_payload(
    *,
    offer_id: str = "offer-a",
    name: str = "EM-A100",
    stock: str = "available",
    enabled: bool = True,
    category: str = "beryllium",
    cores: int = 16,
    ram_gib: int = 64,
    storage_gib: int = 960,
    disk_type: str = "nvme",
    hourly_units: int = 1,
    hourly_nanos: int = 250_000_000,
    monthly_units: int = 699,
) -> dict[str, object]:
    return {
        "id": offer_id,
        "name": name,
        "stock": stock,
        "enable": enabled,
        "commercial_range": category,
        "subscription_period": "hourly",
        "bandwidth": 1_000_000_000,
        "max_bandwidth": 2_000_000_000,
        "cpus": [
            {
                "name": "AMD EPYC Test",
                "core_count": cores,
                "thread_count": cores * 2,
                "frequency": 3200,
            }
        ],
        "memories": [{"capacity": ram_gib * 1024**3, "type": "ddr5", "frequency": 4800}],
        "disks": [{"capacity": storage_gib * 1024**3, "type": disk_type}],
        "gpus": [],
        "price_per_hour": {
            "currency_code": "EUR",
            "units": hourly_units,
            "nanos": hourly_nanos,
        },
        "price_per_month": {
            "currency_code": "EUR",
            "units": monthly_units,
            "nanos": 0,
        },
    }
