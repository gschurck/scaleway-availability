from __future__ import annotations

from app.config import Settings
from app.main import create_app


def test_scheduler_registers_hourly_job_without_public_management_routes(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}",
        scw_secret_key="configured",
        scw_zones="fr-par-1,fr-par-2",
        enable_scheduler=True,
        app_env="test",
    )
    app = create_app(settings)

    job = app.state.crons.get_job("collect_elastic_metal_availability")
    assert job is not None
    assert job.expr == "0 * * * *"
    assert not any(route.path.startswith("/crons") for route in app.routes)


def test_scheduler_is_not_started_without_api_credentials(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}",
        scw_secret_key=None,
        enable_scheduler=True,
        app_env="test",
    )
    app = create_app(settings)

    assert not hasattr(app.state, "crons")

