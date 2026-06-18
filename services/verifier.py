"""
Email verification engine.

Performs syntax validation and mock third-party verification
(Hunter / ZeroBounce / NeverBounce style) before persisting outreach emails.
"""

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

# Valid and Catch-all pass; Risky passes only above score threshold
QUALIFICATION_STATUSES = {"Verified", "Catch-all"}
RISKY_SCORE_THRESHOLD = 70

EMAIL_SYNTAX_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9._%+\-]*[a-zA-Z0-9])?@[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$"
)

DELIVERABILITY_VALID = "Valid"
DELIVERABILITY_RISKY = "Risky"
DELIVERABILITY_INVALID = "Invalid"


def check_email_syntax(email: str) -> bool:
    """Regex syntax check for obvious typos and malformed addresses."""
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    if ".." in email or email.startswith(".") or "@" not in email:
        return False
    return EMAIL_SYNTAX_RE.match(email) is not None


def _verification_bucket(email: str, seed: int = 0) -> int:
    raw = f"{email}:{seed}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16) % 100


def mock_zerobounce_verify(email: str, seed: int = 0) -> Dict[str, Any]:
    """
    Simulates ZeroBounce / Hunter / NeverBounce verification API.

    Returns deliverability, catch-all flag, SMTP check result, and confidence score.
    """
    bucket = _verification_bucket(email, seed)

    if not check_email_syntax(email):
        return {
            "provider": "zerobounce_mock",
            "deliverability": DELIVERABILITY_INVALID,
            "catch_all": False,
            "smtp_check": "failed",
            "score": 0,
            "reason": "syntax_error",
        }

    # Deterministic distribution for demo/testing
    if bucket < 8:
        return {
            "provider": "zerobounce_mock",
            "deliverability": DELIVERABILITY_INVALID,
            "catch_all": False,
            "smtp_check": "failed",
            "score": 15 + bucket,
            "reason": "mailbox_not_found",
        }
    if bucket < 18:
        return {
            "provider": "zerobounce_mock",
            "deliverability": DELIVERABILITY_RISKY,
            "catch_all": False,
            "smtp_check": "passed",
            "score": 50 + (bucket % 25),
            "reason": "low_confidence",
        }
    if bucket < 28:
        return {
            "provider": "zerobounce_mock",
            "deliverability": DELIVERABILITY_VALID,
            "catch_all": True,
            "smtp_check": "passed",
            "score": 72 + (bucket % 20),
            "reason": "catch_all_domain",
        }
    return {
        "provider": "zerobounce_mock",
        "deliverability": DELIVERABILITY_VALID,
        "catch_all": False,
        "smtp_check": "passed",
        "score": 85 + (bucket % 15),
        "reason": "verified",
    }


def _status_from_result(result: Dict[str, Any]) -> str:
    if result["deliverability"] == DELIVERABILITY_INVALID:
        if result.get("reason") == "syntax_error":
            return "Syntax Error"
        return "Invalid"

    if result.get("catch_all"):
        return "Catch-all"

    if result["deliverability"] == DELIVERABILITY_RISKY:
        return "Risky"

    if result["deliverability"] == DELIVERABILITY_VALID:
        return "Verified"

    return "Unverified"


def passes_qualification(result: Dict[str, Any]) -> bool:
    """Return True if email is safe to store for outreach."""
    status = _status_from_result(result)

    if status in QUALIFICATION_STATUSES:
        return True

    if status == "Risky" and result.get("score", 0) >= RISKY_SCORE_THRESHOLD:
        return True

    return False


def _parse_buyer_name(buyer_name: str) -> Tuple[str, str]:
    parts = buyer_name.strip().split()
    if len(parts) < 2:
        first = parts[0].lower() if parts else "contact"
        return first, "team"
    return parts[0].lower(), parts[-1].lower()


def generate_alternative_emails(buyer_name: str, domain: str) -> List[str]:
    """Generate common corporate email format alternatives."""
    first, last = _parse_buyer_name(buyer_name)
    return [
        f"{first}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}@{domain}",
        f"{last}.{first}@{domain}",
    ]


def verify_email(email: str, seed: int = 0) -> Dict[str, Any]:
    """Run full verification pipeline on a single address."""
    if not check_email_syntax(email):
        return {
            "email": email,
            "work_email": None,
            "email_status": "Syntax Error",
            "qualified": False,
            "verification": mock_zerobounce_verify(email, seed),
            "attempt": "primary",
        }

    verification = mock_zerobounce_verify(email, seed)
    status = _status_from_result(verification)
    qualified = passes_qualification(verification)

    return {
        "email": email,
        "work_email": email if qualified else None,
        "email_status": status,
        "qualified": qualified,
        "verification": verification,
        "attempt": "primary",
    }


def verify_and_resolve_work_email(
    primary_email: str,
    buyer_name: str,
    domain: str,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Verify primary work email; if invalid, try alternative formats.

    Only returns a persisted work_email when qualification passes.
    """
    candidates: List[Tuple[str, str]] = [("primary", primary_email)]
    seen = {primary_email.lower()}

    for alt in generate_alternative_emails(buyer_name, domain):
        if alt.lower() not in seen:
            candidates.append(("alternative", alt))
            seen.add(alt.lower())

    attempts = []
    last_result: Optional[Dict[str, Any]] = None

    for attempt_type, candidate in candidates:
        result = verify_email(candidate, seed=seed + len(attempts))
        result["attempt"] = attempt_type
        attempts.append(
            {
                "email": candidate,
                "attempt": attempt_type,
                "email_status": result["email_status"],
                "qualified": result["qualified"],
                "score": result["verification"].get("score"),
            }
        )

        if result["qualified"]:
            return {
                "work_email": result["work_email"],
                "email_status": result["email_status"],
                "qualified": True,
                "verification": result["verification"],
                "attempts": attempts,
                "notes_flag": None,
            }

        last_result = result

    # No qualified email found — flag for manual review
    final_status = last_result["email_status"] if last_result else "Unverified"
    return {
        "work_email": None,
        "email_status": final_status,
        "qualified": False,
        "verification": last_result["verification"] if last_result else {},
        "attempts": attempts,
        "notes_flag": "Email verification failed — manual review required",
    }
