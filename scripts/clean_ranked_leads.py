#!/usr/bin/env python3
"""Remove non-financial companies from scorton_final_ranked_leads.csv."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Import shared filter from project services.
import sys

sys.path.insert(0, str(ROOT))

from services.industry_filter import passes_financial_icp_filter  # noqa: E402


DEFAULT_INPUT = Path.home() / "Desktop" / "scorton_final_ranked_leads.csv"
DEFAULT_OUTPUT = Path.home() / "Desktop" / "scorton_final_ranked_leads.csv"


def clean_ranked_leads(
    input_path: Path,
    output_path: Path,
    *,
    in_place: bool = True,
) -> dict[str, int | list[dict[str, str]]]:
    with input_path.open(newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)
        if not reader.fieldnames:
            raise ValueError("Input CSV is missing a header row")
        fieldnames = list(reader.fieldnames)

        kept: list[dict[str, str]] = []
        removed: list[dict[str, str]] = []
        total_read = 0

        for row in reader:
            total_read += 1
            accepted, reason = passes_financial_icp_filter(row)
            if accepted:
                kept.append(row)
            else:
                removed.append({**row, "removal_reason": reason or "rejected"})

    destination = output_path
    if in_place and output_path == input_path:
        destination = input_path.with_suffix(input_path.suffix + ".tmp")

    with destination.open("w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)

    if in_place and output_path == input_path:
        destination.replace(input_path)

    return {
        "total_read": total_read,
        "kept": len(kept),
        "removed": len(removed),
        "removed_rows": removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove non-financial companies from scorton_final_ranked_leads.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input CSV (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV (default: overwrite --input)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report removals without writing a file",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    output_path = args.output or args.input
    if args.dry_run:
        with args.input.open(newline="", encoding="utf-8-sig") as infile:
            reader = csv.DictReader(infile)
            removed = []
            kept = 0
            total = 0
            for row in reader:
                total += 1
                accepted, reason = passes_financial_icp_filter(row)
                if accepted:
                    kept += 1
                else:
                    removed.append({**row, "removal_reason": reason or "rejected"})
        print(f"[dry-run] Read {total} rows, would keep {kept}, remove {len(removed)}")
        for row in removed:
            print(
                f"  - {row.get('company', row.get('company_name', ''))} "
                f"[{row.get('industry', '')}] ({row.get('removal_reason')})"
            )
        return

    summary = clean_ranked_leads(args.input, output_path, in_place=args.output is None)
    print(
        f"Read {summary['total_read']} rows, kept {summary['kept']}, "
        f"removed {summary['removed']}, wrote {output_path}"
    )
    removed_rows = summary["removed_rows"]
    if removed_rows:
        print("\nRemoved companies:")
        for row in removed_rows:
            print(
                f"  - {row.get('company', row.get('company_name', ''))} "
                f"[{row.get('industry', '')}] ({row.get('removal_reason')})"
            )


if __name__ == "__main__":
    main()
