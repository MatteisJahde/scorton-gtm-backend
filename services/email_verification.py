"""
Lead email verification for CSV ingestion and enrichment.

Delegates to the multi-stage ``email_hygiene`` pipeline (syntax → suppression →
role filter → SMTP verification with Hunter/ZeroBounce waterfall).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.email_hygiene import (
    QUALIFIED_STATUSES,
    REVIEW_STATUSES,
    STATUS_CATCH_ALL,
    STATUS_REVIEW,
    STATUS_SYNTAX_ERROR,
    STATUS_SUPPRESSED,
    STATUS_ROLE_ACCOUNT,
    run_email_hygiene_pipeline,
)

# Backward-compatible aliases
VERIFICATION_STATUS_VERIFIED = "verified"
VERIFICATION_STATUS_CATCH_ALL = "catch_all"
VERIFICATION_STATUS_REVIEW = "review"
VERIFICATION_STATUS_RISKY = "risky"
VERIFICATION_STATUS_INVALID = "invalid"
VERIFICATION_STATUS_UNKNOWN = "unknown"
VERIFICATION_STATUS_SYNTAX_ERROR = "syntax_error"
VERIFICATION_STATUS_ERROR = "error"
VERIFICATION_STATUS_SUPPRESSED = "suppressed"
VERIFICATION_STATUS_ROLE_ACCOUNT = "role_account"

QUALIFIED_VERIFICATION_STATUSES = set(QUALIFIED_STATUSES)


def _email_status_from_verification_status(verification_status: str) -> str:
    from services.email_hygiene import _email_status_label

    return _email_status_label(verification_status)


def verify_lead_email(email: str, *, seed: int = 0) -> Dict[str, Any]:
    """
    Verify a lead work email through the full hygiene pipeline.

    Returns:
        qualified: SMTP-verified personal mailbox (safe for automated outreach)
        needs_review: catch-all or role-account review bucket
        verification_status, email_status, provider, stages
    """
    result = run_email_hygiene_pipeline(email, seed=seed)
    status = result.get("verification_status") or VERIFICATION_STATUS_UNKNOWN
    return {
        "email": result.get("email") or (email or "").strip(),
        "qualified": bool(result.get("qualified")),
        "needs_review": bool(result.get("needs_review")),
        "verification_status": status,
        "email_status": result.get("email_status"),
        "provider": result.get("provider"),
        "verification": result.get("verification") or {},
        "detail": result.get("detail"),
        "stages": result.get("stages") or [],
        "smtp_check": result.get("smtp_check"),
        "attempts": result.get("attempts") or [],
        "verification_depth": result.get("verification_depth"),
    }


def preverified_email_result(
    work_email: str,
    verification_status: str,
    *,
    email_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Build enrichment email_result dict from CSV pre-verification."""
    status = verification_status or VERIFICATION_STATUS_UNKNOWN
    needs_review = status in REVIEW_STATUSES or status == VERIFICATION_STATUS_CATCH_ALL
    return {
        "work_email": work_email,
        "email_status": email_status or _email_status_from_verification_status(status),
        "verification_status": status,
        "qualified": status in QUALIFIED_VERIFICATION_STATUSES,
        "needs_review": needs_review,
        "verification": {"source": "csv_preverified", "verification_status": status},
        "attempts": [],
        "notes_flag": "Review" if needs_review else None,
    }
