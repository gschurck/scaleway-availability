from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.collector import AvailabilityCollector
from app.models import (
    AvailabilityObservation,
    CollectionRun,
    OfferLocation,
    RegionalObservation,
    ServerType,
    ZoneCollectionRun,
)
from app.scaleway import ZoneFetchResult
from tests.conftest import offer_payload


class FakeClient:
    def __init__(self, results: dict[str, ZoneFetchResult]):
        self.results = results

    async def fetch_zone_offers(self, zone: str) -> ZoneFetchResult:
        return self.results[zone]


def success(zone: str, *offers: dict[str, object]) -> ZoneFetchResult:
    return ZoneFetchResult(zone, list(offers), 0.01)


def failure(zone: str) -> ZoneFetchResult:
    return ZoneFetchResult(zone, [], 0.01, "upstream timeout")


async def test_collection_persists_prices_category_and_regional_availability(
    settings, database
) -> None:
    client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload(stock="available")),
            "fr-par-2": success("fr-par-2", offer_payload(stock="low")),
        }
    )
    collector = AvailabilityCollector(settings, database, client)  # type: ignore[arg-type]
    when = datetime(2026, 8, 25, 10, 37, tzinfo=UTC)

    run = await collector.collect(when)

    async with database.session_factory() as session:
        server = await session.scalar(select(ServerType))
        locations = list((await session.scalars(select(OfferLocation))).all())
        regional = await session.scalar(select(RegionalObservation))
        assert run.status == "success"
        assert server is not None and server.commercial_range == "beryllium"
        assert len(locations) == 2
        assert locations[0].monthly_price_nanos == 699_000_000_000
        assert regional is not None and regional.state == "partial"
        assert regional.observed_at.hour == 10 and regional.observed_at.minute == 0


async def test_failed_zones_are_excluded_from_regional_state(settings, database) -> None:
    when = datetime(2026, 8, 25, 11, tzinfo=UTC)
    unavailable_client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload(stock="low")),
            "fr-par-2": failure("fr-par-2"),
        }
    )
    collector = AvailabilityCollector(settings, database, unavailable_client)  # type: ignore[arg-type]
    await collector.collect(when)

    available_client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload(stock="available")),
            "fr-par-2": failure("fr-par-2"),
        }
    )
    collector.client = available_client  # type: ignore[assignment]
    await collector.collect(when + timedelta(hours=1))

    async with database.session_factory() as session:
        states = list(
            (
                await session.scalars(
                    select(RegionalObservation.state).order_by(RegionalObservation.observed_at)
                )
            ).all()
        )
        zone_failures = await session.scalar(
            select(func.count(ZoneCollectionRun.id)).where(ZoneCollectionRun.status == "failed")
        )
        assert states == ["unavailable", "available"]
        assert zone_failures == 2


async def test_all_successful_zones_without_full_stock_are_unavailable(settings, database) -> None:
    client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload(stock="low")),
            "fr-par-2": success("fr-par-2", offer_payload(stock="empty")),
        }
    )
    collector = AvailabilityCollector(settings, database, client)  # type: ignore[arg-type]
    await collector.collect(datetime(2026, 8, 25, 13, tzinfo=UTC))

    async with database.session_factory() as session:
        state = await session.scalar(select(RegionalObservation.state))
        available_count = await session.scalar(
            select(func.count(AvailabilityObservation.id)).where(
                AvailabilityObservation.is_available.is_(True)
            )
        )
        assert state == "unavailable"
        assert available_count == 0


async def test_same_hour_rerun_is_idempotent_and_replaces_state(settings, database) -> None:
    when = datetime(2026, 8, 25, 14, tzinfo=UTC)
    client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload(stock="available")),
            "fr-par-2": success("fr-par-2", offer_payload(stock="available")),
        }
    )
    collector = AvailabilityCollector(settings, database, client)  # type: ignore[arg-type]
    await collector.collect(when)
    client.results = {
        "fr-par-1": success("fr-par-1", offer_payload(stock="empty")),
        "fr-par-2": success("fr-par-2", offer_payload(stock="empty")),
    }
    await collector.collect(when)

    async with database.session_factory() as session:
        assert await session.scalar(select(func.count(CollectionRun.id))) == 1
        assert await session.scalar(select(func.count(AvailabilityObservation.id))) == 2
        assert await session.scalar(select(func.count(RegionalObservation.id))) == 1
        assert await session.scalar(select(RegionalObservation.state)) == "unavailable"


async def test_missing_offer_is_marked_inactive_without_fake_observation(
    settings, database
) -> None:
    when = datetime(2026, 8, 25, 15, tzinfo=UTC)
    client = FakeClient(
        {
            "fr-par-1": success("fr-par-1", offer_payload()),
            "fr-par-2": success("fr-par-2", offer_payload()),
        }
    )
    collector = AvailabilityCollector(settings, database, client)  # type: ignore[arg-type]
    await collector.collect(when)
    client.results = {
        "fr-par-1": success("fr-par-1"),
        "fr-par-2": success("fr-par-2"),
    }
    await collector.collect(when + timedelta(hours=1))

    async with database.session_factory() as session:
        active_count = await session.scalar(
            select(func.count(OfferLocation.id)).where(OfferLocation.active.is_(True))
        )
        observation_count = await session.scalar(select(func.count(AvailabilityObservation.id)))
        assert active_count == 0
        assert observation_count == 2
