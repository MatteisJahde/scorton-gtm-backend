#!/usr/bin/env python3
"""
Load PDL-format company records from CSV into SQLite.

Usage:
  export PDL_API_KEY="your_key"
  python3 scripts/seed_pdl_csv.py --generate 6500 --refresh-seeds --sync
  python3 scripts/seed_pdl_csv.py --csv data/pdl_companies.csv
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Base, SessionLocal, engine  # noqa: E402
from migrations import migrate_db  # noqa: E402
from models import Company  # noqa: E402
from services.pdl_client import PDLAPIError, fetch_companies_from_pdl, require_pdl_api_key
from sorting_agent import ALLOWED_CITIES  # noqa: E402

DEFAULT_CSV = ROOT / "data" / "pdl_companies_sample.csv"
DEFAULT_GENERATE_COUNT = 6500

PDL_COLUMNS = [
    "name",
    "website",
    "industry",
    "size",
    "locality",
    "country",
    "linkedin_url",
]

INDUSTRY_ALIASES = {
    "financial services": "Financial Services",
    "insurance": "Insurance",
    "accounting": "Accounting",
    "investment management": "Financial Services",
    "banking": "Financial Services",
    "technology": "Financial Services",
}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def size_to_employee_count(size: str) -> Optional[int]:
    if not size:
        return None
    size = size.strip().lower()
    if size.isdigit():
        return int(size)
    match = re.match(r"(\d+)\s*-\s*(\d+)", size)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return (low + high) // 2
    return None


def normalize_industry(industry: str) -> str:
    cleaned = (industry or "unknown").strip()
    return INDUSTRY_ALIASES.get(cleaned.lower(), cleaned)


def seed_to_pdl_row(company: dict) -> dict:
    slug = slugify(company["name"])
    return {
        "name": company["name"],
        "website": f"https://www.{slug}.com",
        "industry": company["industry"],
        "size": str(company["employee_count"]),
        "locality": company["city"],
        "country": "united states",
        "linkedin_url": f"https://linkedin.com/company/{slug}",
    }


SEED_COMPANIES: list[dict] = []


def write_pdl_rows_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PDL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in PDL_COLUMNS})
    print(f"Wrote {len(rows)} PDL API rows -> {path.resolve()}")


def fetch_pdl_csv(path: Path, count: Optional[int]) -> None:
    """Fetch real companies from PDL. Raises PDLAPIError on any failure — no fallback."""
    api_key = require_pdl_api_key()
    limit_label = str(count) if count else "unlimited"
    print(
        f"Fetching up to {limit_label} companies from People Data Labs "
        f"across: {', '.join(sorted(ALLOWED_CITIES))}",
        flush=True,
    )
    api_rows = fetch_companies_from_pdl(count, api_key=api_key)
    if not api_rows:
        raise PDLAPIError("PDL API returned zero companies.")
    write_pdl_rows_csv(path, api_rows)
    print(f"SUCCESS: fetched {len(api_rows)} real companies from PDL API", flush=True)


def row_to_company(row: dict) -> Company:
    name = (row.get("name") or "").strip()
    locality = (row.get("locality") or row.get("city") or "").strip() or None
    size = (row.get("size") or "").strip() or None
    employee_count = size_to_employee_count(size or "")
    industry = normalize_industry(row.get("industry") or "unknown")

    return Company(
        name=name,
        website=(row.get("website") or "").strip() or None,
        industry=industry,
        size=size,
        locality=locality,
        country=(row.get("country") or "").strip() or None,
        linkedin_url=(row.get("linkedin_url") or "").strip() or None,
        is_targeted=False,
        week_assigned=None,
        city=locality,
        employee_count=employee_count,
    )


def apply_row_to_company(company: Company, row: dict) -> None:
    locality = (row.get("locality") or row.get("city") or "").strip() or None
    size = (row.get("size") or "").strip() or None
    company.website = (row.get("website") or "").strip() or company.website
    company.industry = normalize_industry(row.get("industry") or company.industry)
    company.size = size or company.size
    company.locality = locality or company.locality
    company.country = (row.get("country") or "").strip() or company.country
    company.linkedin_url = (row.get("linkedin_url") or "").strip() or company.linkedin_url
    company.city = locality or company.city
    company.employee_count = size_to_employee_count(size or "") or company.employee_count


def upsert_seed_companies(db) -> dict:
    inserted = 0
    updated = 0

    for row in SEED_COMPANIES:
        name = row["name"]
        existing = db.query(Company).filter(Company.name == name).first()
        if existing:
            apply_row_to_company(existing, row)
            updated += 1
        else:
            db.add(row_to_company(row))
            inserted += 1

    db.commit()
    return {"inserted": inserted, "updated": updated}


def load_pdl_csv(csv_path: Path, *, sync_existing: bool = False) -> dict:
    Base.metadata.create_all(bind=engine)
    migrate_db()
    db = SessionLocal()

    inserted = 0
    skipped = 0
    updated = 0

    try:
        existing_names = {name for (name,) in db.query(Company.name).all()}

        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    skipped += 1
                    continue

                existing = db.query(Company).filter(Company.name == name).first()
                if existing:
                    if sync_existing:
                        apply_row_to_company(existing, row)
                        updated += 1
                    else:
                        skipped += 1
                    continue

                db.add(row_to_company(row))
                existing_names.add(name)
                inserted += 1

        db.commit()
    finally:
        db.close()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "updated": updated,
        "csv": str(csv_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SQLite from PDL company CSV")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="PDL CSV path")
    parser.add_argument(
        "--generate",
        type=int,
        default=0,
        help=(
            f"Fetch up to N companies from PDL API across all target cities "
            f"(default max: {DEFAULT_GENERATE_COUNT}). Requires PDL_API_KEY."
        ),
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Update existing company rows from the CSV instead of skipping them",
    )
    parser.add_argument(
        "--refresh-seeds",
        action="store_true",
        help="Upsert optional seed companies before loading the CSV",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Clear-sync mode: refresh seeds and update existing DB rows from CSV",
    )
    args = parser.parse_args()

    if args.refresh:
        args.refresh_seeds = True
        args.sync = True

    Base.metadata.create_all(bind=engine)
    migrate_db()

    if args.refresh_seeds:
        db = SessionLocal()
        try:
            seed_result = upsert_seed_companies(db)
            print({"seed_companies": seed_result})
        finally:
            db.close()

    if args.generate > 0:
        count = args.generate
        try:
            fetch_pdl_csv(args.csv, count)
        except PDLAPIError as exc:
            print(f"FATAL: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(1) from exc
    elif not args.csv.exists():
        print("FATAL: CSV not found and --generate was not provided.", file=sys.stderr, flush=True)
        print(
            'Set PDL_API_KEY and run: python3 scripts/seed_pdl_csv.py --generate 6500',
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    result = load_pdl_csv(args.csv, sync_existing=args.sync)
    print(result)


if __name__ == "__main__":
    main()
