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

from seed_data import ACTUAL_COMPANIES_CSV  # noqa: E402
from services.pdl_client import PDLAPIError, require_pdl_api_key  # noqa: E402
from services.lead_batch_builder import (  # noqa: E402
    ACTUAL_COMPANIES_FIELDNAMES,
    generate_verified_lead_batch,
    load_existing_lead_keys,
    normalize_company_key,
    normalize_domain_key,
    write_lead_batch_csv,
)

DEFAULT_TARGET_COUNT = 100
DEFAULT_RELOAD_URL = "https://scorton-gtm-backend.onrender.com/api/reload-from-csv"


def append_rows_to_csv(csv_path: Path, rows: list[dict[str, str]]) -> dict[str, int]:
    """Append new rows to actual_companies.csv, skipping duplicate company names."""
    existing_names, existing_domains = load_existing_lead_keys(csv_path)
    initial_count = len(existing_names)
    to_append = []
    for row in rows:
        name_key = normalize_company_key(row["company"])
        domain_key = normalize_domain_key(row.get("website", ""))
        if name_key in existing_names:
            continue
        if domain_key and domain_key in existing_domains:
            continue
        to_append.append(row)
        existing_names.add(name_key)
        if domain_key:
            existing_domains.add(domain_key)

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
        "total_in_file": initial_count + len(to_append),
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
        "--output",
        type=Path,
        default=None,
        help="Write batch to this CSV instead of appending to actual_companies.csv",
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
        rows, report = generate_verified_lead_batch(
            target_count=args.count,
            api_key=api_key,
            existing_csv=args.csv,
        )
    except (PDLAPIError, RuntimeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.dry_run:
        print(
            {
                "dry_run": True,
                "rows_prepared": len(rows),
                "report": report.__dict__,
                "sample": rows[:3],
            }
        )
        return

    if args.output:
        write_lead_batch_csv(rows, args.output)
        print({"output": str(args.output.resolve()), "verified": len(rows), "report": report.__dict__})
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
