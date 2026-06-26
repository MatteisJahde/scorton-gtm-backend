"""
Hunter.io email verification API client.

Docs: https://hunter.io/api-documentation/v2#email-verifier
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

HUNTER_EMAIL_VERIFIER_URL = "https://api.hunter.io/v2/email-verifier"
HUNTER_DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"
DEFAULT_TIMEOUT_SECONDS = 20


class HunterVerificationError(Exception):
    """Raised when Hunter.io returns an error response."""


def get_hunter_api_key() -> Optional[str]:
    return (os.getenv("HUNTER_API_KEY") or os.getenv("HUNTER_IO_API_KEY") or "").strip() or None


def hunter_verify_email(email: str, *, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Verify a single email via Hunter.io.

    Returns normalized verification payload with raw Hunter fields under ``hunter``.
    """
    key = api_key or get_hunter_api_key()
    if not key:
        raise HunterVerificationError("HUNTER_API_KEY is not configured")

    response = requests.get(
        HUNTER_EMAIL_VERIFIER_URL,
        params={"email": email.strip(), "api_key": key},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HunterVerificationError(f"Invalid JSON from Hunter.io: {response.text[:200]}") from exc

    if response.status_code != 200:
        errors = payload.get("errors") or []
        message = errors[0].get("details") if errors else response.text[:200]
        raise HunterVerificationError(f"Hunter.io HTTP {response.status_code}: {message}")

    data = payload.get("data") or {}
    status = str(data.get("status") or "").lower()
    result = str(data.get("result") or "").lower()

    return {
        "provider": "hunter",
        "status": status,
        "result": result,
        "score": data.get("score"),
        "accept_all": bool(data.get("accept_all")),
        "disposable": bool(data.get("disposable")),
        "webmail": bool(data.get("webmail")),
        "smtp_check": data.get("smtp_check"),
        "mx_records": data.get("mx_records"),
        "regexp": data.get("regexp"),
        "gibberish": data.get("gibberish"),
        "hunter": data,
    }


class HunterDomainSearchError(Exception):
    """Raised when Hunter.io domain search fails."""


def hunter_domain_search(
    domain: str,
    *,
    api_key: Optional[str] = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Return Hunter.io domain-search payload for a company domain."""
    key = api_key or get_hunter_api_key()
    if not key:
        raise HunterDomainSearchError("HUNTER_API_KEY is not configured")

    response = requests.get(
        HUNTER_DOMAIN_SEARCH_URL,
        params={
            "domain": domain.strip().lower(),
            "api_key": key,
            "limit": limit,
        },
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HunterDomainSearchError(
            f"Invalid JSON from Hunter.io: {response.text[:200]}"
        ) from exc

    if response.status_code != 200:
        errors = payload.get("errors") or []
        message = errors[0].get("details") if errors else response.text[:200]
        raise HunterDomainSearchError(f"Hunter.io HTTP {response.status_code}: {message}")

    return payload.get("data") or {}
