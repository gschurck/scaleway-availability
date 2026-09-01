from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import Database
from app.locations import region_for_zone
from app.models import (
    AvailabilityObservation,
    CollectionRun,
    OfferLocation,
    RegionalObservation,
    ServerType,
    ZoneCollectionRun,
)
from app.scaleway import NormalizedOffer, ScalewayClient, ZoneFetchResult, normalize_offer

logger = logging.getLogger(__name__)


def hour_bucket(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


class AvailabilityCollector:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        client: ScalewayClient | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.client = client or ScalewayClient(settings)
        self._lock = asyncio.Lock()

    async def current_hour_is_collected(self) -> bool:
        bucket = hour_bucket()
        async with self.database.session_factory() as session:
            return (
                await session.scalar(
                    select(CollectionRun.id).where(
                        CollectionRun.scheduled_at == bucket,
                        CollectionRun.status.in_(("success", "partial")),
                    )
                )
                is not None
            )

    async def collect(self, scheduled_at: datetime | None = None) -> CollectionRun:
        bucket = hour_bucket(scheduled_at)
        async with self._lock:
            fetch_results = await asyncio.gather(
                *(self.client.fetch_zone_offers(zone) for zone in self.settings.zones)
            )
            async with self.database.session_factory() as session:
                run = await self._get_or_create_run(session, bucket)
                await self._replace_run_data(session, run, fetch_results, bucket)
                await session.commit()
                await session.refresh(run)
                logger.info(
                    "availability collection completed",
                    extra={
                        "run_id": run.id,
                        "status": run.status,
                        "scheduled_at": bucket.isoformat(),
                    },
                )
                return run

    async def _get_or_create_run(self, session: AsyncSession, bucket: datetime) -> CollectionRun:
        run = await session.scalar(
            select(CollectionRun).where(CollectionRun.scheduled_at == bucket)
        )
        if run is None:
            run = CollectionRun(scheduled_at=bucket, started_at=datetime.now(UTC), status="running")
            session.add(run)
            await session.flush()
        else:
            run.started_at = datetime.now(UTC)
            run.finished_at = None
            run.status = "running"
        return run

    async def _replace_run_data(
        self,
        session: AsyncSession,
        run: CollectionRun,
        results: list[ZoneFetchResult],
        bucket: datetime,
    ) -> None:
        await session.execute(
            delete(RegionalObservation).where(RegionalObservation.collection_run_id == run.id)
        )
        await session.execute(
            delete(AvailabilityObservation).where(
                AvailabilityObservation.collection_run_id == run.id
            )
        )
        await session.execute(
            delete(ZoneCollectionRun).where(ZoneCollectionRun.collection_run_id == run.id)
        )

        successful_zones: set[str] = set()
        seen_by_region: dict[str, dict[int, dict[str, bool]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        for result in results:
            zone_run = ZoneCollectionRun(
                collection_run_id=run.id,
                zone=result.zone,
                status="success" if result.succeeded else "failed",
                offer_count=len(result.offers),
                latency_seconds=result.latency_seconds,
                error_message=result.error,
            )
            session.add(zone_run)
            if not result.succeeded:
                logger.warning(
                    "zone collection failed", extra={"zone": result.zone, "error": result.error}
                )
                continue

            successful_zones.add(result.zone)
            normalized = [normalize_offer(offer) for offer in result.offers]
            await self._persist_zone(session, run, result.zone, bucket, normalized, seen_by_region)

        await self._persist_regional_observations(
            session, run, bucket, successful_zones, seen_by_region
        )
        run.finished_at = datetime.now(UTC)
        if len(successful_zones) == len(self.settings.zones):
            run.status = "success"
        elif successful_zones:
            run.status = "partial"
        else:
            run.status = "failed"

    async def _persist_zone(
        self,
        session: AsyncSession,
        run: CollectionRun,
        zone: str,
        bucket: datetime,
        offers: list[NormalizedOffer],
        seen_by_region: dict[str, dict[int, dict[str, bool]]],
    ) -> None:
        region = region_for_zone(zone)
        existing_active = list(
            (
                await session.scalars(
                    select(OfferLocation).where(
                        OfferLocation.zone == zone, OfferLocation.active.is_(True)
                    )
                )
            ).all()
        )
        seen_location_ids: set[int] = set()

        for offer in offers:
            server_type = await self._upsert_server_type(session, offer)
            location = await session.scalar(
                select(OfferLocation).where(
                    OfferLocation.api_offer_id == offer.api_offer_id,
                    OfferLocation.zone == zone,
                    OfferLocation.server_type_id == server_type.id,
                )
            )
            if location is None:
                location = OfferLocation(
                    server_type_id=server_type.id,
                    api_offer_id=offer.api_offer_id,
                    zone=zone,
                    region=region,
                    first_seen_at=bucket,
                    last_seen_at=bucket,
                )
                session.add(location)
                await session.flush()
            location.active = True
            location.enabled = offer.enabled
            location.current_stock = offer.stock
            location.hourly_currency = offer.hourly_price.currency
            location.hourly_price_nanos = offer.hourly_price.nanos
            location.monthly_currency = offer.monthly_price.currency
            location.monthly_price_nanos = offer.monthly_price.nanos
            location.last_seen_at = bucket
            seen_location_ids.add(location.id)

            session.add(
                AvailabilityObservation(
                    collection_run_id=run.id,
                    offer_location_id=location.id,
                    observed_at=bucket,
                    stock=offer.stock,
                    enabled=offer.enabled,
                    is_available=offer.is_available,
                )
            )
            current = seen_by_region[region][server_type.id].get(zone, False)
            seen_by_region[region][server_type.id][zone] = current or offer.is_available

        for location in existing_active:
            if location.id not in seen_location_ids:
                location.active = False

    async def _upsert_server_type(
        self, session: AsyncSession, offer: NormalizedOffer
    ) -> ServerType:
        server_type = await session.scalar(
            select(ServerType).where(
                ServerType.name == offer.name,
                ServerType.hardware_fingerprint == offer.hardware_fingerprint,
            )
        )
        if server_type is None:
            server_type = ServerType(
                name=offer.name,
                hardware_fingerprint=offer.hardware_fingerprint,
            )
            session.add(server_type)
            await session.flush()
        server_type.commercial_range = offer.commercial_range
        server_type.total_cores = offer.total_cores
        server_type.total_threads = offer.total_threads
        server_type.ram_bytes = offer.ram_bytes
        server_type.storage_bytes = offer.storage_bytes
        server_type.storage_types = offer.storage_types
        server_type.has_gpu = offer.has_gpu
        server_type.public_bandwidth_bps = offer.public_bandwidth_bps
        server_type.specs_json = offer.specs
        return server_type

    async def _persist_regional_observations(
        self,
        session: AsyncSession,
        run: CollectionRun,
        bucket: datetime,
        successful_zones: set[str],
        seen_by_region: dict[str, dict[int, dict[str, bool]]],
    ) -> None:
        configured_by_region: dict[str, set[str]] = defaultdict(set)
        for zone in self.settings.zones:
            configured_by_region[region_for_zone(zone)].add(zone)

        for region, server_samples in seen_by_region.items():
            valid_zones = configured_by_region[region] & successful_zones
            for server_type_id, samples_by_zone in server_samples.items():
                available_zones = sum(samples_by_zone.get(zone, False) for zone in valid_zones)
                if available_zones == len(valid_zones):
                    state = "available"
                elif available_zones == 0:
                    state = "unavailable"
                else:
                    state = "partial"
                session.add(
                    RegionalObservation(
                        collection_run_id=run.id,
                        server_type_id=server_type_id,
                        region=region,
                        observed_at=bucket,
                        state=state,
                    )
                )
