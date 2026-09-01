from __future__ import annotations

import httpx

from app.scaleway import NANOS_PER_UNIT, ScalewayClient, money_value, normalize_offer
from tests.conftest import offer_payload


def test_normalize_offer_keeps_specs_prices_and_category() -> None:
    offer = normalize_offer(offer_payload())

    assert offer.name == "EM-A100"
    assert offer.commercial_range == "beryllium"
    assert offer.total_cores == 16
    assert offer.total_threads == 32
    assert offer.ram_bytes == 64 * 1024**3
    assert offer.storage_bytes == 960 * 1024**3
    assert offer.storage_types == "nvme"
    assert offer.hourly_price.nanos == NANOS_PER_UNIT + 250_000_000
    assert offer.monthly_price.nanos == 699 * NANOS_PER_UNIT
    assert offer.is_available is True


def test_only_full_enabled_stock_is_available() -> None:
    assert normalize_offer(offer_payload(stock="available", enabled=True)).is_available is True
    assert normalize_offer(offer_payload(stock="low", enabled=True)).is_available is False
    assert normalize_offer(offer_payload(stock="empty", enabled=True)).is_available is False
    assert normalize_offer(offer_payload(stock="available", enabled=False)).is_available is False


def test_fingerprint_changes_with_hardware_but_not_stock_or_price() -> None:
    original = normalize_offer(offer_payload())
    transient_change = normalize_offer(
        offer_payload(stock="low", hourly_units=9, monthly_units=999)
    )
    hardware_change = normalize_offer(offer_payload(cores=32))

    assert original.hardware_fingerprint == transient_change.hardware_fingerprint
    assert original.hardware_fingerprint != hardware_change.hardware_fingerprint


def test_money_requires_currency() -> None:
    assert money_value(None).nanos is None
    assert money_value({"units": 1, "nanos": 0}).nanos is None


async def test_client_paginates_hourly_offers(settings) -> None:
    first_page = [offer_payload(offer_id=f"offer-{index}") for index in range(100)]
    second_page = [offer_payload(offer_id="offer-100")]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Auth-Token"] == "test-secret"
        assert request.url.params["subscription_period"] == "hourly"
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={"total_count": 101, "offers": first_page if page == 1 else second_page},
        )

    client = ScalewayClient(settings, transport=httpx.MockTransport(handler))
    result = await client.fetch_zone_offers("fr-par-1")

    assert result.succeeded is True
    assert len(result.offers) == 101


async def test_client_resolves_monthly_price_from_linked_monthly_offer(settings) -> None:
    hourly_offer = offer_payload(offer_id="hourly-offer")
    hourly_offer.pop("price_per_month")
    hourly_offer["monthly_offer_id"] = "monthly-offer"
    monthly_offer = {
        "id": "monthly-offer",
        "subscription_period": "monthly",
        "price_per_month": {
            "currency_code": "EUR",
            "units": 649,
            "nanos": 500_000_000,
        },
    }
    requested_periods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        period = request.url.params["subscription_period"]
        requested_periods.append(period)
        offers = [hourly_offer] if period == "hourly" else [monthly_offer]
        return httpx.Response(200, json={"total_count": 1, "offers": offers})

    client = ScalewayClient(settings, transport=httpx.MockTransport(handler))
    result = await client.fetch_zone_offers("fr-par-1")

    assert result.succeeded is True
    assert requested_periods == ["hourly", "monthly"]
    assert len(result.offers) == 1
    normalized = normalize_offer(result.offers[0])
    assert normalized.hourly_price.nanos == 1_250_000_000
    assert normalized.monthly_price.currency == "EUR"
    assert normalized.monthly_price.nanos == 649_500_000_000
