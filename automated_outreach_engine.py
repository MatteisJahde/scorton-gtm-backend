#!/usr/bin/env python3
"""
Automated outreach list builder.

Fetches Fintech and Accounting companies from People Data Labs, resolves a buyer
contact for each, appends rows to actual_companies.csv, and triggers a dashboard
reload on the deployed backend.

Usage:
    export PDL_API_KEY="your_key"
    python automated_outreach_engine.py
    python automated_outreach_engine.py --count 100 --dry-run
    python automated_outreach_engine.py --skip-reload

CSV output columns (actual_companies.csv schema):
    company, website, industry, city, employee_count, intent, signal_score,
    buyer_name (Reach Out To), job_title, work_email (Email)
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from city_utils import normalize_city_name  # noqa: E402
from seed_data import ACTUAL_COMPANIES_CSV  # noqa: E402
from services.pdl_client import (  # noqa: E402
    PDLAPIError,
    fetch_fintech_and_accounting_companies,
    require_pdl_api_key,
)
from services.pdl_contact_search import PDLPersonSearchError, search_buyer_contact  # noqa: E402
from sorting_agent import ALLOWED_CITIES  # noqa: E402

DEFAULT_TARGET_COUNT = 100
DEFAULT_RELOAD_URL = "https://scorton-gtm-backend.onrender.com/api/reload-from-csv"
DEFAULT_INTENT = "high"

ACTUAL_COMPANIES_FIELDNAMES = [
    "company",
    "website",
    "industry",
    "city",
    "employee_count",
    "intent",
    "signal_score",
    "buyer_name",
    "job_title",
    "work_email",
]


def _normalize_website(raw: str) -> str:
    website = (raw or "").strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def _parse_employee_count(raw: object) -> int:
    text = str(raw or "").strip()
    if text.isdigit():
        return int(text)
    digits = "".join(char for char in text if char.isdigit())
    return int(digits) if digits else 100


def _default_signal_score(employee_count: int) -> int:
    return min(97, max(82, 70 + employee_count // 25))


def format_company_for_csv(
    company: dict[str, Any],
    contact: dict[str, str],
) -> dict[str, str]:
    """Map PDL company + buyer contact into actual_companies.csv row shape."""
    employee_count = _parse_employee_count(company.get("employee_count") or company.get("size"))
    city = normalize_city_name(company.get("city") or company.get("locality") or "") or ""

    return {
        "company": company["name"],
        "website": _normalize_website(company.get("website") or ""),
        "industry": company.get("industry") or "Financial Services",
        "city": city,
        "employee_count": str(employee_count),
        "intent": DEFAULT_INTENT,
        "signal_score": str(_default_signal_score(employee_count)),
        "buyer_name": contact.get("buyer_name") or "",
        "job_title": contact.get("job_title") or "",
        "work_email": contact.get("work_email") or "",
    }


def _row_is_complete(row: dict[str, str]) -> bool:
    city = normalize_city_name(row.get("city") or "")
    if not city or city not in ALLOWED_CITIES:
        return False
    required = ("company", "website", "industry", "buyer_name", "job_title", "work_email")
    return all(str(row.get(field) or "").strip() for field in required)


def fetch_target_list(
    *,
    target_count: int,
    api_key: str,
) -> list[dict[str, str]]:
    """Fetch companies from PDL and attach buyer contacts."""
    companies = fetch_fintech_and_accounting_companies(
        target_count=max(target_count * 2, target_count + 25),
        api_key=api_key,
    )

    rows: list[dict[str, str]] = []
    skipped_no_contact = 0

    for index, company in enumerate(companies, start=1):
        print(f"[{index}/{len(companies)}] Resolving contact for {company['name']}...", flush=True)
        try:
            contact = search_buyer_contact(
                company_name=company["name"],
                website=company.get("website") or "",
                industry=company.get("industry") or "",
                api_key=api_key,
            )
        except PDLPersonSearchError as exc:
            print(f"  Skipped (person search error): {exc}", flush=True)
            skipped_no_contact += 1
            continue

        if not contact:
            print("  Skipped (no buyer contact found in PDL)", flush=True)
            skipped_no_contact += 1
            continue

        row = format_company_for_csv(company, contact)
        if not _row_is_complete(row):
            print(
                f"  Skipped (incomplete row — city={row.get('city')!r}, "
                f"email={bool(row.get('work_email'))})",
                flush=True,
            )
            skipped_no_contact += 1
            continue

        rows.append(row)
        if len(rows) >= target_count:
            break

    print(
        f"Prepared {len(rows)} complete rows "
        f"(skipped {skipped_no_contact} without usable contact/location).",
        flush=True,
    )
    if not rows:
        raise RuntimeError("No complete Fintech/Accounting rows could be built from PDL.")
    return rows


def _read_existing_company_names(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return {
            (row.get("company") or row.get("Company") or "").strip().lower()
            for row in reader
            if (row.get("company") or row.get("Company") or "").strip()
        }


def append_rows_to_csv(csv_path: Path, rows: list[dict[str, str]]) -> dict[str, int]:
    """Append new rows to actual_companies.csv, skipping duplicate company names."""
    existing_names = _read_existing_company_names(csv_path)
    to_append = [
        row for row in rows if row["company"].strip().lower() not in existing_names
    ]

    write_header = not csv_path.exists() or csv_path.stat().st_size == 0
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACTUAL_COMPANIES_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(to_append)

    return {
        "appended": len(to_append),
        "skipped_duplicates": len(rows) - len(to_append),
        "total_in_file": len(existing_names) + len(to_append),
    }


def trigger_dashboard_reload(reload_url: str) -> dict[str, Any]:
    """POST to /api/reload-from-csv to refresh the Lovable dashboard."""
    print(f"Triggering dashboard reload: {reload_url}", flush=True)
    response = requests.post(reload_url, timeout=180)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_response": response.text[:500]}
    print("Dashboard reload complete.", flush=True)
    return payload


def resolve_reload_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    configured = (os.getenv("BACKEND_RELOAD_URL") or os.getenv("API_BASE_URL") or "").strip()
    if configured:
        if configured.endswith("/api/reload-from-csv"):
            return configured
        return urljoin(configured.rstrip("/") + "/", "api/reload-from-csv")
    return DEFAULT_RELOAD_URL


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a Fintech/Accounting target list from PDL and refresh the dashboard."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_TARGET_COUNT,
        help=f"Number of companies to fetch (default: {DEFAULT_TARGET_COUNT})",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=ACTUAL_COMPANIES_CSV,
        help="Path to actual_companies.csv (default: project root)",
    )
    parser.add_argument(
        "--reload-url",
        default=None,
        help="Override reload endpoint URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print rows without writing CSV or calling reload API",
    )
    parser.add_argument(
        "--skip-reload",
        action="store_true",
        help="Append to CSV but do not call /api/reload-from-csv",
    )
    args = parser.parse_args()

    try:
        api_key = require_pdl_api_key()
    except PDLAPIError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        rows = fetch_target_list(target_count=args.count, api_key=api_key)
    except (PDLAPIError, RuntimeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.dry_run:
        print({"dry_run": True, "rows_prepared": len(rows), "sample": rows[:3]})
        return

    append_stats = append_rows_to_csv(args.csv, rows)
    print({"csv_append": append_stats, "csv_path": str(args.csv.resolve())})

    if args.skip_reload:
        print("Skipping dashboard reload (--skip-reload).")
        return

    reload_url = resolve_reload_url(args.reload_url)
    try:
        reload_result = trigger_dashboard_reload(reload_url)
    except requests.RequestException as exc:
        print(
            f"WARNING: CSV updated but dashboard reload failed: {exc}\n"
            f"Run manually: curl -X POST {reload_url}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    print({"reload": reload_result})


if __name__ == "__main__":
    main()
