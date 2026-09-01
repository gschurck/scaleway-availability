from __future__ import annotations

REGION_LABELS = {
    "fr-par": "Paris",
    "nl-ams": "Amsterdam",
    "pl-waw": "Warsaw",
}


def region_for_zone(zone: str) -> str:
    parts = zone.rsplit("-", 1)
    return parts[0] if len(parts) == 2 else zone


def region_label(region: str) -> str:
    return REGION_LABELS.get(region, region.upper())


def category_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()
