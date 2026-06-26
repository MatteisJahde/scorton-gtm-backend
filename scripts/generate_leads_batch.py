#!/usr/bin/env python3
"""
Generate a verified batch of new GTM leads for manual merge into actual_companies.csv.

Usage:
    # From local PDL JSONL export (no API key required):
    python scripts/generate_leads_batch.py --source jsonl --input-file data.json --count 100

    # From PDL API:
    export PDL_API_KEY="your_key"
    python scripts/generate_leads_batch.py --count 100 --output new_leads_batch.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seed_data import ACTUAL_COMPANIES_CSV  # noqa: E402
from services.lead_batch_builder import (  # noqa: E402
    DEFAULT_BATCH_OUTPUT,
    DEFAULT_JSONL_MAX_LINES,
    build_new_leads_batch,
    generate_verified_lead_batch,
    generate_verified_lead_batch_from_jsonl,
    write_lead_batch_csv,
)
from services.pdl_client import PDLAPIError, get_pdl_api_key, require_pdl_api_key  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate verified unique leads into new_leads_batch.csv."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of verified unique leads to produce (default: 100)",
    )
    parser.add_argument(
        "--source",
        choices=("pdl", "jsonl"),
        default="pdl",
        help="Lead source: PDL API or local JSONL file (default: pdl)",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=ROOT / "data.json",
        help="JSONL input path when --source jsonl (default: data.json)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_JSONL_MAX_LINES,
        help=f"Max JSONL lines to scan (default: {DEFAULT_JSONL_MAX_LINES})",
    )
    parser.add_argument(
        "--check-http",
        action="store_true",
        help="Perform live HTTP website checks (slower for JSONL scans)",
    )
    parser.add_argument(
        "--existing-csv",
        type=Path,
        default=ACTUAL_COMPANIES_CSV,
        help="Existing leads CSV used for deduplication (default: actual_companies.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / DEFAULT_BATCH_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_BATCH_OUTPUT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing output CSV",
    )
    args = parser.parse_args()

    api_key = get_pdl_api_key()
    if args.source == "pdl":
        try:
            api_key = require_pdl_api_key()
        except PDLAPIError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    try:
        if args.dry_run:
            if args.source == "jsonl":
                rows, report = generate_verified_lead_batch_from_jsonl(
                    target_count=args.count,
                    jsonl_path=args.input_file,
                    existing_csv=args.existing_csv,
                    api_key=api_key,
                    max_lines=args.max_lines,
                    check_http=args.check_http,
                )
            else:
                rows, report = generate_verified_lead_batch(
                    target_count=args.count,
                    api_key=api_key or "",
                    existing_csv=args.existing_csv,
                )
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "verified_rows": len(rows),
                        "sample": rows[:3],
                        "report": report.__dict__,
                    },
                    indent=2,
                )
            )
            return

        report = build_new_leads_batch(
            target_count=args.count,
            existing_csv=args.existing_csv,
            output_csv=args.output,
            api_key=api_key,
            source=args.source,
            jsonl_path=args.input_file,
            max_lines=args.max_lines,
            check_http=args.check_http,
        )
        print(
            json.dumps(
                {
                    "output": report.output_path,
                    "verified": report.verified,
                    "target_count": report.target_count,
                    "candidates_fetched": report.candidates_fetched,
                    "rejected_website": report.rejected_website,
                    "rejected_verification": report.rejected_verification,
                    "rejected_duplicate": report.rejected_duplicate,
                    "rejected_incomplete": report.rejected_incomplete,
                    "rejected_contact_lookup": report.rejected_contact_lookup,
                },
                indent=2,
            )
        )
    except (PDLAPIError, RuntimeError, FileNotFoundError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
