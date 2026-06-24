"""
People Data Labs Company Search API client.

DEPRECATED: The production pipeline no longer calls PDL. Use actual_companies.csv
via seed_data.load_actual_companies() instead.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import requests

from city_utils import extract_city_from_pdl_record

PDL_SEARCH_URL = "https://api.peopledatalabs.com/v5/company/search"

# Display name -> PDL SQL locality value
TARGET_CITIES = (
    ("New York", "new york"),
    ("San Francisco", "san francisco"),
    ("Charlotte", "charlotte"),
    ("Miami", "miami"),
    ("Chicago", "chicago"),
)

TARGET_INDUSTRIES = (
    "financial services",
    "insurance",
    "accounting",
    "investment management",
    "banking",
)

# Fintech + Accounting list-building (PDL industry values).
FINTECH_ACCOUNTING_PDL_INDUSTRIES = (
    "financial services",
    "fintech",
    "financial technology",
    "banking",
    "investment management",
    "payments",
    "capital markets",
    "accounting",
)


class PDLAPIError(RuntimeError):
    """Raised when a PDL API request fails."""


def get_pdl_api_key() -> Optional[str]:
    return os.getenv("PDL_API_KEY")


def require_pdl_api_key() -> str:
    api_key = get_pdl_api_key()
    if api_key:
        print("DEBUG: API KEY FOUND", flush=True)
        return api_key
    print("DEBUG: API KEY MISSING", flush=True)
    raise PDLAPIError(
        "PDL_API_KEY is not set in the environment. "
        'Export it with: export PDL_API_KEY="your_key"'
    )


def _sql_for_city(locality: str, *, industries: tuple[str, ...] = TARGET_INDUSTRIES) -> str:
    industry_list = ", ".join(f"'{value}'" for value in industries)
    return (
        "SELECT * FROM company WHERE "
        "employee_count >= 20 AND employee_count <= 500 AND "
        "location.country = 'united states' AND "
        f"location.locality = '{locality}' AND "
        f"industry IN ({industry_list})"
    )


def _sql_for_fintech_accounting(locality: str) -> str:
    return _sql_for_city(locality, industries=FINTECH_ACCOUNTING_PDL_INDUSTRIES)


def _pdl_record_to_row(record: dict[str, Any], *, display_city: str) -> dict[str, str]:
    employee_count = record.get("employee_count")
    size = str(employee_count) if employee_count is not None else (record.get("size") or "")
    city = extract_city_from_pdl_record(record) or display_city

    return {
        "name": (record.get("name") or record.get("display_name") or "").strip(),
        "website": (record.get("website") or "").strip(),
        "industry": (record.get("industry") or "unknown").strip(),
        "size": size,
        "employee_count": size,
        "city": city,
        "locality": city,
        "country": "united states",
        "linkedin_url": (record.get("linkedin_url") or "").strip(),
        "pdl_id": str(record.get("id") or ""),
    }


def _extract_error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return response.text

    error = body.get("error")
    if isinstance(error, dict):
        return error.get("message") or json.dumps(body)
    return json.dumps(body)


def _raise_http_failure(response: requests.Response, *, city: str) -> None:
    reason = response.reason or "Unknown"
    message = _extract_error_message(response)
    print(
        f"API DEBUG [{city}]: HTTP {response.status_code} {reason}. Data: {message}",
        flush=True,
    )
    raise PDLAPIError(
        f"PDL API request failed for {city}: "
        f"HTTP {response.status_code} {reason} — {message}"
    )


def _fetch_city_companies(
    *,
    display_city: str,
    sql_locality: str,
    api_key: str,
    max_rows: Optional[int] = None,
    sql_builder=None,
) -> list[dict[str, str]]:
    """Fetch all available companies for one city (paginated). No per-city quotas."""
    headers = {
        "Content-Type": "application/json",
        "X-api-key": api_key,
    }
    build_sql = sql_builder or _sql_for_city

    rows: list[dict[str, str]] = []
    scroll_token: Optional[str] = None
    page = 0

    print(f"Fetching PDL companies for {display_city}...", flush=True)

    while True:
        if max_rows is not None and len(rows) >= max_rows:
            break

        batch_size = 100 if max_rows is None else min(100, max_rows - len(rows))
        payload: dict[str, Any] = {
            "sql": build_sql(sql_locality),
            "size": batch_size,
            "titlecase": True,
        }
        if scroll_token:
            payload["scroll_token"] = scroll_token

        try:
            response = requests.post(PDL_SEARCH_URL, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            print(f"API DEBUG [{display_city}]: Connection error — {exc}", flush=True)
            raise PDLAPIError(
                f"Connection error while fetching {display_city}: {exc}"
            ) from exc

        if response.status_code == 401:
            _raise_http_failure(response, city=display_city)
        if response.status_code == 403:
            _raise_http_failure(response, city=display_city)
        if response.status_code == 429:
            _raise_http_failure(response, city=display_city)
        if response.status_code != 200:
            _raise_http_failure(response, city=display_city)

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            print(
                f"API DEBUG [{display_city}]: Non-JSON response — {response.text}",
                flush=True,
            )
            raise PDLAPIError(
                f"PDL API returned non-JSON response for {display_city}."
            ) from exc

        if body.get("status") != 200:
            message = json.dumps(body)
            print(
                f"API DEBUG [{display_city}]: Error payload — {message}",
                flush=True,
            )
            raise PDLAPIError(f"PDL API error for {display_city}: {message}")

        page += 1
        batch = body.get("data") or []
        if page == 1 and not batch:
            print(f"API DEBUG [{display_city}]: Zero results returned.", flush=True)
            raise PDLAPIError(
                f"PDL API returned zero results for city: {display_city}. "
                "Check filters or API plan limits."
            )

        for record in batch:
            row = _pdl_record_to_row(record, display_city=display_city)
            if row["name"]:
                rows.append(row)

        scroll_token = body.get("scroll_token")
        if not scroll_token or not batch:
            break

    print(f"  {display_city}: {len(rows)} companies fetched", flush=True)
    return rows[:max_rows] if max_rows is not None else rows


def fetch_companies_from_pdl(
    target_count: Optional[int] = None,
    *,
    api_key: Optional[str] = None,
) -> list[dict[str, str]]:
    """
    Fetch real companies from PDL across all target cities.

    No per-city quotas. Paginates until API is exhausted or target_count is reached.
    Raises PDLAPIError on connection failure or zero results for any city.
    """
    api_key = api_key or require_pdl_api_key()

    all_rows: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for display_city, sql_locality in TARGET_CITIES:
        remaining = None if target_count is None else max(target_count - len(all_rows), 0)
        if remaining == 0:
            break

        city_rows = _fetch_city_companies(
            display_city=display_city,
            sql_locality=sql_locality,
            api_key=api_key,
            max_rows=remaining,
        )

        for row in city_rows:
            name = row["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            all_rows.append(row)
            if target_count is not None and len(all_rows) >= target_count:
                break

    if not all_rows:
        raise PDLAPIError("PDL API returned zero companies across all target cities.")

    print(
        f"Total fetched: {len(all_rows)} companies across "
        f"{len(TARGET_CITIES)} cities (no city quotas applied).",
        flush=True,
    )
    return all_rows[:target_count] if target_count is not None else all_rows


def map_pdl_industry_to_target_industry(pdl_industry: str) -> str:
    """Map PDL industry strings to dashboard-accepted industry labels."""
    normalized = (pdl_industry or "").strip().lower()
    if "account" in normalized:
        return "Accounting"
    return "Financial Services"


def fetch_fintech_and_accounting_companies(
    target_count: int = 100,
    *,
    api_key: Optional[str] = None,
) -> list[dict[str, str]]:
    """
    Fetch companies in Fintech / Financial Services and Accounting from PDL.

    Returns up to ``target_count`` unique companies across all target US cities.
    """
    api_key = api_key or require_pdl_api_key()

    all_rows: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for display_city, sql_locality in TARGET_CITIES:
        remaining = max(target_count - len(all_rows), 0)
        if remaining == 0:
            break

        city_rows = _fetch_city_companies(
            display_city=display_city,
            sql_locality=sql_locality,
            api_key=api_key,
            max_rows=remaining,
            sql_builder=_sql_for_fintech_accounting,
        )

        for row in city_rows:
            name = row["name"]
            if not name or name in seen_names:
                continue
            if not row.get("website"):
                continue
            seen_names.add(name)
            row["industry"] = map_pdl_industry_to_target_industry(row["industry"])
            all_rows.append(row)
            if len(all_rows) >= target_count:
                break

    if not all_rows:
        raise PDLAPIError(
            "PDL API returned zero Fintech/Accounting companies across target cities."
        )

    print(
        f"Fetched {len(all_rows)} Fintech/Accounting companies "
        f"(target={target_count}).",
        flush=True,
    )
    return all_rows[:target_count]
