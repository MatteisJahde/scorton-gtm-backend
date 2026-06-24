"""Load real company records from actual_companies.csv (no synthetic/PDL API data)."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Mapping, Optional

from city_utils import normalize_city_name

ROOT_DIR = Path(__file__).resolve().parent
ACTUAL_COMPANIES_CSV = ROOT_DIR / "actual_companies.csv"

PLACEHOLDER_NAME_MARKERS = ("PDL Sample Co", "Sample Co")

# Friendly spreadsheet headers -> internal field names.
CSV_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("company", "name", "company_name"),
    "website": ("website", "company_website", "company website"),
    "industry": ("industry",),
    "city": ("city", "locality", "location"),
    "employee_count": (
        "employee_count",
        "employee count",
        "employees",
        "employee_size",
        "size",
    ),
    "linkedin_url": ("linkedin_url", "company_linkedin_url", "linkedin", "linkedin url"),
}

SYNTHETIC_NAME_PATTERN = re.compile(
    r"\b(Atlas|Beacon|Crown|Delta|Echo|Falcon|Granite|Harbor|Iron|Juniper|"
    r"Keystone|Lighthouse|Meridian|Nova|Orion|Pioneer|Quantum|River|Sterling|Titan|"
    r"Union|Vertex|Westfield|Zenith|Axiom|Bridgewater|Catalyst|Dominion|Evergreen|Frontier)\b"
    r".*\bGroup\s+\d{4}\b",
    re.IGNORECASE,
)


def actual_companies_path() -> Path:
    return ACTUAL_COMPANIES_CSV


def actual_companies_available() -> bool:
    return ACTUAL_COMPANIES_CSV.exists()


def _normalize_header(header: str) -> str:
    return header.strip().lower().replace("_", " ")


def _first_value(row: Mapping[str, str], aliases: tuple[str, ...]) -> str:
    normalized_row = {_normalize_header(key): (value or "").strip() for key, value in row.items()}
    for alias in aliases:
        value = normalized_row.get(_normalize_header(alias), "")
        if value:
            return value
    return ""


def _parse_employee_count(raw: str) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.match(r"(\d+)\s*-\s*(\d+)", text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return (low + high) // 2
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _normalize_website(raw: str, company_name: str) -> str:
    website = (raw or "").strip()
    if not website:
        slug = "".join(char.lower() for char in company_name if char.isalnum())
        return f"https://www.{slug[:28]}.com" if slug else ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def is_placeholder_company(name: object) -> bool:
    value = str(name or "").strip()
    return any(marker in value for marker in PLACEHOLDER_NAME_MARKERS)


def is_synthetic_company_name(name: str) -> bool:
    value = str(name or "").strip()
    if not value:
        return True
    if is_placeholder_company(value):
        return True
    return bool(SYNTHETIC_NAME_PATTERN.search(value))


def map_csv_row_to_company(row: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """Map a CSV row to the internal company dict used by ingestion."""
    name = _first_value(row, CSV_FIELD_ALIASES["name"])
    if not name or is_synthetic_company_name(name):
        return None

    city = normalize_city_name(_first_value(row, CSV_FIELD_ALIASES["city"]))
    industry = _first_value(row, CSV_FIELD_ALIASES["industry"])
    employee_count = _parse_employee_count(_first_value(row, CSV_FIELD_ALIASES["employee_count"]))

    return {
        "name": name,
        "website": _normalize_website(_first_value(row, CSV_FIELD_ALIASES["website"]), name),
        "industry": industry,
        "city": city,
        "locality": city,
        "employee_count": employee_count,
        "linkedin_url": _first_value(row, CSV_FIELD_ALIASES["linkedin_url"]) or None,
    }


def load_actual_companies(csv_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Read companies from actual_companies.csv. Returns [] if the file is missing."""
    path = csv_path or ACTUAL_COMPANIES_CSV
    if not path.exists():
        return []

    companies: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            company = map_csv_row_to_company(row)
            if company:
                companies.append(company)
    return companies


def get_companies() -> list[dict[str, Any]]:
    """Primary data source for ingestion (static CSV only)."""
    return load_actual_companies()


# Backward-compatible alias used by ingestion.py / dataset_builder.py.
COMPANIES = get_companies()
