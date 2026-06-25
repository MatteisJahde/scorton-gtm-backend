"""HTTP domain reachability checks for lead CSV verification."""

from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import requests

REQUEST_TIMEOUT_SECONDS = 5
USER_AGENT = "ScortonGTMLeadBot/1.0"
DEFAULT_MAX_WORKERS = 10

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


def check_website_active(website: str) -> tuple[bool, str]:
    """
    Check whether a website responds successfully.

    Tries HEAD first, then falls back to GET on failure or non-2xx.
    Returns (is_active, detail).
    """
    if not website_has_valid_format(website):
        return False, "invalid_url"

    url = normalize_website(website)
    headers = {"User-Agent": USER_AGENT}
    session = requests.Session()
    session.trust_env = False

    try:
        for method in ("head", "get"):
            try:
                if method == "head":
                    response = session.head(
                        url,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                        allow_redirects=True,
                        headers=headers,
                    )
                else:
                    response = session.get(
                        url,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                        allow_redirects=True,
                        stream=True,
                        headers=headers,
                    )
                    response.close()

                if 200 <= response.status_code < 300:
                    return True, f"{method}_{response.status_code}"

                if method == "head":
                    continue
                return False, f"get_{response.status_code}"
            except requests.Timeout:
                if method == "get":
                    return False, "timeout"
            except requests.RequestException as exc:
                if method == "get":
                    return False, type(exc).__name__
        return False, "unreachable"
    finally:
        session.close()


def _check_row(row: dict[str, str]) -> tuple[dict[str, str], bool, str]:
    is_active, detail = check_website_active(row.get("website", ""))
    return row, is_active, detail


def verify_lead_rows(
    rows: list[dict[str, str]],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, Any]:
    """Verify website column for CSV rows; return only active rows plus report."""
    if not rows:
        return {
            "valid_rows": [],
            "discarded_rows": [],
            "summary": {"read": 0, "kept": 0, "discarded": 0},
        }

    valid_rows: list[dict[str, str]] = []
    discarded_rows: list[dict[str, str]] = []

    workers = max(1, min(max_workers, len(rows)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_check_row, row) for row in rows]
        for future in as_completed(futures):
            row, is_active, detail = future.result()
            company = row.get("company", "")
            website = row.get("website", "")
            if is_active:
                valid_rows.append(row)
                print(f"[verify] {company} ({website}) -> VALID ({detail})", flush=True)
            else:
                discarded_rows.append(
                    {
                        **row,
                        "validation_status": STATUS_DEAD,
                        "validation_detail": detail,
                    }
                )
                print(f"[verify] {company} ({website}) -> DEAD ({detail})", flush=True)

    valid_rows.sort(key=lambda row: (row.get("company", "").lower(), row.get("website", "")))
    discarded_rows.sort(
        key=lambda row: (row.get("company", "").lower(), row.get("website", ""))
    )

    return {
        "valid_rows": valid_rows,
        "discarded_rows": discarded_rows,
        "summary": {
            "read": len(rows),
            "kept": len(valid_rows),
            "discarded": len(discarded_rows),
        },
    }


def verify_csv_bytes(
    payload: bytes,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, Any]:
    """Parse uploaded CSV bytes, verify domains, and return cleaned CSV text."""
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "website" not in reader.fieldnames:
        raise ValueError("CSV must include a 'website' column")

    fieldnames = list(reader.fieldnames)
    rows = list(reader)
    result = verify_lead_rows(rows, max_workers=max_workers)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(result["valid_rows"])

    return {
        **result,
        "fieldnames": fieldnames,
        "csv": output.getvalue(),
    }
