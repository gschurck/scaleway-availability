from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

NANOS_PER_UNIT = 1_000_000_000


@dataclass(frozen=True)
class MoneyValue:
    currency: str | None
    nanos: int | None


@dataclass(frozen=True)
class NormalizedOffer:
    api_offer_id: str
    name: str
    commercial_range: str
    hardware_fingerprint: str
    total_cores: int
    total_threads: int
    ram_bytes: int
    storage_bytes: int
    storage_types: str
    has_gpu: bool
    public_bandwidth_bps: int
    specs: dict[str, Any]
    enabled: bool
    stock: str
    hourly_price: MoneyValue
    monthly_price: MoneyValue

    @property
    def is_available(self) -> bool:
        return self.enabled and self.stock == "available"


@dataclass(frozen=True)
class ZoneFetchResult:
    zone: str
    offers: list[dict[str, Any]]
    latency_seconds: float
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def money_value(value: dict[str, Any] | None) -> MoneyValue:
    if not value:
        return MoneyValue(None, None)
    currency = value.get("currency_code")
    if not currency:
        return MoneyValue(None, None)
    units = int(value.get("units") or 0)
    nanos = int(value.get("nanos") or 0)
    return MoneyValue(str(currency).upper(), units * NANOS_PER_UNIT + nanos)


def normalize_offer(offer: dict[str, Any]) -> NormalizedOffer:
    cpus = offer.get("cpus") or []
    memories = offer.get("memories") or []
    disks = offer.get("disks") or []
    gpus = offer.get("gpus") or []

    total_cores = sum(int(cpu.get("core_count") or 0) for cpu in cpus)
    total_threads = sum(int(cpu.get("thread_count") or 0) for cpu in cpus)
    ram_bytes = sum(int(memory.get("capacity") or 0) for memory in memories)
    storage_bytes = sum(int(disk.get("capacity") or 0) for disk in disks)
    storage_types = ",".join(
        sorted({str(disk.get("type") or "unknown").strip().lower() for disk in disks})
    )

    specs = {
        "cpus": cpus,
        "memories": memories,
        "disks": disks,
        "gpus": gpus,
        "persistent_memories": offer.get("persistent_memories") or [],
        "raid_controllers": offer.get("raid_controllers") or [],
        "bandwidth": int(offer.get("bandwidth") or 0),
        "max_bandwidth": int(offer.get("max_bandwidth") or 0),
        "private_bandwidth": int(offer.get("private_bandwidth") or 0),
        "shared_bandwidth": bool(offer.get("shared_bandwidth", False)),
    }
    canonical_specs = json.dumps(specs, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical_specs.encode()).hexdigest()

    return NormalizedOffer(
        api_offer_id=str(offer["id"]),
        name=str(offer.get("name") or offer["id"]),
        commercial_range=str(offer.get("commercial_range") or "unknown").strip().lower(),
        hardware_fingerprint=fingerprint,
        total_cores=total_cores,
        total_threads=total_threads,
        ram_bytes=ram_bytes,
        storage_bytes=storage_bytes,
        storage_types=storage_types,
        has_gpu=bool(gpus),
        public_bandwidth_bps=int(offer.get("bandwidth") or 0),
        specs=specs,
        enabled=bool(offer.get("enable", False)),
        stock=str(offer.get("stock") or "empty").lower(),
        hourly_price=money_value(offer.get("price_per_hour")),
        monthly_price=money_value(offer.get("price_per_month")),
    )


class ScalewayClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch_zone_offers(self, zone: str) -> ZoneFetchResult:
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(self.settings.request_retries):
            try:
                offers = await self._fetch_all_pages(zone)
                return ZoneFetchResult(zone, offers, time.monotonic() - started)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.request_retries:
                    await asyncio.sleep(0.25 * (2**attempt))
        message = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown error"
        return ZoneFetchResult(zone, [], time.monotonic() - started, message[:500])

    async def _fetch_all_pages(self, zone: str) -> list[dict[str, Any]]:
        if not self.settings.scw_secret_key:
            raise ValueError("SCW_SECRET_KEY is not configured")
        headers = {"X-Auth-Token": self.settings.scw_secret_key}
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self.settings.scw_api_base_url,
            headers=headers,
            timeout=timeout,
            transport=self.transport,
        ) as client:
            hourly_offers = await self._fetch_offer_pages(client, zone, "hourly")
            unresolved_monthly_ids = {
                str(offer["monthly_offer_id"])
                for offer in hourly_offers
                if not offer.get("price_per_month") and offer.get("monthly_offer_id")
            }
            if not unresolved_monthly_ids:
                return hourly_offers

            monthly_offers = await self._fetch_offer_pages(client, zone, "monthly")
            monthly_prices = {
                str(offer["id"]): offer.get("price_per_month")
                for offer in monthly_offers
                if str(offer.get("id")) in unresolved_monthly_ids and offer.get("price_per_month")
            }
            return [
                {
                    **offer,
                    "price_per_month": monthly_prices[str(offer["monthly_offer_id"])],
                }
                if str(offer.get("monthly_offer_id")) in monthly_prices
                else offer
                for offer in hourly_offers
            ]

    @staticmethod
    async def _fetch_offer_pages(
        client: httpx.AsyncClient,
        zone: str,
        subscription_period: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            response = await client.get(
                f"/baremetal/v1/zones/{zone}/offers",
                params={
                    "page": page,
                    "page_size": 100,
                    "subscription_period": subscription_period,
                },
            )
            response.raise_for_status()
            payload = response.json()
            page_offers = payload.get("offers")
            if not isinstance(page_offers, list):
                raise ValueError("Scaleway response does not contain an offers list")
            results.extend(
                offer
                for offer in page_offers
                if offer.get("subscription_period", subscription_period) == subscription_period
            )
            total_count = int(payload.get("total_count") or len(results))
            if not page_offers or len(results) >= total_count or len(page_offers) < 100:
                break
            page += 1
            if page > 100:
                raise ValueError("Scaleway pagination exceeded 100 pages")
        return results
