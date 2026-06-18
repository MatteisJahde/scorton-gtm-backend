#!/usr/bin/env python3
"""Wipe SQLite cache and rebuild the master target dataset export from scratch."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER_CSV_PATH = ROOT / "data" / "target_dataset_1000_companies.csv"
DB_PATH = ROOT / "companies.db"
EXPORT_CACHE = ROOT / "data" / "export-target-dataset.csv"
PDL_CSV = ROOT / "data" / "pdl_companies_sample.csv"

GENERATE_COUNT = 6500
CHICAGO_LOOKALIKES = 400

CACHE_GLOBS = (
    ROOT / "companies.db",
    ROOT / "data" / "*.csv",
    ROOT / "*.csv",
)


def wipe_local_state() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"deleted db: {DB_PATH}")

    for pattern in CACHE_GLOBS:
        for path in sorted(pattern.parent.glob(pattern.name)):
            if path.is_file():
                path.unlink()
                print(f"deleted cache: {path}")


def write_master_csv(csv_content: str) -> int:
    """Force-write the master export to the exact Excel-facing path."""
    MASTER_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    with MASTER_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        handle.write(csv_content)
        handle.flush()
        handle.close()

    rows = max(csv_content.count("\n") - 1, 0)
    return rows


def seed_fresh_companies() -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "seed_pdl_csv.py"),
        "--generate",
        str(GENERATE_COUNT),
        "--chicago-lookalikes",
        str(CHICAGO_LOOKALIKES),
        "--refresh-seeds",
    ]
    print("running:", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def ingest_and_export() -> dict:
    sys.path.insert(0, str(ROOT))

    import csv
    import io
    from collections import Counter

    from database import Base, SessionLocal, engine
    from dataset_builder import (
        CHICAGO_TARGET_ACCOUNTS,
        MAX_TARGET_ACCOUNTS,
        build_target_dataset,
        deduplicate_target_dataset_csv,
        export_target_dataset_csv,
    )
    from sorting_agent import CITY_TARGET_QUOTAS
    from ingestion import ingest_companies
    from migrations import migrate_db

    Base.metadata.create_all(bind=engine)
    migrate_db()

    db = SessionLocal()
    try:
        ingest_result = ingest_companies(db)
        build_result = build_target_dataset(db)
        csv_content = export_target_dataset_csv(db)

        row_count = write_master_csv(csv_content)
        removed = deduplicate_target_dataset_csv(str(MASTER_CSV_PATH))
        csv_content = MASTER_CSV_PATH.read_text(encoding="utf-8")
        row_count = max(csv_content.count("\n") - 1, 0)
        if removed:
            print(f"deduplicated: removed {removed} duplicate rows by company_website")
        with EXPORT_CACHE.open("w", encoding="utf-8", newline="") as handle:
            handle.write(csv_content)
            handle.flush()
            handle.close()
        print(
            f"SUCCESS: Wrote {row_count} rows to data/target_dataset_1000_companies.csv "
            f"({MASTER_CSV_PATH.resolve()})"
        )

        rows = list(csv.DictReader(io.StringIO(csv_content)))
        city_counts = Counter(row.get("city") or "" for row in rows)

        return {
            "ingest": ingest_result,
            "build": build_result,
            "output_csv": str(MASTER_CSV_PATH.resolve()),
            "total_rows": row_count,
            "parsed_rows": len(rows),
            "chicago_rows": city_counts.get("Chicago", 0),
            "city_counts": dict(city_counts),
            "targets": {
                "total": MAX_TARGET_ACCOUNTS,
                "chicago": CHICAGO_TARGET_ACCOUNTS,
                "city_quotas": dict(CITY_TARGET_QUOTAS),
            },
        }
    finally:
        db.close()


def main() -> int:
    wipe_local_state()
    seed_fresh_companies()
    result = ingest_and_export()
    print(result)

    qualify_cmd = [sys.executable, str(ROOT / "qualify_accounts.py")]
    print("running:", " ".join(qualify_cmd))
    subprocess.run(qualify_cmd, cwd=ROOT, check=True)

    if result["total_rows"] != result["targets"]["total"]:
        print(
            f"ERROR: expected {result['targets']['total']} rows, got {result['total_rows']}",
            file=sys.stderr,
        )
        return 1

    for city, expected in result["targets"]["city_quotas"].items():
        actual = result["city_counts"].get(city, 0)
        if actual != expected:
            print(
                f"ERROR: expected {expected} {city} rows, got {actual}",
                file=sys.stderr,
            )
            return 1

    if not MASTER_CSV_PATH.exists():
        print(f"ERROR: missing export file at {MASTER_CSV_PATH.resolve()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
