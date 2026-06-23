"""City extraction and normalization for PDL rows, seed data, and API responses."""

from __future__ import annotations

from typing import Any, Mapping, Optional

TARGET_CITY_NAMES = (
    "Charlotte",
    "Miami",
    "New York",
    "San Francisco",
)

CITY_ALIASES = {
    "charlotte": "Charlotte",
    "miami": "Miami",
    "new york": "New York",
    "new york city": "New York",
    "nyc": "New York",
    "san francisco": "San Francisco",
    "sf": "San Francisco",
}


def normalize_city_name(value: object) -> Optional[str]:
    """Map raw locality/city strings to canonical target-city names."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    if text in TARGET_CITY_NAMES:
        return text
    return CITY_ALIASES.get(text.lower())


def extract_city_from_record(record: Mapping[str, Any]) -> Optional[str]:
    """Read city/locality from a dict-shaped company or lead record."""
    for key in ("city", "locality", "location", "company_city", "hq_city"):
        value = record.get(key)
        if value is None:
            continue
        if key == "location" and isinstance(value, Mapping):
            for nested_key in ("locality", "city", "name"):
                nested_value = value.get(nested_key)
                if nested_value is None:
                    continue
                normalized = normalize_city_name(nested_value)
                if normalized:
                    return normalized
            continue
        normalized = normalize_city_name(value)
        if normalized:
            return normalized
    return None


def extract_city_from_pdl_record(record: Mapping[str, Any]) -> Optional[str]:
    """Extract and normalize city from a raw PDL API company record."""
    return extract_city_from_record(record)
