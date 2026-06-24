"""
ZeroBounce email validation API client.

Docs: https://www.zerobounce.net/docs/email-validation-api-quickstart/
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

ZEROBOUNCE_VALIDATE_URL = "https://api.zerobounce.net/v2/validate"
DEFAULT_TIMEOUT_SECONDS = 20


class ZeroBounceVerificationError(Exception):
    """Raised when ZeroBounce returns an error response."""


def get_zerobounce_api_key() -> Optional[str]:
    return (os.getenv("ZEROBOUNCE_API_KEY") or os.getenv("ZEROBOUNCE_KEY") or "").strip() or None


def zerobounce_verify_email(email: str, *, api_key: Optional[str] = None) -> Dict[str, Any]:
    key = api_key or get_zerobounce_api_key()
    if not key:
        raise ZeroBounceVerificationError("ZEROBOUNCE_API_KEY is not configured")

    response = requests.get(
        ZEROBOUNCE_VALIDATE_URL,
        params={"api_key": key, "email": email.strip()},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ZeroBounceVerificationError(
            f"Invalid JSON from ZeroBounce: {response.text[:200]}"
        ) from exc

    if response.status_code != 200:
        message = payload.get("error") or response.text[:200]
        raise ZeroBounceVerificationError(f"ZeroBounce HTTP {response.status_code}: {message}")

    status = str(payload.get("status") or "").lower()
    sub_status = str(payload.get("sub_status") or "").lower()

    return {
        "provider": "zerobounce",
        "status": status,
        "sub_status": sub_status,
        "account": payload.get("account"),
        "domain": payload.get("domain"),
        "did_you_mean": payload.get("did_you_mean"),
        "free_email": bool(payload.get("free_email")),
        "mx_found": payload.get("mx_found"),
        "smtp_provider": payload.get("smtp_provider"),
        "zerobounce": payload,
    }
