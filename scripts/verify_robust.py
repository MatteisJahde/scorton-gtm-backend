#!/usr/bin/env python3
"""
Robust local domain verification with batching, resume support, and SSL fixes.

Reads filtered_leads.csv, verifies websites in batches, and appends VALID rows
to verified_leads.csv after each batch. Re-run safely after crashes.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import certifi
import requests

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BATCH_SIZE = 10
REQUEST_TIMEOUT_SECONDS = 5
USER_AGENT = "Mozilla/5.0 (compatible; ScortonGTMLeadBot/1.0; +https://scorton.ai)"

STATUS_VALID = "VALID"
STATUS_DEAD = "DEAD"

# urllib3 emits this on macOS when Python is linked against LibreSSL.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*",
    category=Warning,
)


def configure_ssl_environment() -> str:
    """Point TLS verification at the certifi CA bundle."""
    ca_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
    return ca_bundle


def normalize_website(raw: str) -> str:
    website = (raw or "").strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website.rstrip("/")


def website_key(raw: str) -> str:
    return normalize_website(raw).lower()


def website_has_valid_format(website: str) -> bool:
    parsed = urlparse(normalize_website(website))
    return bool(parsed.netloc and "." in parsed.netloc)


def display_domain(website: str) -> str:
    parsed = urlparse(normalize_website(website))
    return parsed.netloc or website


def build_session(*, trust_env: bool = True) -> requests.Session:
    session = requests.Session()
    session.trust_env = trust_env
    session.verify = configure_ssl_environment()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def check_website_active(session: requests.Session, website: str) -> tuple[str, str]:
    """
    Try HEAD, then GET. Never raises — returns (VALID|DEAD, detail).
    """
    try:
        if not website_has_valid_format(website):
            return STATUS_DEAD, "invalid_url"

        url = normalize_website(website)
        for method in ("head", "get"):
            try:
                if method == "head":
                    response = session.head(
                        url,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                        allow_redirects=True,
                    )
                else:
                    response = session.get(
                        url,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                        allow_redirects=True,
                        stream=True,
                    )
                    response.close()

                if 200 <= response.status_code < 300:
                    return STATUS_VALID, f"{method}_{response.status_code}"

                if method == "head":
                    continue
                return STATUS_DEAD, f"get_{response.status_code}"
            except requests.Timeout:
                if method == "get":
                    return STATUS_DEAD, "timeout"
            except requests.RequestException as exc:
                if method == "get":
                    return STATUS_DEAD, type(exc).__name__

        return STATUS_DEAD, "unreachable"
    except Exception as exc:  # noqa: BLE001 — per-site guard; keep batch running
        return STATUS_DEAD, f"error:{type(exc).__name__}"


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"processed": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"processed": {}}
    if "processed" not in data or not isinstance(data["processed"], dict):
        return {"processed": {}}
    return data


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_existing_output_websites(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "website" not in reader.fieldnames:
            return set()
        return {website_key(row.get("website", "")) for row in reader if row.get("website")}


def append_valid_rows(
    output_path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> int:
    if not rows:
        return 0

    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_input_rows(input_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with input_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "website" not in reader.fieldnames:
            raise ValueError("Input CSV must include a 'website' column")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    return fieldnames, rows


def verify_robust(
    *,
    input_path: Path,
    output_path: Path,
    state_path: Path,
    batch_size: int,
    trust_env: bool,
) -> dict[str, int]:
    fieldnames, rows = read_input_rows(input_path)
    state = load_state(state_path)
    processed: dict[str, dict[str, str]] = state["processed"]
    already_written = load_existing_output_websites(output_path)

    total = len(rows)
    pending: list[tuple[int, dict[str, str]]] = []
    for index, row in enumerate(rows, start=1):
        key = website_key(row.get("website", ""))
        if key and key in processed:
            continue
        pending.append((index, row))

    session = build_session(trust_env=trust_env)
    kept_this_run = 0
    dead_this_run = 0
    skipped = total - len(pending)

    try:
        for batch_start in range(0, len(pending), batch_size):
            batch = pending[batch_start : batch_start + batch_size]
            batch_valid_rows: list[dict[str, str]] = []

            for position, row in batch:
                company = row.get("company", "").strip() or display_domain(row.get("website", ""))
                website = row.get("website", "")
                domain = display_domain(website)

                status, detail = check_website_active(session, website)
                print(f"[{position}/{total}] {domain} -> {status} ({detail})", flush=True)

                key = website_key(website)
                processed[key] = {
                    "status": status,
                    "detail": detail,
                    "company": company,
                    "website": normalize_website(website),
                }

                if status == STATUS_VALID:
                    kept_this_run += 1
                    if key not in already_written:
                        batch_valid_rows.append(row)
                        already_written.add(key)
                else:
                    dead_this_run += 1

            appended = append_valid_rows(output_path, fieldnames, batch_valid_rows)
            save_state(state_path, {"processed": processed})
            print(
                f"Batch saved: checked {len(batch)}, appended {appended} VALID row(s) "
                f"to {output_path}",
                flush=True,
            )
    finally:
        session.close()
        save_state(state_path, {"processed": processed})

    return {
        "total": total,
        "skipped_already_processed": skipped,
        "checked_this_run": len(pending),
        "kept_this_run": kept_this_run,
        "dead_this_run": dead_this_run,
        "kept_total": sum(1 for item in processed.values() if item.get("status") == STATUS_VALID),
        "dead_total": sum(1 for item in processed.values() if item.get("status") == STATUS_DEAD),
    }


def reset_outputs(output_path: Path, state_path: Path) -> None:
    for path in (output_path, state_path):
        if path.exists():
            path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify filtered lead domains locally with resume support."
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
        help="Output CSV path for VALID rows (default: verified_leads.csv)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Resume state JSON path (default: <output>.state.json)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Domains per persistence batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--no-trust-env",
        action="store_true",
        help="Ignore HTTP_PROXY/HTTPS_PROXY environment variables",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete output + state files before starting",
    )
    args = parser.parse_args()

    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    state_path = args.state_file or args.output.with_suffix(".state.json")
    if args.reset:
        reset_outputs(args.output, state_path)
        print(f"Reset complete: removed {args.output} and {state_path}", flush=True)

    ca_bundle = configure_ssl_environment()
    print(f"Using CA bundle: {ca_bundle}", flush=True)

    summary = verify_robust(
        input_path=args.input,
        output_path=args.output,
        state_path=state_path,
        batch_size=args.batch_size,
        trust_env=not args.no_trust_env,
    )

    print("\nDomain verification summary")
    print(f"  Total input rows:     {summary['total']}")
    print(f"  Skipped (resumed):    {summary['skipped_already_processed']}")
    print(f"  Checked this run:     {summary['checked_this_run']}")
    print(f"  Kept this run:        {summary['kept_this_run']} (VALID)")
    print(f"  Dead this run:        {summary['dead_this_run']} (DEAD)")
    print(f"  Kept overall:         {summary['kept_total']}")
    print(f"  Dead overall:         {summary['dead_total']}")
    print(f"  Output:               {args.output}")
    print(f"  State:                {state_path}")


if __name__ == "__main__":
    main()
