"""
Async ZeroBounce email validation (httpx).

Adds verification metadata without overwriting CSV-sourced extras fields.
"""

from __future__ import annotations

import traceback
from typing import Any

import httpx

from services.zerobounce_client import ZEROBOUNCE_VALIDATE_URL, get_zerobounce_api_key

ZEROBOUNCE_RATE_LIMIT_DELAY_SECONDS = 0.1

# Keys populated from CSV validation — never overwritten by ZeroBounce.
CSV_PROTECTED_EXTRAS_KEYS = frozenset(
    {
        "work_email",
        "buyer_name",
        "job_title",
        "intent",
        "signal_score",
        "lead_verification_status",
        "verification_status",
        "email_status",
        "contact_verification_status",
        "email_provider",
        "contact_provider",
        "website",
    }
)


def email_status_from_zerobounce_payload(payload: dict[str, Any]) -> str:
    """Map ZeroBounce API status to dashboard email_status labels."""
    status = str(payload.get("status") or "").lower()
    if status == "valid":
        return "Verified"
    if status == "catch-all":
        return "Review"
    if status in {"invalid", "spamtrap", "abuse", "do_not_mail"}:
        return "Invalid"
    if status == "unknown":
        return "Unverified"
    return "Risky"


async def verify_email_with_zerobounce(email: str) -> dict[str, Any]:
    """Validate a single email via the ZeroBounce API (async httpx)."""
    normalized = (email or "").strip()
    if not normalized:
        return {
            "email": normalized,
            "email_status": "Invalid",
            "zerobounce_status": "missing_email",
            "qualified": False,
        }

    api_key = get_zerobounce_api_key()
    if not api_key:
        print("[zerobounce] ZEROBOUNCE_API_KEY is not configured — skipping validation", flush=True)
        return {
            "email": normalized,
            "email_status": "Unverified",
            "zerobounce_status": "missing_api_key",
            "qualified": False,
        }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                ZEROBOUNCE_VALIDATE_URL,
                params={"api_key": api_key, "email": normalized},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))

        email_status = email_status_from_zerobounce_payload(payload)
        return {
            "email": normalized,
            "email_status": email_status,
            "zerobounce_status": payload.get("status"),
            "zerobounce_sub_status": payload.get("sub_status"),
            "verification_status": str(payload.get("status") or "").lower(),
            "qualified": email_status in {"Verified", "Review"},
            "zerobounce": payload,
        }
    except Exception as exc:
        print(f"[zerobounce] Verification failed for {normalized}: {exc}", flush=True)
        traceback.print_exc()
        return {
            "email": normalized,
            "email_status": "Unverified",
            "zerobounce_status": "error",
            "verification_status": "error",
            "qualified": False,
            "error": str(exc),
        }


def apply_zerobounce_to_extras(extras: dict[str, Any], result: dict[str, Any]) -> None:
    """
    Attach ZeroBounce fields to csv extras.

    Existing CSV fields (email_status, lead_verification_status, etc.) are preserved.
    """
    extras["zerobounce_status"] = result.get("zerobounce_status")
    extras["zerobounce_sub_status"] = result.get("zerobounce_sub_status")
    extras["zerobounce_email_status"] = result.get("email_status")
    extras["zerobounce_verification_status"] = result.get("verification_status")
    if result.get("zerobounce") is not None:
        extras["zerobounce"] = result.get("zerobounce")
