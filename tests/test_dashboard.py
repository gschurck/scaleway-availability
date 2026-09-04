from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.config import Settings
from app.dashboard import DashboardService, RankingFilters
from app.main import create_app
from app.models import (
    AvailabilityObservation,
    CollectionRun,
    OfferLocation,
    RegionalObservation,
    ServerType,
    ZoneCollectionRun,
)


async def seed_ranked_server(database, *, sample_count: int = 24) -> ServerType:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    async with database.session_factory() as session:
        server = ServerType(
            name="EM-BERYLLIUM-1",
            hardware_fingerprint="a" * 64,
            commercial_range="beryllium",
            total_cores=32,
            total_threads=64,
            ram_bytes=128 * 1024**3,
            storage_bytes=1920 * 1024**3,
            storage_types="nvme",
            has_gpu=False,
            public_bandwidth_bps=1_000_000_000,
            specs_json={
                "cpus": [{"name": "AMD EPYC", "core_count": 32, "thread_count": 64}],
                "disks": [{"capacity": 960 * 1024**3, "type": "nvme"}] * 2,
                "gpus": [],
            },
        )
        session.add(server)
        await session.flush()
        location = OfferLocation(
            server_type_id=server.id,
            api_offer_id="offer-ranked",
            zone="fr-par-1",
            region="fr-par",
            active=True,
            enabled=True,
            current_stock="available",
            hourly_currency="EUR",
            hourly_price_nanos=1_500_000_000,
            monthly_currency="EUR",
            monthly_price_nanos=799_000_000_000,
            first_seen_at=now - timedelta(hours=sample_count - 1),
            last_seen_at=now,
        )
        session.add(location)
        for index in range(sample_count):
            observed_at = now - timedelta(hours=sample_count - index - 1)
            run = CollectionRun(
                scheduled_at=observed_at,
                started_at=observed_at,
                finished_at=observed_at + timedelta(seconds=2),
                status="success",
            )
            session.add(run)
            await session.flush()
            session.add_all(
                [
                    ZoneCollectionRun(
                        collection_run_id=run.id,
                        zone="fr-par-1",
                        status="success",
                        offer_count=1,
                        latency_seconds=0.1,
                    ),
                    ZoneCollectionRun(
                        collection_run_id=run.id,
                        zone="fr-par-2",
                        status="success",
                        offer_count=0,
                        latency_seconds=0.1,
                    ),
                ]
            )
            session.add(
                RegionalObservation(
                    collection_run_id=run.id,
                    server_type_id=server.id,
                    region="fr-par",
                    observed_at=observed_at,
                    state="available" if index < sample_count - 4 else "unavailable",
                )
            )
            session.add(
                AvailabilityObservation(
                    collection_run_id=run.id,
                    offer_location_id=location.id,
                    observed_at=observed_at,
                    stock=(
                        "available"
                        if index < sample_count - 4
                        else "low"
                        if index == sample_count - 1
                        else "empty"
                    ),
                    enabled=True,
                    is_available=index < sample_count - 4,
                )
            )
        await session.commit()
        return server


async def test_rankings_require_24_samples_and_apply_category_price_filters(
    settings, database
) -> None:
    server = await seed_ranked_server(database)
    dashboard = DashboardService(settings)
    async with database.session_factory() as session:
        results = await dashboard.rankings(
            session,
            RankingFilters(
                region="fr-par",
                category="beryllium",
                min_cores=16,
                min_ram_gb=64,
                disk_type="nvme",
                max_hourly_price_eur="2.00",
                max_monthly_price_eur="800",
            ),
        )
        categories = await dashboard.categories(session)
        bounds = await dashboard.filter_bounds(session)

    assert [item.server_type.id for item in results.ranked] == [server.id]
    assert results.ranked[0].availability_percent == 20 / 48 * 100
    assert results.ranked[0].available_samples == 20
    assert results.ranked[0].valid_samples == 48
    assert results.ranked[0].price.monthly_nanos == 799_000_000_000
    assert categories == [("beryllium", "Beryllium")]
    assert bounds.storage_gb_max == 1920
    assert bounds.hourly_price_eur_max == 2
    assert bounds.monthly_price_eur_max == 799

    async with database.session_factory() as session:
        no_results = await dashboard.rankings(
            session,
            RankingFilters(region="fr-par", category="titanium"),
        )
    assert no_results.ranked == []
    assert no_results.new == []

    async with database.session_factory() as session:
        over_monthly_budget = await dashboard.rankings(
            session,
            RankingFilters(region="fr-par", max_monthly_price_eur="700"),
        )
    assert over_monthly_budget.ranked == []
    assert over_monthly_budget.new == []


async def test_server_with_23_samples_is_listed_as_new(settings, database) -> None:
    await seed_ranked_server(database, sample_count=23)
    async with database.session_factory() as session:
        results = await DashboardService(settings).rankings(
            session, RankingFilters(region="fr-par")
        )
    assert results.ranked == []
    assert len(results.new) == 1


async def test_rankings_can_sort_by_availability_or_price(settings, database) -> None:
    available_server = await seed_ranked_server(database)
    async with database.session_factory() as session:
        runs = list(
            (
                await session.scalars(
                    select(CollectionRun).order_by(CollectionRun.scheduled_at)
                )
            ).all()
        )
        cheaper_server = ServerType(
            name="EM-CHEAPER",
            hardware_fingerprint="b" * 64,
            commercial_range="beryllium",
            total_cores=16,
            total_threads=32,
            ram_bytes=64 * 1024**3,
            storage_bytes=960 * 1024**3,
            storage_types="nvme",
            has_gpu=False,
            public_bandwidth_bps=1_000_000_000,
            specs_json={},
        )
        session.add(cheaper_server)
        await session.flush()
        location = OfferLocation(
            server_type_id=cheaper_server.id,
            api_offer_id="offer-cheaper",
            zone="fr-par-1",
            region="fr-par",
            active=True,
            enabled=True,
            current_stock="empty",
            hourly_currency="EUR",
            hourly_price_nanos=500_000_000,
            monthly_currency="EUR",
            monthly_price_nanos=300_000_000_000,
            first_seen_at=runs[0].scheduled_at,
            last_seen_at=runs[-1].scheduled_at,
        )
        session.add(location)
        await session.flush()
        session.add_all(
            AvailabilityObservation(
                collection_run_id=run.id,
                offer_location_id=location.id,
                observed_at=run.scheduled_at,
                stock="empty",
                enabled=True,
                is_available=False,
            )
            for run in runs
        )
        await session.commit()

    dashboard = DashboardService(settings)
    async with database.session_factory() as session:
        by_availability = await dashboard.rankings(
            session, RankingFilters(region="fr-par", sort_by="availability")
        )
        by_price = await dashboard.rankings(
            session, RankingFilters(region="fr-par", sort_by="price")
        )

    assert [item.server_type.id for item in by_availability.ranked] == [
        available_server.id,
        cheaper_server.id,
    ]
    assert [item.server_type.id for item in by_price.ranked] == [
        cheaper_server.id,
        available_server.id,
    ]


async def test_all_regions_returns_each_server_once_with_combined_availability(
    settings, database
) -> None:
    server = await seed_ranked_server(database)
    async with database.session_factory() as session:
        runs = list((await session.scalars(select(CollectionRun))).all())
        for run in runs:
            session.add_all(
                [
                    ZoneCollectionRun(
                        collection_run_id=run.id,
                        zone="nl-ams-1",
                        status="success",
                        offer_count=0,
                    ),
                    ZoneCollectionRun(
                        collection_run_id=run.id,
                        zone="nl-ams-2",
                        status="success",
                        offer_count=0,
                    ),
                ]
            )
        await session.commit()

    all_regions_settings = settings.model_copy(
        update={"scw_zones": "fr-par-1,fr-par-2,nl-ams-1,nl-ams-2"}
    )
    async with database.session_factory() as session:
        results = await DashboardService(all_regions_settings).rankings(session, RankingFilters())

    assert results.label == "All regions"
    assert [item.server_type.id for item in results.ranked] == [server.id]
    assert results.ranked[0].available_samples == 20
    assert results.ranked[0].valid_samples == 96
    assert results.ranked[0].availability_percent == 20 / 96 * 100


async def test_public_pages_render_monthly_price_categories_and_htmx_history(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'web.db'}",
        scw_zones="fr-par-1,fr-par-2",
        enable_scheduler=False,
        app_env="test",
    )
    app = create_app(settings)
    await app.state.database.create_schema()
    server = await seed_ranked_server(app.state.database)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        homepage = await client.get("/")
        partial = await client.get(
            "/partials/rankings",
            params={
                "region": "fr-par",
                "category": "beryllium",
                "timeframe": "30d",
                "sort_by": "price",
            },
            headers={"HX-Request": "true"},
        )
        empty_filter_partial = await client.get(
            "/partials/rankings",
            params={
                "timeframe": "30d",
                "region": "",
                "zone": "fr-par-1",
                "category": "",
                "min_cores": "",
                "min_ram_gb": "",
                "disk_type": "",
                "min_storage_gb": "",
                "gpu": "any",
                "max_hourly_price_eur": "",
                "max_monthly_price_eur": "",
            },
            headers={"HX-Request": "true"},
        )
        invalid_filter_partial = await client.get("/partials/rankings", params={"min_cores": "0"})
        zero_storage_partial = await client.get(
            "/partials/rankings", params={"min_storage_gb": "0"}
        )
        detail = await client.get(f"/servers/{server.id}?region=fr-par&timeframe=30d")
        zone_detail = await client.get(
            f"/servers/{server.id}?zone=fr-par-1&timeframe=30d"
        )
        detail_timeframe_partial = await client.get(
            f"/servers/{server.id}?region=fr-par&timeframe=7d",
            headers={"HX-Request": "true", "HX-Target": "availability-data"},
        )
        health = await client.get("/healthz")

    assert homepage.status_code == 200
    assert "EM-BERYLLIUM-1" in homepage.text
    assert homepage.text.count("EM-BERYLLIUM-1") == 1
    assert "€799.00/mo" in homepage.text
    assert "Beryllium" in homepage.text
    assert "htmx.min.js" in homepage.text
    assert "theme.js" in homepage.text
    assert "data-theme-toggle" in homepage.text
    assert '<body hx-boost="true">' in homepage.text
    assert 'href="/healthz" hx-boost="false"' in homepage.text
    assert 'hx-trigger="input delay:300ms"' in homepage.text
    assert 'name="sort_by"' in homepage.text
    assert "Availability — highest first" in homepage.text
    assert "Price — lowest first" in homepage.text
    assert "Apply filters" not in homepage.text
    assert 'name="min_storage_gb"' in homepage.text
    assert 'id="min-storage-range"' in homepage.text
    assert 'type="range"' in homepage.text
    assert 'max="1920"' in homepage.text
    assert 'id="max-price-range"' in homepage.text
    assert "€2.00/h" in homepage.text
    assert 'name="max_monthly_price_eur"' in homepage.text
    assert 'id="max-monthly-price-range"' in homepage.text
    assert 'max="799"' in homepage.text
    assert "Maximum monthly price" in homepage.text
    assert partial.status_code == 200
    assert partial.headers["HX-Push-Url"].startswith("/?")
    assert "<html" not in partial.text
    assert empty_filter_partial.status_code == 200
    assert "EM-BERYLLIUM-1" in empty_filter_partial.text
    assert invalid_filter_partial.status_code == 422
    assert zero_storage_partial.status_code == 200
    assert detail.status_code == 200
    assert "Availability history" in detail.text
    assert "Availability by zone" in detail.text
    assert "fr-par-1" in detail.text
    assert "fr-par-2" in detail.text
    assert "Not offered in this zone" in detail.text
    assert "timeline-available" in detail.text
    assert "timeline-low" in detail.text
    assert "timeline-partial" in detail.text
    assert "timeline-low" in zone_detail.text
    assert "Low stock" in zone_detail.text
    assert "low stock" in zone_detail.text
    assert 'id="availability-data"' in detail.text
    assert 'hx-target="#availability-data"' in detail.text
    assert detail_timeframe_partial.status_code == 200
    assert '<section id="availability-data"' in detail_timeframe_partial.text
    assert "<html" not in detail_timeframe_partial.text
    assert "EM-BERYLLIUM-1" not in detail_timeframe_partial.text
    assert 'aria-current="page">7 days</a>' in detail_timeframe_partial.text
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    await app.state.database.dispose()


async def test_inactive_offer_is_hidden_by_default(settings, database) -> None:
    server = await seed_ranked_server(database)
    async with database.session_factory() as session:
        location = await session.scalar(
            select(OfferLocation).where(OfferLocation.server_type_id == server.id)
        )
        location.active = False
        await session.commit()
    dashboard = DashboardService(settings)
    async with database.session_factory() as session:
        hidden = await dashboard.rankings(session, RankingFilters(region="fr-par"))
        visible = await dashboard.rankings(
            session, RankingFilters(region="fr-par", include_inactive=True)
        )
    assert hidden.ranked == []
    assert len(visible.ranked) == 1

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        hidden_response = await client.get(
            "/partials/rankings",
            params={"region": "fr-par"},
            headers={"HX-Request": "true"},
        )
        visible_response = await client.get(
            "/partials/rankings",
            params={"region": "fr-par", "include_inactive": "true"},
            headers={"HX-Request": "true"},
        )

    assert "EM-BERYLLIUM-1" not in hidden_response.text
    assert "EM-BERYLLIUM-1" in visible_response.text
    await app.state.database.dispose()
