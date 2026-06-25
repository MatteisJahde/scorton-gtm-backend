#!/usr/bin/env python3
"""Filter actual_companies.csv by strict financial ICP rules."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.industry_filter import ALLOWED_INDUSTRIES, passes_financial_icp_filter  # noqa: E402


def filter_leads(
    input_path: Path,
    output_path: Path,
) -> tuple[int, int, list[dict[str, str]]]:
    """Return (rows_read, rows_written, removed_rows)."""
    with input_path.open(newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        if not reader.fieldnames or "industry" not in reader.fieldnames:
            raise ValueError("Input CSV must include an 'industry' column")
        if "company" not in reader.fieldnames:
            raise ValueError("Input CSV must include a 'company' column")

        fieldnames = reader.fieldnames
        rows: list[dict[str, str]] = []
        removed: list[dict[str, str]] = []
        total_read = 0
        for row in reader:
            total_read += 1
            accepted, reason = passes_financial_icp_filter(row)
            if not accepted:
                removed.append({**row, "removal_reason": reason or "rejected"})
                continue
            rows.append(row)

    with output_path.open("w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return total_read, len(rows), removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter leads by financial ICP industry and blocklist rules."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "actual_companies.csv",
        help="Input CSV path (default: actual_companies.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "filtered_leads.csv",
        help="Output CSV path (default: filtered_leads.csv)",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    print(f"Allowed industries: {', '.join(sorted(ALLOWED_INDUSTRIES))}")

    total, kept, removed = filter_leads(args.input, args.output)
    print(f"Read {total} rows, kept {kept}, removed {len(removed)}, wrote {args.output}")

    if removed:
        print("\nRemoved companies:")
        for row in removed:
            print(
                f"  - {row.get('company', '')} "
                f"[{row.get('industry', '')}] "
                f"({row.get('removal_reason', 'unknown')})"
            )


if __name__ == "__main__":
    main()
