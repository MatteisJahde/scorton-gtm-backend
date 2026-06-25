#!/usr/bin/env python3
"""Verify lead domains via the Render backend (for blocked local HTTP)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RENDER_API = "https://scorton-gtm-backend.onrender.com"
DEFAULT_ENDPOINT_PATH = "/api/verify-leads-csv"
DEFAULT_TIMEOUT_SECONDS = 900


def verify_via_render(
    *,
    input_path: Path,
    output_path: Path,
    api_base_url: str,
    timeout_seconds: int,
) -> dict:
    endpoint = f"{api_base_url.rstrip('/')}{DEFAULT_ENDPOINT_PATH}"
    print(f"Uploading {input_path} to {endpoint}")

    with input_path.open("rb") as handle:
        response = requests.post(
            endpoint,
            files={"file": (input_path.name, handle, "text/csv")},
            timeout=timeout_seconds,
        )

    if response.status_code != 200:
        detail = response.text
        try:
            detail = json.dumps(response.json(), indent=2)
        except ValueError:
            pass
        raise SystemExit(f"Verification failed ({response.status_code}):\n{detail}")

    payload = response.json()
    if not payload.get("success"):
        raise SystemExit(f"Verification failed: {json.dumps(payload, indent=2)}")

    data = payload.get("data") or {}
    csv_content = data.get("csv")
    if csv_content is None:
        raise SystemExit("Verification response missing CSV data.")

    output_path.write_text(csv_content, encoding="utf-8")
    return data.get("summary") or {}, data.get("discarded") or []


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload filtered_leads.csv to Render for domain verification."
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
        "--api-base-url",
        default=DEFAULT_RENDER_API,
        help=f"Render API base URL (default: {DEFAULT_RENDER_API})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    summary, discarded = verify_via_render(
        input_path=args.input,
        output_path=args.output,
        api_base_url=args.api_base_url,
        timeout_seconds=args.timeout,
    )

    print("\nDomain verification summary")
    print(f"  Read:      {summary.get('read', 0)}")
    print(f"  Kept:      {summary.get('kept', 0)} (VALID)")
    print(f"  Discarded: {summary.get('discarded', 0)} (DEAD)")
    print(f"  Output:    {args.output}")

    if discarded:
        print("\nDiscarded leads (dead domains):")
        for row in discarded:
            print(
                f"  - {row.get('company', '')} "
                f"({row.get('website', '')}) "
                f"[{row.get('validation_detail', 'unknown')}]"
            )


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
