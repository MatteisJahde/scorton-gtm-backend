"""
Multi-stage email validation and hygiene pipeline.

Stages (run in order, short-circuit on hard failures):
  1. Syntax & format validation — reject junk before any API call
  2. Suppression list — drop previously hard-bounced / do-not-mail addresses
  3. Role-account filtering — flag or drop generic inboxes (info@, sales@, …)
  4. SMTP verification — Hunter.io then ZeroBounce waterfall (mailbox-level)
  5. Catch-all routing — accept-all domains go to a Review bucket, not Valid/Risky

Provider waterfall (stage 4):
  Hunter.io email verifier (primary) → if low-confidence / risky / error → ZeroBounce
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from services.hunter_client import HunterVerificationError, get_hunter_api_key, hunter_verify_email
from services.suppression_list import is_suppressed
from services.verifier import check_email_syntax
from services.zerobounce_client import (
    ZeroBounceVerificationError,
    get_zerobounce_api_key,
    zerobounce_verify_email,
)

# Normalized pipeline statuses
STATUS_VERIFIED = "verified"
STATUS_REVIEW = "review"
STATUS_CATCH_ALL = "catch_all"
STATUS_RISKY = "risky"
STATUS_INVALID = "invalid"
STATUS_UNKNOWN = "unknown"
STATUS_SYNTAX_ERROR = "syntax_error"
STATUS_ROLE_ACCOUNT = "role_account"
STATUS_SUPPRESSED = "suppressed"
STATUS_ERROR = "error"

# Leads safe for automated outreach
QUALIFIED_STATUSES = {STATUS_VERIFIED}

# Catch-all domains — keep but require manual review before send
REVIEW_STATUSES = {STATUS_REVIEW, STATUS_CATCH_ALL}

ROLE_ACCOUNT_LOCAL_PARTS = frozenset(
    {
        "info",
        "sales",
        "support",
        "contact",
        "hello",
        "admin",
        "office",
        "marketing",
        "hr",
        "careers",
        "jobs",
        "press",
        "media",
        "billing",
        "accounts",
        "reception",
        "enquiries",
        "inquiry",
        "inquiries",
        "team",
        "help",
        "service",
        "customerservice",
        "customer",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "mail",
        "general",
        "feedback",
        "newsletter",
        "subscribe",
        "unsubscribe",
        "webmaster",
        "postmaster",
        "abuse",
        "privacy",
        "legal",
        "compliance",
        "finance",
        "accounting",
        "ap",
        "ar",
        "orders",
        "order",
        "shop",
        "store",
    }
)

COMMON_DOMAIN_TYPOS = {
    "gmial.com": "gmail.com",
    "gmal.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "hotmal.com": "hotmail.com",
    "hotmial.com": "hotmail.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "outlok.com": "outlook.com",
    "outllok.com": "outlook.com",
}

DISPOSABLE_DOMAIN_HINTS = (
    "mailinator",
    "guerrillamail",
    "tempmail",
    "10minutemail",
    "throwaway",
    "yopmail",
    "trashmail",
)

HUNTER_LOW_CONFIDENCE_SCORE = int(os.getenv("EMAIL_HUNTER_MIN_SCORE", "70"))


def _role_account_action() -> str:
    return (os.getenv("EMAIL_ROLE_ACCOUNT_ACTION") or "drop").strip().lower()


def _email_status_label(status: str) -> str:
    return {
        STATUS_VERIFIED: "Verified",
        STATUS_REVIEW: "Review",
        STATUS_CATCH_ALL: "Review",
        STATUS_RISKY: "Risky",
        STATUS_INVALID: "Invalid",
        STATUS_UNKNOWN: "Unverified",
        STATUS_SYNTAX_ERROR: "Syntax Error",
        STATUS_ROLE_ACCOUNT: "Role Account",
        STATUS_SUPPRESSED: "Suppressed",
        STATUS_ERROR: "Unverified",
    }.get(status, "Unverified")


def normalize_email(email: object) -> str:
    return str(email or "").strip().lower()


def validate_syntax(email: str) -> dict[str, Any]:
    """Stage 1: syntax, format, and obvious typo checks."""
    if not email:
        return {"passed": False, "reason": "missing_email", "status": STATUS_INVALID}

    if not check_email_syntax(email):
        return {"passed": False, "reason": "syntax_error", "status": STATUS_SYNTAX_ERROR}

    local, _, domain = email.partition("@")
    if len(local) > 64 or len(domain) > 255:
        return {"passed": False, "reason": "length_exceeded", "status": STATUS_SYNTAX_ERROR}

    if domain in COMMON_DOMAIN_TYPOS:
        return {
            "passed": False,
            "reason": "domain_typo",
            "status": STATUS_SYNTAX_ERROR,
            "suggested_domain": COMMON_DOMAIN_TYPOS[domain],
        }

    if any(hint in domain for hint in DISPOSABLE_DOMAIN_HINTS):
        return {"passed": False, "reason": "disposable_domain", "status": STATUS_INVALID}

    if re.search(r"[^a-z0-9._%+\-]", local):
        return {"passed": False, "reason": "invalid_local_chars", "status": STATUS_SYNTAX_ERROR}

    return {"passed": True, "reason": "ok", "status": STATUS_VERIFIED}


def is_role_account(email: str) -> bool:
    """Stage 2: detect generic / role-based inboxes."""
    local = normalize_email(email).split("@", 1)[0]
    base = local.split("+", 1)[0]
    if base in ROLE_ACCOUNT_LOCAL_PARTS:
        return True
    for prefix in ROLE_ACCOUNT_LOCAL_PARTS:
        if base.startswith(f"{prefix}.") or base.startswith(f"{prefix}_"):
            return True
    return False


def _hunter_smtp_passed(hunter_result: dict[str, Any]) -> bool:
    smtp = hunter_result.get("smtp_check")
    if smtp is True:
        return True
    if str(smtp).lower() in {"true", "passed", "ok"}:
        return True
    mx = hunter_result.get("mx_records")
    return bool(mx) and hunter_result.get("regexp") is True


def _status_from_hunter(hunter_result: dict[str, Any]) -> str:
    status = str(hunter_result.get("status") or "").lower()
    result = str(hunter_result.get("result") or "").lower()

    if hunter_result.get("disposable"):
        return STATUS_INVALID

    if status == "accept_all" or hunter_result.get("accept_all"):
        return STATUS_REVIEW

    if status == "valid" and result == "deliverable":
        if _hunter_smtp_passed(hunter_result):
            return STATUS_VERIFIED
        return STATUS_RISKY

    if status in {"invalid", "disposable"} or result == "undeliverable":
        return STATUS_INVALID

    if result == "risky" or status == "webmail":
        return STATUS_RISKY

    return STATUS_UNKNOWN


def _status_from_zerobounce(zb_result: dict[str, Any]) -> str:
    status = str(zb_result.get("status") or "").lower()
    sub_status = str(zb_result.get("sub_status") or "").lower()

    if status in {"invalid", "spamtrap", "abuse", "do_not_mail"}:
        return STATUS_INVALID
    if status == "catch-all" or sub_status == "accept_all":
        return STATUS_REVIEW
    if status == "valid":
        if str(zb_result.get("mx_found")).lower() in {"true", "yes"}:
            return STATUS_VERIFIED
        return STATUS_RISKY
    if status == "unknown":
        return STATUS_UNKNOWN
    return STATUS_RISKY


def _hunter_needs_waterfall(hunter_result: dict[str, Any], status: str) -> bool:
    if status in {STATUS_VERIFIED, STATUS_REVIEW}:
        score = hunter_result.get("score")
        if score is not None and int(score) < HUNTER_LOW_CONFIDENCE_SCORE:
            return True
        return False
    return status in {STATUS_RISKY, STATUS_UNKNOWN, STATUS_ERROR}


def _verify_smtp_hunter(email: str) -> dict[str, Any]:
    result = hunter_verify_email(email)
    status = _status_from_hunter(result)
    return {
        "provider": "hunter",
        "verification_status": status,
        "verification": result,
        "smtp_check": _hunter_smtp_passed(result),
        "detail": result.get("result") or result.get("status"),
        "score": result.get("score"),
    }


def _verify_smtp_zerobounce(email: str) -> dict[str, Any]:
    result = zerobounce_verify_email(email)
    status = _status_from_zerobounce(result)
    return {
        "provider": "zerobounce",
        "verification_status": status,
        "verification": result,
        "smtp_check": str(result.get("mx_found")).lower() in {"true", "yes"},
        "detail": result.get("status"),
        "score": None,
    }


def verify_smtp_depth(email: str) -> dict[str, Any]:
    """
    Stage 4: SMTP-level verification with provider waterfall.

    Hunter (mailbox SMTP handshake) → ZeroBounce when Hunter is inconclusive.
    """
    hunter_key = get_hunter_api_key()
    zerobounce_key = get_zerobounce_api_key()
    attempts: list[dict[str, Any]] = []

    if hunter_key:
        try:
            hunter_attempt = _verify_smtp_hunter(email)
            attempts.append(hunter_attempt)
            if not _hunter_needs_waterfall(
                hunter_attempt["verification"],
                hunter_attempt["verification_status"],
            ):
                return {**hunter_attempt, "attempts": attempts, "verification_depth": "smtp"}
        except HunterVerificationError as exc:
            attempts.append({"provider": "hunter", "error": str(exc)})

    if zerobounce_key:
        try:
            zb_attempt = _verify_smtp_zerobounce(email)
            attempts.append(zb_attempt)
            return {**zb_attempt, "attempts": attempts, "verification_depth": "smtp"}
        except ZeroBounceVerificationError as exc:
            attempts.append({"provider": "zerobounce", "error": str(exc)})

    if attempts:
        last = attempts[-1]
        return {
            "provider": last.get("provider"),
            "verification_status": STATUS_ERROR,
            "verification": last,
            "smtp_check": False,
            "detail": last.get("error", "verification_failed"),
            "attempts": attempts,
            "verification_depth": "smtp",
        }

    return {
        "provider": None,
        "verification_status": STATUS_UNKNOWN,
        "verification": {"reason": "no_verification_provider"},
        "smtp_check": False,
        "detail": "no_provider",
        "attempts": attempts,
        "verification_depth": "none",
    }


def run_email_hygiene_pipeline(
    email: str,
    *,
    seed: int = 0,
    skip_role_filter: bool = False,
) -> dict[str, Any]:
    """
    Run the full multi-stage hygiene pipeline for one email address.

    Returns a dict with:
      - qualified: True only for SMTP-verified personal mailboxes
      - needs_review: True for catch-all / review bucket
      - verification_status, email_status, stages, provider
    """
    _ = seed  # reserved for mock fallback compatibility
    normalized = normalize_email(email)
    stages: list[dict[str, str]] = []

    syntax = validate_syntax(normalized)
    stages.append({"stage": "syntax", "result": syntax["reason"]})
    if not syntax["passed"]:
        status = syntax["status"]
        return _pipeline_result(
            email=normalized,
            status=status,
            qualified=False,
            needs_review=False,
            provider="syntax",
            stages=stages,
            verification={},
            detail=syntax["reason"],
        )

    if is_suppressed(normalized):
        stages.append({"stage": "suppression", "result": "suppressed"})
        return _pipeline_result(
            email=normalized,
            status=STATUS_SUPPRESSED,
            qualified=False,
            needs_review=False,
            provider="suppression",
            stages=stages,
            verification={"reason": "hard_bounce_or_suppressed"},
            detail="suppressed",
        )

    if not skip_role_filter and is_role_account(normalized):
        stages.append({"stage": "role_account", "result": "role_inbox"})
        action = _role_account_action()
        if action == "review":
            return _pipeline_result(
                email=normalized,
                status=STATUS_ROLE_ACCOUNT,
                qualified=False,
                needs_review=True,
                provider="role_filter",
                stages=stages,
                verification={"reason": "role_account"},
                detail="role_account_review",
            )
        return _pipeline_result(
            email=normalized,
            status=STATUS_ROLE_ACCOUNT,
            qualified=False,
            needs_review=False,
            provider="role_filter",
            stages=stages,
            verification={"reason": "role_account"},
            detail="role_account_dropped",
        )

    smtp = verify_smtp_depth(normalized)
    stages.append(
        {
            "stage": "smtp",
            "result": smtp.get("verification_status") or STATUS_UNKNOWN,
            "provider": str(smtp.get("provider") or ""),
        }
    )

    status = str(smtp.get("verification_status") or STATUS_UNKNOWN)
    needs_review = status in REVIEW_STATUSES
    qualified = status in QUALIFIED_STATUSES

    return _pipeline_result(
        email=normalized,
        status=status,
        qualified=qualified,
        needs_review=needs_review,
        provider=str(smtp.get("provider") or ""),
        stages=stages,
        verification=smtp.get("verification") or {},
        detail=str(smtp.get("detail") or ""),
        smtp_check=bool(smtp.get("smtp_check")),
        attempts=smtp.get("attempts") or [],
        verification_depth=smtp.get("verification_depth"),
    )


def _pipeline_result(
    *,
    email: str,
    status: str,
    qualified: bool,
    needs_review: bool,
    provider: str,
    stages: list[dict[str, str]],
    verification: dict[str, Any],
    detail: str,
    smtp_check: bool = False,
    attempts: Optional[list] = None,
    verification_depth: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "email": email,
        "qualified": qualified,
        "needs_review": needs_review,
        "verification_status": status,
        "email_status": _email_status_label(status),
        "provider": provider,
        "verification": verification,
        "detail": detail,
        "stages": stages,
        "smtp_check": smtp_check,
        "attempts": attempts or [],
        "verification_depth": verification_depth,
    }
