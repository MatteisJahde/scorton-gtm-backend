"""
Discover professional contacts for a company domain via Hunter.io or PDL.

Provider waterfall (``CONTACT_ENRICHMENT_PROVIDER=auto``):
  1. Hunter.io domain search — try ranked executive emails through hygiene pipeline
  2. PDL person search — when Hunter misses or returns low-confidence results
  3. No-contact marker or drop (``CONTACT_ENRICHMENT_DROP_IF_MISSING``)

Each discovered email passes the multi-stage hygiene pipeline before acceptance.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from config.personas import DEFAULT_EXECUTIVE_TITLES, INDUSTRY_EXECUTIVE_TITLES
from services.contact_fields import (
    CONTACT_STATUS_NO_CONTACT,
    CONTACT_STATUS_REVIEW,
    CONTACT_STATUS_VERIFIED,
    is_likely_synthetic_contact,
    is_placeholder_contact_name,
)
from services.email_hygiene import (
    is_role_account,
    run_email_hygiene_pipeline,
)
from services.hunter_client import (
    HunterDomainSearchError,
    get_hunter_api_key,
    hunter_domain_search,
)
from services.pdl_client import get_pdl_api_key
from services.pdl_contact_search import PDLPersonSearchError, search_buyer_contact

_TITLE_KEYWORDS = tuple(
    title.lower()
    for titles in INDUSTRY_EXECUTIVE_TITLES.values()
    for title in titles
) + tuple(title.lower() for title in DEFAULT_EXECUTIVE_TITLES)

HUNTER_MIN_CONTACT_CONFIDENCE = int(os.getenv("HUNTER_MIN_CONTACT_CONFIDENCE", "70"))


def _provider_mode() -> str:
    return (os.getenv("CONTACT_ENRICHMENT_PROVIDER") or "auto").strip().lower()


def _drop_if_missing() -> bool:
    return (os.getenv("CONTACT_ENRICHMENT_DROP_IF_MISSING") or "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def should_drop_lead_without_contact() -> bool:
    return _drop_if_missing()


def _title_score(position: str, industry: str) -> int:
    text = (position or "").lower()
    if not text:
        return 0
    score = 0
    preferred = INDUSTRY_EXECUTIVE_TITLES.get(industry, DEFAULT_EXECUTIVE_TITLES)
    for title in preferred:
        if title.lower() in text:
            score += 20
    for keyword in _TITLE_KEYWORDS:
        if keyword in text:
            score += 5
    if any(token in text for token in ("risk", "compliance", "governance", "finance", "cro", "cfo")):
        score += 8
    return score


def _name_from_hunter_email(entry: dict[str, Any]) -> str:
    first = str(entry.get("first_name") or "").strip()
    last = str(entry.get("last_name") or "").strip()
    if first and last:
        return f"{first} {last}"
    return str(entry.get("value") or "").split("@")[0].replace(".", " ").title()


def _rank_hunter_contacts(data: dict[str, Any], industry: str) -> list[dict[str, Any]]:
    """Return Hunter domain-search emails ranked by title fit, skipping role inboxes."""
    emails = data.get("emails") or []
    ranked: list[tuple[int, dict[str, Any]]] = []

    for entry in emails:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("value") or "").strip()
        if not email or "@" not in email or is_role_account(email):
            continue
        position = str(entry.get("position") or entry.get("department") or "").strip()
        confidence = int(entry.get("confidence") or 0)
        score = _title_score(position, industry) + min(confidence, 100) // 5
        ranked.append((score, entry))

    ranked.sort(key=lambda item: item[0], reverse=True)
    results: list[dict[str, Any]] = []
    for score, entry in ranked:
        email = str(entry.get("value") or "").strip()
        position = str(entry.get("position") or "Finance Leader").strip()
        confidence = int(entry.get("confidence") or 0)
        results.append(
            {
                "contact_name": _name_from_hunter_email(entry),
                "contact_role": position,
                "verified_email": email,
                "linkedin_url": str(entry.get("linkedin") or "").strip(),
                "hunter_confidence": confidence,
                "hunter_rank_score": score,
                "enrichment_provider": "hunter",
            }
        )
    return results


def _apply_hygiene_to_contact(contact: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Run hygiene pipeline on a discovered contact email."""
    email = str(contact.get("verified_email") or "").strip()
    if not email:
        return None

    hygiene = run_email_hygiene_pipeline(email)
    status = str(hygiene.get("verification_status") or "")
    qualified = bool(hygiene.get("qualified"))
    needs_review = bool(hygiene.get("needs_review"))

    if status in {"invalid", "syntax_error", "suppressed", "role_account"} and not needs_review:
        return None

    lead_status = "Verified" if qualified else ("Review" if needs_review else "Unverified")
    if qualified:
        contact_status = CONTACT_STATUS_VERIFIED
    elif needs_review:
        contact_status = CONTACT_STATUS_REVIEW
    else:
        contact_status = CONTACT_STATUS_NO_CONTACT

    return {
        **contact,
        "buyer_name": contact["contact_name"],
        "job_title": contact["contact_role"],
        "work_email": email if (qualified or needs_review) else "",
        "verified_email": email if (qualified or needs_review) else "",
        "email_status": hygiene.get("email_status"),
        "lead_verification_status": lead_status,
        "contact_status": contact_status,
        "contact_verification_status": f"{contact.get('enrichment_provider', 'unknown')}_{status}",
        "verification_status": status,
        "needs_review": needs_review,
        "email_qualified": qualified,
        "hygiene_stages": hygiene.get("stages") or [],
        "smtp_check": hygiene.get("smtp_check"),
        "verification_depth": hygiene.get("verification_depth"),
    }


def _from_hunter(domain: str, industry: str) -> Optional[dict[str, Any]]:
    if not get_hunter_api_key():
        return None
    try:
        data = hunter_domain_search(domain)
    except HunterDomainSearchError:
        return None

    candidates = _rank_hunter_contacts(data, industry)
    if not candidates:
        return None

    best_verified: Optional[dict[str, Any]] = None
    best_review: Optional[dict[str, Any]] = None
    low_confidence = True

    for candidate in candidates:
        confidence = int(candidate.get("hunter_confidence") or 0)
        if confidence >= HUNTER_MIN_CONTACT_CONFIDENCE:
            low_confidence = False

        enriched = _apply_hygiene_to_contact(candidate)
        if not enriched:
            continue

        if enriched.get("email_qualified"):
            return enriched
        if enriched.get("needs_review") and best_review is None:
            best_review = enriched

        if best_verified is None and enriched.get("work_email"):
            best_verified = enriched

    if low_confidence:
        return None

    return best_review or best_verified


def _from_pdl(
    *,
    company_name: str,
    website: str,
    industry: str,
) -> Optional[dict[str, Any]]:
    api_key = get_pdl_api_key()
    if not api_key:
        return None
    try:
        contact = search_buyer_contact(
            company_name=company_name,
            website=website,
            industry=industry,
            api_key=api_key,
        )
    except PDLPersonSearchError:
        return None
    if not contact or not contact.get("buyer_name"):
        return None

    email = str(contact.get("work_email") or "").strip()
    candidate = {
        "contact_name": contact["buyer_name"],
        "contact_role": contact.get("job_title") or "",
        "verified_email": email,
        "linkedin_url": contact.get("linkedin_url") or "",
        "enrichment_provider": "pdl",
        "hunter_confidence": 0,
    }
    if not email:
        return None

    enriched = _apply_hygiene_to_contact(candidate)
    if enriched and (enriched.get("email_qualified") or enriched.get("needs_review")):
        return enriched
    return None


def _csv_is_trusted(
    *,
    buyer_name: str,
    job_title: str,
    work_email: str,
    company_name: str,
    lead_verification_status: Optional[str],
) -> bool:
    if lead_verification_status == "Verified" and work_email and buyer_name:
        if not is_placeholder_contact_name(buyer_name):
            if not is_likely_synthetic_contact(buyer_name, work_email, company_name):
                hygiene = run_email_hygiene_pipeline(work_email)
                if hygiene.get("qualified") or hygiene.get("needs_review"):
                    return True
    return False


def enrich_contact_for_company(
    *,
    company_name: str,
    website: str,
    domain: str,
    industry: str,
    buyer_name: str = "",
    job_title: str = "",
    work_email: str = "",
    lead_verification_status: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """
    Resolve a professional contact for one company.

    Returns a contact payload, a no-contact marker dict, or None when the lead
    should be dropped (``CONTACT_ENRICHMENT_DROP_IF_MISSING=true``).
    """
    if _csv_is_trusted(
        buyer_name=buyer_name,
        job_title=job_title,
        work_email=work_email,
        company_name=company_name,
        lead_verification_status=lead_verification_status,
    ):
        hygiene = run_email_hygiene_pipeline(work_email)
        return {
            "contact_name": buyer_name.strip(),
            "contact_role": job_title.strip(),
            "verified_email": work_email.strip(),
            "buyer_name": buyer_name.strip(),
            "job_title": job_title.strip(),
            "work_email": work_email.strip(),
            "enrichment_provider": "csv",
            "contact_status": CONTACT_STATUS_VERIFIED,
            "lead_verification_status": "Verified" if hygiene.get("qualified") else "Review",
            "email_status": hygiene.get("email_status"),
            "contact_verification_status": "csv_verified",
            "verification_status": hygiene.get("verification_status"),
            "needs_review": hygiene.get("needs_review"),
        }

    mode = _provider_mode()
    contact: Optional[dict[str, Any]] = None

    if mode in {"auto", "hunter"}:
        contact = _from_hunter(domain, industry)

    if contact is None and mode in {"auto", "pdl"}:
        contact = _from_pdl(
            company_name=company_name,
            website=website,
            industry=industry,
        )

    if contact and (
        contact.get("email_qualified")
        or contact.get("needs_review")
        or contact.get("contact_status") == CONTACT_STATUS_VERIFIED
    ):
        return contact

    if should_drop_lead_without_contact():
        return None

    return {
        "contact_name": "No Contact Found",
        "contact_role": job_title.strip() or "—",
        "verified_email": "",
        "buyer_name": buyer_name.strip() or "No Contact Found",
        "job_title": job_title.strip() or "—",
        "work_email": "",
        "enrichment_provider": "none",
        "contact_status": CONTACT_STATUS_NO_CONTACT,
        "lead_verification_status": "Unverified",
        "email_status": "Unverified",
        "contact_verification_status": "not_found",
        "verification_status": "unverified",
    }


def merge_contact_into_record(record: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
    merged = dict(record)
    merged.update(contact)
    return merged
