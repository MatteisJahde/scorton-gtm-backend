"""
Lead email verification for CSV ingestion and enrichment.

Uses Hunter.io when ``HUNTER_API_KEY`` is set; otherwise falls back to the
local verifier mock (syntax + deterministic deliverability simulation).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.hunter_client import HunterVerificationError, get_hunter_api_key, hunter_verify_email
from services.verifier import (
    check_email_syntax,
    mock_zerobounce_verify,
    passes_qualification,
    _status_from_result,
)
from services.zerobounce_client import (
    ZeroBounceVerificationError,
    get_zerobounce_api_key,
    zerobounce_verify_email,
)

# Normalized statuses stored on leads / target dataset exports.
VERIFICATION_STATUS_VERIFIED = "verified"
VERIFICATION_STATUS_CATCH_ALL = "catch_all"
VERIFICATION_STATUS_RISKY = "risky"
VERIFICATION_STATUS_INVALID = "invalid"
VERIFICATION_STATUS_UNKNOWN = "unknown"
VERIFICATION_STATUS_SYNTAX_ERROR = "syntax_error"
VERIFICATION_STATUS_ERROR = "error"

QUALIFIED_VERIFICATION_STATUSES = {
    VERIFICATION_STATUS_VERIFIED,
    VERIFICATION_STATUS_CATCH_ALL,
}


def _email_status_from_verification_status(verification_status: str) -> str:
    mapping = {
        VERIFICATION_STATUS_VERIFIED: "Verified",
        VERIFICATION_STATUS_CATCH_ALL: "Catch-all",
        VERIFICATION_STATUS_RISKY: "Risky",
        VERIFICATION_STATUS_INVALID: "Invalid",
        VERIFICATION_STATUS_UNKNOWN: "Unverified",
        VERIFICATION_STATUS_SYNTAX_ERROR: "Syntax Error",
        VERIFICATION_STATUS_ERROR: "Unverified",
    }
    return mapping.get(verification_status, "Unverified")


def _verification_status_from_hunter(hunter_result: Dict[str, Any]) -> str:
    status = hunter_result.get("status") or ""
    result = hunter_result.get("result") or ""

    if hunter_result.get("disposable"):
        return VERIFICATION_STATUS_INVALID

    if status == "valid" and result == "deliverable":
        return VERIFICATION_STATUS_VERIFIED

    if status == "accept_all" or hunter_result.get("accept_all"):
        return VERIFICATION_STATUS_CATCH_ALL

    if status in {"invalid", "disposable"} or result == "undeliverable":
        return VERIFICATION_STATUS_INVALID

    if result == "risky" or status == "webmail":
        return VERIFICATION_STATUS_RISKY

    return VERIFICATION_STATUS_UNKNOWN


def _verification_status_from_zerobounce(zb_result: Dict[str, Any]) -> str:
    status = (zb_result.get("status") or "").lower()
    if status == "valid":
        return VERIFICATION_STATUS_VERIFIED
    if status == "catch-all":
        return VERIFICATION_STATUS_CATCH_ALL
    if status in {"invalid", "spamtrap", "abuse", "do_not_mail"}:
        return VERIFICATION_STATUS_INVALID
    if status == "unknown":
        return VERIFICATION_STATUS_UNKNOWN
    return VERIFICATION_STATUS_RISKY


def _verification_status_from_mock(mock_result: Dict[str, Any]) -> str:
    email_status = _status_from_result(mock_result)
    mapping = {
        "Verified": VERIFICATION_STATUS_VERIFIED,
        "Catch-all": VERIFICATION_STATUS_CATCH_ALL,
        "Risky": VERIFICATION_STATUS_RISKY,
        "Invalid": VERIFICATION_STATUS_INVALID,
        "Syntax Error": VERIFICATION_STATUS_SYNTAX_ERROR,
        "Unverified": VERIFICATION_STATUS_UNKNOWN,
    }
    return mapping.get(email_status, VERIFICATION_STATUS_UNKNOWN)


def verify_lead_email(email: str, *, seed: int = 0) -> Dict[str, Any]:
    """
    Verify a lead work email for ingestion.

    Returns:
        qualified: whether the row should be kept
        verification_status: normalized status for output
        email_status: human-readable status (legacy field)
        provider: hunter | mock
        verification: raw provider payload
    """
    normalized_email = (email or "").strip()
    if not normalized_email:
        return {
            "email": normalized_email,
            "qualified": False,
            "verification_status": VERIFICATION_STATUS_INVALID,
            "email_status": "Invalid",
            "provider": None,
            "verification": {},
            "detail": "missing_email",
        }

    if not check_email_syntax(normalized_email):
        return {
            "email": normalized_email,
            "qualified": False,
            "verification_status": VERIFICATION_STATUS_SYNTAX_ERROR,
            "email_status": "Syntax Error",
            "provider": "syntax",
            "verification": {"reason": "syntax_error"},
            "detail": "syntax_error",
        }

    hunter_key = get_hunter_api_key()
    if hunter_key:
        try:
            hunter_result = hunter_verify_email(normalized_email, api_key=hunter_key)
            verification_status = _verification_status_from_hunter(hunter_result)
            qualified = verification_status in QUALIFIED_VERIFICATION_STATUSES
            return {
                "email": normalized_email,
                "qualified": qualified,
                "verification_status": verification_status,
                "email_status": _email_status_from_verification_status(verification_status),
                "provider": "hunter",
                "verification": hunter_result,
                "detail": hunter_result.get("result") or hunter_result.get("status"),
            }
        except HunterVerificationError as exc:
            return {
                "email": normalized_email,
                "qualified": False,
                "verification_status": VERIFICATION_STATUS_ERROR,
                "email_status": "Unverified",
                "provider": "hunter",
                "verification": {"error": str(exc)},
                "detail": str(exc),
            }

    zerobounce_key = get_zerobounce_api_key()
    if zerobounce_key:
        try:
            zb_result = zerobounce_verify_email(normalized_email, api_key=zerobounce_key)
            verification_status = _verification_status_from_zerobounce(zb_result)
            qualified = verification_status in QUALIFIED_VERIFICATION_STATUSES
            return {
                "email": normalized_email,
                "qualified": qualified,
                "verification_status": verification_status,
                "email_status": _email_status_from_verification_status(verification_status),
                "provider": "zerobounce",
                "verification": zb_result,
                "detail": zb_result.get("status"),
            }
        except ZeroBounceVerificationError as exc:
            return {
                "email": normalized_email,
                "qualified": False,
                "verification_status": VERIFICATION_STATUS_ERROR,
                "email_status": "Unverified",
                "provider": "zerobounce",
                "verification": {"error": str(exc)},
                "detail": str(exc),
            }

    mock_result = mock_zerobounce_verify(normalized_email, seed=seed)
    verification_status = _verification_status_from_mock(mock_result)
    qualified = passes_qualification(mock_result)
    return {
        "email": normalized_email,
        "qualified": qualified,
        "verification_status": verification_status,
        "email_status": _status_from_result(mock_result),
        "provider": "mock",
        "verification": mock_result,
        "detail": mock_result.get("reason"),
    }


def preverified_email_result(
    work_email: str,
    verification_status: str,
    *,
    email_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Build enrichment email_result dict from CSV pre-verification."""
    status = verification_status or VERIFICATION_STATUS_UNKNOWN
    return {
        "work_email": work_email,
        "email_status": email_status or _email_status_from_verification_status(status),
        "verification_status": status,
        "qualified": status in QUALIFIED_VERIFICATION_STATUSES,
        "verification": {"source": "csv_preverified", "verification_status": status},
        "attempts": [],
        "notes_flag": None,
    }
