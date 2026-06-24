#!/usr/bin/env python3
"""
Load companies from actual_companies.csv into SQLite.

PDL API usage has been removed. Place your real company export at:
  ./actual_companies.csv
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Base, SessionLocal, engine  # noqa: E402
from dataset_builder import build_target_dataset  # noqa: E402
from ingestion import ingest_companies  # noqa: E402
from migrations import migrate_db  # noqa: E402
from seed_data import ACTUAL_COMPANIES_CSV, actual_companies_available, get_companies  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest actual_companies.csv into SQLite")
    parser.add_argument(
        "--csv",
        type=Path,
        default=ACTUAL_COMPANIES_CSV,
        help="Path to actual_companies.csv (default: project root)",
    )
    parser.add_argument(
        "--build-target-dataset",
        action="store_true",
        help="Also rebuild target_accounts after ingest",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"FATAL: CSV not found: {args.csv.resolve()}", file=sys.stderr)
        raise SystemExit(1)

    Base.metadata.create_all(bind=engine)
    migrate_db()

    # Temporarily override loader path by copying path into standard location if custom.
    if args.csv.resolve() != ACTUAL_COMPANIES_CSV.resolve():
        ACTUAL_COMPANIES_CSV.write_text(args.csv.read_text(encoding="utf-8"), encoding="utf-8")

    companies = get_companies()
    if not companies:
        print("FATAL: No valid rows found in CSV.", file=sys.stderr)
        raise SystemExit(1)

    db = SessionLocal()
    try:
        ingest_result = ingest_companies(db)
        print({"ingest": ingest_result, "csv_rows": len(companies)})
        if args.build_target_dataset:
            build_result = build_target_dataset(db)
            print({"build_target_dataset": build_result})
    finally:
        db.close()


if __name__ == "__main__":
    main()
