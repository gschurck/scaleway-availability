from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class ServerType(Base):
    __tablename__ = "server_types"
    __table_args__ = (
        UniqueConstraint("name", "hardware_fingerprint", name="uq_server_type_identity"),
        Index("ix_server_type_filters", "commercial_range", "total_cores", "ram_bytes"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    hardware_fingerprint: Mapped[str] = mapped_column(String(64))
    commercial_range: Mapped[str] = mapped_column(String(80), default="unknown", index=True)
    total_cores: Mapped[int] = mapped_column(Integer, default=0)
    total_threads: Mapped[int] = mapped_column(Integer, default=0)
    ram_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_types: Mapped[str] = mapped_column(String(160), default="")
    has_gpu: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    public_bandwidth_bps: Mapped[int] = mapped_column(Integer, default=0)
    specs_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    locations: Mapped[list[OfferLocation]] = relationship(
        back_populates="server_type", cascade="all, delete-orphan"
    )


class OfferLocation(Base):
    __tablename__ = "offer_locations"
    __table_args__ = (
        UniqueConstraint(
            "api_offer_id", "zone", "server_type_id", name="uq_offer_location_api_zone_type"
        ),
        Index("ix_offer_location_current", "region", "zone", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_type_id: Mapped[int] = mapped_column(ForeignKey("server_types.id", ondelete="CASCADE"))
    api_offer_id: Mapped[str] = mapped_column(String(80))
    zone: Mapped[str] = mapped_column(String(32), index=True)
    region: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    current_stock: Mapped[str] = mapped_column(String(32), default="empty")
    hourly_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    hourly_price_nanos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    monthly_price_nanos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    server_type: Mapped[ServerType] = relationship(back_populates="locations")
    observations: Mapped[list[AvailabilityObservation]] = relationship(
        back_populates="offer_location", cascade="all, delete-orphan"
    )


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")

    zone_runs: Mapped[list[ZoneCollectionRun]] = relationship(
        back_populates="collection_run", cascade="all, delete-orphan"
    )


class ZoneCollectionRun(Base):
    __tablename__ = "zone_collection_runs"
    __table_args__ = (UniqueConstraint("collection_run_id", "zone", name="uq_zone_run"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_run_id: Mapped[int] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE")
    )
    zone: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24))
    offer_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    collection_run: Mapped[CollectionRun] = relationship(back_populates="zone_runs")


class AvailabilityObservation(Base):
    __tablename__ = "availability_observations"
    __table_args__ = (
        UniqueConstraint("collection_run_id", "offer_location_id", name="uq_offer_observation_run"),
        Index("ix_offer_observation_time", "observed_at", "is_available"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_run_id: Mapped[int] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE")
    )
    offer_location_id: Mapped[int] = mapped_column(
        ForeignKey("offer_locations.id", ondelete="CASCADE")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stock: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean)
    is_available: Mapped[bool] = mapped_column(Boolean, index=True)

    offer_location: Mapped[OfferLocation] = relationship(back_populates="observations")


class RegionalObservation(Base):
    __tablename__ = "regional_observations"
    __table_args__ = (
        UniqueConstraint(
            "collection_run_id", "server_type_id", "region", name="uq_regional_observation_run"
        ),
        Index("ix_regional_observation_stats", "server_type_id", "region", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_run_id: Mapped[int] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="CASCADE")
    )
    server_type_id: Mapped[int] = mapped_column(ForeignKey("server_types.id", ondelete="CASCADE"))
    region: Mapped[str] = mapped_column(String(32), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(24), index=True)
