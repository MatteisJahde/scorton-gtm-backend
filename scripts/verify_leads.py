#!/usr/bin/env python3
"""Verify website reachability for filtered leads before production export."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]

REQUEST_TIMEOUT_SECONDS = 5
USER_AGENT = "ScortonGTMLeadBot/1.0"

STATUS_VALID = "VALID"
STATUS_DEAD = "DEAD"


def normalize_website(raw: str) -> str:
    website = (raw or "").strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def website_has_valid_format(website: str) -> bool:
    parsed = urlparse(normalize_website(website))
    return bool(parsed.netloc and "." in parsed.netloc)


def check_website_status(website: str, *, trust_env: bool = False) -> tuple[str, str]:
    """
    Perform an HTTP HEAD request and return (validation_status, detail).

    VALID  -> status code 200-299
    DEAD   -> timeout, connection error, 4xx, 5xx, or invalid URL
    """
    if not website_has_valid_format(website):
        return STATUS_DEAD, "invalid_url"

    url = normalize_website(website)
    session = requests.Session()
    session.trust_env = trust_env
    try:
        response = session.head(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        if 200 <= response.status_code < 300:
            return STATUS_VALID, f"http_{response.status_code}"
        return STATUS_DEAD, f"http_{response.status_code}"
    except requests.Timeout:
        return STATUS_DEAD, "timeout"
    except requests.RequestException as exc:
        return STATUS_DEAD, type(exc).__name__
    finally:
        session.close()


def verify_leads(
    input_path: Path,
    output_path: Path,
    *,
    trust_env: bool = False,
    show_progress: bool = True,
) -> tuple[int, int, list[dict[str, str]]]:
    """Return (rows_read, rows_kept, dead_rows)."""
    with input_path.open(newline="", encoding="utf-8") as infile:
        reader = csv.DictReader(infile)
        if not reader.fieldnames or "website" not in reader.fieldnames:
            raise ValueError("Input CSV must include a 'website' column")

        fieldnames = reader.fieldnames
        rows = list(reader)
        total_read = len(rows)
        valid_rows: list[dict[str, str]] = []
        dead_rows: list[dict[str, str]] = []

        for index, row in enumerate(rows, start=1):
            company = row.get("company", "")
            website = row.get("website", "")
            status, detail = check_website_status(website, trust_env=trust_env)
            if show_progress:
                print(f"[{index}/{total_read}] {company} -> {status} ({detail})")

            if status == STATUS_VALID:
                valid_rows.append(row)
            else:
                dead_rows.append(
                    {
                        **row,
                        "validation_status": status,
                        "validation_detail": detail,
                    }
                )

    with output_path.open("w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(valid_rows)

    return total_read, len(valid_rows), dead_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify website connectivity for filtered_leads.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "filtered_leads.csv",
        help="Input CSV path (default: filtered_leads.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "verified_leads.csv",
        help="Output CSV path (default: verified_leads.csv)",
    )
    parser.add_argument(
        "--trust-env",
        action="store_true",
        help="Use HTTP_PROXY/HTTPS_PROXY environment variables (default: direct connection)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide per-domain progress output",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    total, kept, dead = verify_leads(
        args.input,
        args.output,
        trust_env=args.trust_env,
        show_progress=not args.quiet,
    )
    discarded = len(dead)

    print("Domain verification summary")
    print(f"  Read:      {total}")
    print(f"  Kept:      {kept} (VALID)")
    print(f"  Discarded: {discarded} (DEAD)")
    print(f"  Output:    {args.output}")

    if dead:
        print("\nDiscarded leads (dead domains):")
        for row in dead:
            print(
                f"  - {row.get('company', '')} "
                f"({row.get('website', '')}) "
                f"[{row.get('validation_detail', 'unknown')}]"
            )


if __name__ == "__main__":
    main()
