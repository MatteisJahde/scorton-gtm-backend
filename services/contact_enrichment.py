"""
Discover professional contacts for a company domain via Hunter.io or PDL.

Provider order (``CONTACT_ENRICHMENT_PROVIDER=auto``):
  1. Hunter.io domain search (+ optional email verification)
  2. PDL person search
  3. None (caller applies fallback / drop logic)
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from config.personas import DEFAULT_EXECUTIVE_TITLES, INDUSTRY_EXECUTIVE_TITLES
from services.contact_fields import (
    CONTACT_STATUS_NO_CONTACT,
    CONTACT_STATUS_VERIFIED,
    is_likely_synthetic_contact,
    is_placeholder_contact_name,
)
from services.hunter_client import (
    HunterDomainSearchError,
    HunterVerificationError,
    get_hunter_api_key,
    hunter_domain_search,
    hunter_verify_email,
)
from services.pdl_client import get_pdl_api_key
from services.pdl_contact_search import PDLPersonSearchError, search_buyer_contact

_TITLE_KEYWORDS = tuple(
    title.lower()
    for titles in INDUSTRY_EXECUTIVE_TITLES.values()
    for title in titles
) + tuple(title.lower() for title in DEFAULT_EXECUTIVE_TITLES)


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


def _pick_hunter_contact(data: dict[str, Any], industry: str) -> Optional[dict[str, str]]:
    emails = data.get("emails") or []
    if not emails:
        return None

    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in emails:
        if not isinstance(entry, dict):
            continue
        email = str(entry.get("value") or "").strip()
        if not email or "@" not in email:
            continue
        position = str(entry.get("position") or entry.get("department") or "").strip()
        confidence = int(entry.get("confidence") or 0)
        score = _title_score(position, industry) + min(confidence, 100) // 5
        ranked.append((score, entry))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    _, best = ranked[0]
    email = str(best.get("value") or "").strip()
    position = str(best.get("position") or "Finance Leader").strip()
    return {
        "contact_name": _name_from_hunter_email(best),
        "contact_role": position,
        "verified_email": email,
        "linkedin_url": str(best.get("linkedin") or "").strip(),
        "enrichment_provider": "hunter",
    }


def _verify_with_hunter(email: str) -> tuple[bool, str]:
    try:
        result = hunter_verify_email(email)
    except HunterVerificationError:
        return False, "unverified"
    status = str(result.get("status") or "").lower()
    if status in {"valid", "accept_all", "webmail"}:
        return True, "Verified"
    result_label = str(result.get("result") or "").lower()
    if result_label in {"deliverable", "risky"}:
        return True, "Verified"
    return False, "Unverified"


def _from_hunter(domain: str, industry: str) -> Optional[dict[str, Any]]:
    if not get_hunter_api_key():
        return None
    try:
        data = hunter_domain_search(domain)
    except HunterDomainSearchError:
        return None

    contact = _pick_hunter_contact(data, industry)
    if not contact:
        return None

    qualified, email_status = _verify_with_hunter(contact["verified_email"])
    return {
        **contact,
        "buyer_name": contact["contact_name"],
        "job_title": contact["contact_role"],
        "work_email": contact["verified_email"],
        "email_status": email_status,
        "lead_verification_status": "Verified" if qualified else "Unverified",
        "contact_status": CONTACT_STATUS_VERIFIED if qualified else CONTACT_STATUS_NO_CONTACT,
        "contact_verification_status": "hunter_verified" if qualified else "hunter_unverified",
        "verification_status": "verified" if qualified else "unverified",
    }


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
    return {
        "contact_name": contact["buyer_name"],
        "contact_role": contact.get("job_title") or "",
        "verified_email": email,
        "buyer_name": contact["buyer_name"],
        "job_title": contact.get("job_title") or "",
        "work_email": email,
        "linkedin_url": contact.get("linkedin_url") or "",
        "enrichment_provider": "pdl",
        "email_status": "Verified" if email else "Unverified",
        "lead_verification_status": "Verified" if email else "Unverified",
        "contact_status": CONTACT_STATUS_VERIFIED if email else CONTACT_STATUS_NO_CONTACT,
        "contact_verification_status": "pdl_verified" if email else "pdl_no_email",
        "verification_status": "verified" if email else "unverified",
    }


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
        return {
            "contact_name": buyer_name.strip(),
            "contact_role": job_title.strip(),
            "verified_email": work_email.strip(),
            "buyer_name": buyer_name.strip(),
            "job_title": job_title.strip(),
            "work_email": work_email.strip(),
            "enrichment_provider": "csv",
            "contact_status": CONTACT_STATUS_VERIFIED,
            "lead_verification_status": "Verified",
            "email_status": "Verified",
            "contact_verification_status": "csv_verified",
            "verification_status": "verified",
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

    if contact and contact.get("contact_status") == CONTACT_STATUS_VERIFIED:
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
