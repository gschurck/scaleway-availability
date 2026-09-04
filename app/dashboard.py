from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Literal

from sqlalchemy import Integer, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.locations import category_label, region_for_zone, region_label
from app.models import (
    AvailabilityObservation,
    CollectionRun,
    OfferLocation,
    ServerType,
    ZoneCollectionRun,
)
from app.scaleway import NANOS_PER_UNIT

Timeframe = Literal["7d", "30d", "all"]
SortBy = Literal["availability", "price"]


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def cutoff_for(timeframe: Timeframe, now: datetime | None = None) -> datetime | None:
    now = ensure_utc(now) or datetime.now(UTC)
    if timeframe == "7d":
        return now - timedelta(days=7)
    if timeframe == "30d":
        return now - timedelta(days=30)
    return None


def price_filter_nanos(value: str | None) -> int | None:
    if not value:
        return None
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Maximum price must be a number") from exc
    if decimal_value < 0:
        raise ValueError("Maximum price cannot be negative")
    return int(decimal_value * NANOS_PER_UNIT)


@dataclass(frozen=True)
class RankingFilters:
    timeframe: Timeframe = "30d"
    sort_by: SortBy = "availability"
    region: str | None = None
    zone: str | None = None
    min_cores: int | None = None
    min_ram_gb: int | None = None
    disk_type: str | None = None
    min_storage_gb: int | None = None
    gpu: str = "any"
    max_hourly_price_eur: str | None = None
    max_monthly_price_eur: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class PricePair:
    hourly_currency: str | None
    hourly_nanos: int | None
    monthly_currency: str | None
    monthly_nanos: int | None


@dataclass(frozen=True)
class LocationStock:
    zone: str
    stock: str
    enabled: bool
    active: bool


@dataclass
class RankedServer:
    server_type: ServerType
    region: str
    availability_percent: float | None
    available_samples: int
    valid_samples: int
    expected_samples: int
    coverage_percent: float
    eligible: bool
    price: PricePair
    stocks: list[LocationStock] = field(default_factory=list)


@dataclass
class RankingResults:
    scope: str
    label: str
    ranked: list[RankedServer]
    new: list[RankedServer]


@dataclass(frozen=True)
class TimelinePoint:
    timestamp: datetime
    label: str
    state: str
    percent: float | None


@dataclass(frozen=True)
class ZoneHistory:
    zone: str
    stats: RankedServer
    timeline: list[TimelinePoint]


@dataclass(frozen=True)
class FilterBounds:
    storage_gb_max: int
    hourly_price_eur_max: int
    monthly_price_eur_max: int


class DashboardService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def categories(self, session: AsyncSession) -> list[tuple[str, str]]:
        values = (
            await session.scalars(
                select(ServerType.commercial_range)
                .where(ServerType.commercial_range != "")
                .distinct()
                .order_by(ServerType.commercial_range)
            )
        ).all()
        return [(value, category_label(value)) for value in values]

    async def filter_bounds(self, session: AsyncSession) -> FilterBounds:
        storage_bytes = await session.scalar(select(func.max(ServerType.storage_bytes))) or 0
        hourly_price_nanos = (
            await session.scalar(select(func.max(OfferLocation.hourly_price_nanos))) or 0
        )
        monthly_price_nanos = (
            await session.scalar(select(func.max(OfferLocation.monthly_price_nanos))) or 0
        )
        return FilterBounds(
            storage_gb_max=max(1, ceil(storage_bytes / 1024**3)),
            hourly_price_eur_max=max(1, ceil(hourly_price_nanos / NANOS_PER_UNIT)),
            monthly_price_eur_max=max(1, ceil(monthly_price_nanos / NANOS_PER_UNIT)),
        )

    async def rankings(self, session: AsyncSession, filters: RankingFilters) -> RankingResults:
        zones = self._filter_zones(filters)
        candidates = await self._candidate_servers(session, filters, zones)
        servers = [
            await self._rank_server_scope(session, server, filters, zones) for server in candidates
        ]
        if filters.sort_by == "price":
            servers.sort(
                key=lambda item: (
                    item.price.hourly_nanos is None,
                    item.price.hourly_nanos if item.price.hourly_nanos is not None else 0,
                    -(item.availability_percent or 0),
                    item.server_type.name.lower(),
                )
            )
        else:
            servers.sort(
                key=lambda item: (
                    -(item.availability_percent if item.availability_percent is not None else -1),
                    -item.valid_samples,
                    item.server_type.name.lower(),
                )
            )
        if filters.zone:
            scope = filters.zone
            label = filters.zone
        elif filters.region:
            scope = filters.region
            label = region_label(filters.region)
        else:
            scope = "all"
            label = "All regions"
        return RankingResults(
            scope=scope,
            label=label,
            ranked=[server for server in servers if server.eligible],
            new=[server for server in servers if not server.eligible],
        )

    def _filter_zones(self, filters: RankingFilters) -> list[str]:
        if filters.zone:
            return [filters.zone]
        if filters.region:
            return self._region_zones(filters.region)
        return list(self.settings.zones)

    async def server_detail(
        self,
        session: AsyncSession,
        server_type_id: int,
        timeframe: Timeframe,
        region: str | None,
        zone: str | None,
    ) -> (
        tuple[
            ServerType,
            list[RankedServer],
            list[TimelinePoint],
            list[ZoneHistory],
        ]
        | None
    ):
        server = await session.get(ServerType, server_type_id)
        if server is None:
            return None
        location_regions = list(
            (
                await session.scalars(
                    select(OfferLocation.region)
                    .where(OfferLocation.server_type_id == server.id)
                    .distinct()
                    .order_by(OfferLocation.region)
                )
            ).all()
        )
        selected_region = region or (region_for_zone(zone) if zone else None)
        selected_region = selected_region or (location_regions[0] if location_regions else "fr-par")
        stats = [
            await self._rank_server(
                session,
                server,
                RankingFilters(timeframe=timeframe, region=item_region),
                item_region,
            )
            for item_region in location_regions
        ]
        timeline = await self._timeline(session, server.id, timeframe, selected_region, zone=zone)
        region_zones = [
            item for item in self.settings.zones if region_for_zone(item) == selected_region
        ]
        zone_histories = [
            ZoneHistory(
                zone=item_zone,
                stats=await self._rank_server(
                    session,
                    server,
                    RankingFilters(
                        timeframe=timeframe,
                        region=selected_region,
                        zone=item_zone,
                    ),
                    selected_region,
                    include_inactive=True,
                ),
                timeline=await self._timeline(
                    session,
                    server.id,
                    timeframe,
                    selected_region,
                    zone=item_zone,
                ),
            )
            for item_zone in region_zones
        ]
        return server, stats, timeline, zone_histories

    async def latest_success(self, session: AsyncSession) -> datetime | None:
        value = await session.scalar(
            select(func.max(ZoneCollectionRun.id)).where(ZoneCollectionRun.status == "success")
        )
        if value is None:
            return None
        from app.models import CollectionRun

        return ensure_utc(
            await session.scalar(
                select(CollectionRun.finished_at)
                .join(ZoneCollectionRun)
                .where(ZoneCollectionRun.id == value)
            )
        )

    async def _candidate_servers(
        self, session: AsyncSession, filters: RankingFilters, zones: list[str]
    ) -> list[ServerType]:
        if not zones:
            return []
        query: Select[tuple[ServerType]] = (
            select(ServerType).join(OfferLocation).where(OfferLocation.zone.in_(zones)).distinct()
        )
        query = query.where(OfferLocation.active.is_(True))
        if filters.min_cores is not None:
            query = query.where(ServerType.total_cores >= filters.min_cores)
        if filters.min_ram_gb is not None:
            query = query.where(ServerType.ram_bytes >= filters.min_ram_gb * 1024**3)
        if filters.disk_type:
            query = query.where(ServerType.storage_types.contains(filters.disk_type.lower()))
        if filters.min_storage_gb is not None:
            query = query.where(ServerType.storage_bytes >= filters.min_storage_gb * 1024**3)
        if filters.gpu == "yes":
            query = query.where(ServerType.has_gpu.is_(True))
        elif filters.gpu == "no":
            query = query.where(ServerType.has_gpu.is_(False))
        if filters.category:
            query = query.where(ServerType.commercial_range == filters.category.lower())
        max_price = price_filter_nanos(filters.max_hourly_price_eur)
        if max_price is not None:
            query = query.where(
                OfferLocation.hourly_currency == "EUR",
                OfferLocation.hourly_price_nanos <= max_price,
            )
        max_monthly_price = price_filter_nanos(filters.max_monthly_price_eur)
        if max_monthly_price is not None:
            query = query.where(
                OfferLocation.monthly_currency == "EUR",
                OfferLocation.monthly_price_nanos <= max_monthly_price,
            )
        return list((await session.scalars(query.order_by(ServerType.name))).unique().all())

    async def _rank_server_scope(
        self,
        session: AsyncSession,
        server: ServerType,
        filters: RankingFilters,
        zones: list[str],
    ) -> RankedServer:
        if filters.zone:
            return await self._rank_server(session, server, filters, region_for_zone(filters.zone))
        cutoff = cutoff_for(filters.timeframe)
        available, valid, first, valid_hours = await self._scope_stats(
            session, server.id, zones, cutoff
        )
        expected = self._expected_samples(first, cutoff) * len(zones)
        availability = (available / valid * 100) if valid else None
        coverage = min(100.0, valid / expected * 100) if expected else 0.0
        price, stocks = await self._location_summary(
            session, server.id, zones, include_inactive=False
        )
        return RankedServer(
            server_type=server,
            region=filters.region or "all",
            availability_percent=availability,
            available_samples=available,
            valid_samples=valid,
            expected_samples=expected,
            coverage_percent=coverage,
            eligible=valid_hours >= 24,
            price=price,
            stocks=stocks,
        )

    async def _rank_server(
        self,
        session: AsyncSession,
        server: ServerType,
        filters: RankingFilters,
        region: str,
        *,
        include_inactive: bool = False,
    ) -> RankedServer:
        cutoff = cutoff_for(filters.timeframe)
        if filters.zone:
            stats_query = (
                select(
                    func.count(AvailabilityObservation.id),
                    func.sum(func.cast(AvailabilityObservation.is_available, type_=Integer)),
                    func.min(AvailabilityObservation.observed_at),
                )
                .join(OfferLocation)
                .where(
                    OfferLocation.server_type_id == server.id,
                    OfferLocation.zone == filters.zone,
                )
            )
            if cutoff is not None:
                stats_query = stats_query.where(AvailabilityObservation.observed_at >= cutoff)
            row = (await session.execute(stats_query)).one()
            valid = int(row[0] or 0)
            available = int(row[1] or 0)
            first = ensure_utc(row[2])
            expected = self._expected_samples(first, cutoff)
            eligibility_samples = valid
        else:
            region_zones = self._region_zones(region)
            available, valid, first, eligibility_samples = await self._scope_stats(
                session, server.id, region_zones, cutoff
            )
            expected = self._expected_samples(first, cutoff) * len(region_zones)
        availability = (available / valid * 100) if valid else None
        coverage = min(100.0, valid / expected * 100) if expected else 0.0
        price, stocks = await self._location_summary(
            session,
            server.id,
            [filters.zone] if filters.zone else self._region_zones(region),
            include_inactive,
        )
        return RankedServer(
            server_type=server,
            region=region,
            availability_percent=availability,
            available_samples=available,
            valid_samples=valid,
            expected_samples=expected,
            coverage_percent=coverage,
            eligible=eligibility_samples >= 24,
            price=price,
            stocks=stocks,
        )

    def _region_zones(self, region: str) -> list[str]:
        return [zone for zone in self.settings.zones if region_for_zone(zone) == region]

    async def _scope_stats(
        self,
        session: AsyncSession,
        server_type_id: int,
        zones: list[str],
        cutoff: datetime | None,
    ) -> tuple[int, int, datetime | None, int]:
        first_observed = ensure_utc(
            await session.scalar(
                select(func.min(AvailabilityObservation.observed_at))
                .join(OfferLocation)
                .where(
                    OfferLocation.server_type_id == server_type_id,
                    OfferLocation.zone.in_(zones),
                )
            )
        )
        if first_observed is None or not zones:
            return 0, 0, None, 0
        start = max(first_observed, cutoff) if cutoff is not None else first_observed

        valid_samples = (
            select(
                ZoneCollectionRun.collection_run_id.label("run_id"),
                ZoneCollectionRun.zone.label("zone"),
                CollectionRun.scheduled_at.label("observed_at"),
            )
            .join(CollectionRun)
            .where(
                ZoneCollectionRun.status == "success",
                ZoneCollectionRun.zone.in_(zones),
                CollectionRun.scheduled_at >= start,
            )
            .subquery()
        )
        available_samples = (
            select(
                AvailabilityObservation.collection_run_id.label("run_id"),
                OfferLocation.zone.label("zone"),
            )
            .join(OfferLocation)
            .where(
                OfferLocation.server_type_id == server_type_id,
                OfferLocation.zone.in_(zones),
                AvailabilityObservation.is_available.is_(True),
            )
            .group_by(AvailabilityObservation.collection_run_id, OfferLocation.zone)
            .subquery()
        )
        row = (
            await session.execute(
                select(
                    func.count(available_samples.c.run_id),
                    func.count(valid_samples.c.run_id),
                    func.min(valid_samples.c.observed_at),
                    func.count(func.distinct(valid_samples.c.run_id)),
                )
                .select_from(valid_samples)
                .outerjoin(
                    available_samples,
                    and_(
                        available_samples.c.run_id == valid_samples.c.run_id,
                        available_samples.c.zone == valid_samples.c.zone,
                    ),
                )
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0), ensure_utc(row[2]), int(row[3] or 0)

    @staticmethod
    def _expected_samples(first: datetime | None, cutoff: datetime | None) -> int:
        if first is None:
            return 0
        start = max(first, cutoff) if cutoff is not None else first
        elapsed = datetime.now(UTC) - start
        return max(1, int(elapsed.total_seconds() // 3600) + 1)

    async def _location_summary(
        self,
        session: AsyncSession,
        server_type_id: int,
        zones: list[str],
        include_inactive: bool,
    ) -> tuple[PricePair, list[LocationStock]]:
        query = select(OfferLocation).where(
            OfferLocation.server_type_id == server_type_id,
            OfferLocation.zone.in_(zones),
        )
        if not include_inactive:
            query = query.where(OfferLocation.active.is_(True))
        locations = list((await session.scalars(query)).all())
        priced = [item for item in locations if item.hourly_price_nanos is not None]
        priced.sort(key=lambda item: item.hourly_price_nanos or 0)
        cheapest = priced[0] if priced else (locations[0] if locations else None)
        price = PricePair(
            cheapest.hourly_currency if cheapest else None,
            cheapest.hourly_price_nanos if cheapest else None,
            cheapest.monthly_currency if cheapest else None,
            cheapest.monthly_price_nanos if cheapest else None,
        )
        stocks = [
            LocationStock(item.zone, item.current_stock, item.enabled, item.active)
            for item in sorted(locations, key=lambda value: value.zone)
        ]
        return price, stocks

    async def _timeline(
        self,
        session: AsyncSession,
        server_type_id: int,
        timeframe: Timeframe,
        region: str,
        zone: str | None,
    ) -> list[TimelinePoint]:
        cutoff = cutoff_for(timeframe)
        if not zone:
            return await self._regional_timeline(session, server_type_id, timeframe, region, cutoff)
        query = (
            select(
                AvailabilityObservation.observed_at,
                AvailabilityObservation.is_available,
                AvailabilityObservation.stock,
            )
            .join(OfferLocation)
            .where(
                OfferLocation.server_type_id == server_type_id,
                OfferLocation.zone == zone,
            )
            .order_by(AvailabilityObservation.observed_at)
        )
        if cutoff is not None:
            query = query.where(AvailabilityObservation.observed_at >= cutoff)
        rows = (await session.execute(query)).all()
        if timeframe != "all":
            return [
                TimelinePoint(
                    timestamp=ensure_utc(row[0]) or datetime.now(UTC),
                    label=(ensure_utc(row[0]) or datetime.now(UTC)).strftime("%d %b %H:%M UTC"),
                    state=(
                        "available"
                        if row[1]
                        else "low"
                        if row[2] == "low"
                        else "unavailable"
                    ),
                    percent=100.0 if row[1] else 0.0,
                )
                for row in rows
            ]

        daily: dict[datetime, list[str]] = defaultdict(list)
        for observed_at, value, _stock in rows:
            timestamp = ensure_utc(observed_at) or datetime.now(UTC)
            state = "available" if value else "unavailable"
            daily[timestamp.replace(hour=0, minute=0, second=0, microsecond=0)].append(state)
        points: list[TimelinePoint] = []
        for day, states in daily.items():
            valid = [state for state in states if state != "unknown"]
            percent = valid.count("available") / len(valid) * 100 if valid else None
            state = (
                "unknown"
                if percent is None
                else "available"
                if percent == 100
                else "unavailable"
                if percent == 0
                else "partial"
            )
            points.append(TimelinePoint(day, day.strftime("%d %b %Y"), state, percent))
        return points

    async def _regional_timeline(
        self,
        session: AsyncSession,
        server_type_id: int,
        timeframe: Timeframe,
        region: str,
        cutoff: datetime | None,
    ) -> list[TimelinePoint]:
        zones = self._region_zones(region)
        first_observed = ensure_utc(
            await session.scalar(
                select(func.min(AvailabilityObservation.observed_at))
                .join(OfferLocation)
                .where(
                    OfferLocation.server_type_id == server_type_id,
                    OfferLocation.region == region,
                )
            )
        )
        if first_observed is None or not zones:
            return []
        start = max(first_observed, cutoff) if cutoff is not None else first_observed
        valid_rows = (
            await session.execute(
                select(
                    ZoneCollectionRun.collection_run_id,
                    CollectionRun.scheduled_at,
                    ZoneCollectionRun.zone,
                )
                .join(CollectionRun)
                .where(
                    ZoneCollectionRun.status == "success",
                    ZoneCollectionRun.zone.in_(zones),
                    CollectionRun.scheduled_at >= start,
                )
                .order_by(CollectionRun.scheduled_at, ZoneCollectionRun.zone)
            )
        ).all()
        available_keys = set(
            (
                await session.execute(
                    select(
                        AvailabilityObservation.collection_run_id,
                        OfferLocation.zone,
                    )
                    .join(OfferLocation)
                    .where(
                        OfferLocation.server_type_id == server_type_id,
                        OfferLocation.region == region,
                        AvailabilityObservation.is_available.is_(True),
                        AvailabilityObservation.observed_at >= start,
                    )
                    .distinct()
                )
            ).all()
        )
        samples: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
        for run_id, observed_at, sample_zone in valid_rows:
            timestamp = ensure_utc(observed_at) or datetime.now(UTC)
            samples[timestamp][1] += 1
            if (run_id, sample_zone) in available_keys:
                samples[timestamp][0] += 1

        if timeframe != "all":
            return [
                self._regional_timeline_point(timestamp, available, valid, hourly=True)
                for timestamp, (available, valid) in sorted(samples.items())
            ]

        daily: dict[datetime, list[int]] = defaultdict(lambda: [0, 0])
        for timestamp, (available, valid) in samples.items():
            day = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            daily[day][0] += available
            daily[day][1] += valid
        return [
            self._regional_timeline_point(day, available, valid, hourly=False)
            for day, (available, valid) in sorted(daily.items())
        ]

    @staticmethod
    def _regional_timeline_point(
        timestamp: datetime,
        available: int,
        valid: int,
        *,
        hourly: bool,
    ) -> TimelinePoint:
        percent = available / valid * 100
        state = "available" if percent == 100 else "unavailable" if percent == 0 else "partial"
        label = timestamp.strftime("%d %b %H:%M UTC" if hourly else "%d %b %Y")
        return TimelinePoint(timestamp, label, state, percent)
