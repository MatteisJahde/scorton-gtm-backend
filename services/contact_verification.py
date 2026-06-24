"""
Contact name / job title validation against professional profile data.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional

from services.pdl_person import (
    PDLPersonError,
    _company_names_match,
    domain_from_website,
    get_pdl_api_key,
    pdl_enrich_person,
    titles_compatible,
)

CONTACT_STATUS_VERIFIED = "verified"
CONTACT_STATUS_NOT_FOUND = "not_found"
CONTACT_STATUS_TITLE_MISMATCH = "title_mismatch"
CONTACT_STATUS_COMPANY_MISMATCH = "company_mismatch"
CONTACT_STATUS_INVALID_INPUT = "invalid_input"
CONTACT_STATUS_ERROR = "error"
CONTACT_STATUS_MOCK = "mock_verified"


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if len(parts) < 2:
        return parts[0] if parts else "", ""
    return parts[0], parts[-1]


def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower().strip()


def _domains_align(email_domain: str, website_domain: str) -> bool:
    if not email_domain or not website_domain:
        return False
    if email_domain == website_domain:
        return True
    email_root = email_domain.split(".")[0]
    site_root = website_domain.split(".")[0]
    return email_root == site_root or email_domain.endswith(website_domain) or website_domain.endswith(email_domain)


def _mock_contact_verification(
    *,
    email: str,
    buyer_name: str,
    job_title: str,
    company_name: str,
    website: str,
    seed: int,
) -> Dict[str, Any]:
    """Deterministic fallback when PDL is not configured."""
    bucket = int(hashlib.md5(f"{email}:{seed}".encode()).hexdigest(), 16) % 100
    first, last = _split_name(buyer_name)
    domain_ok = _domains_align(_email_domain(email), domain_from_website(website))
    name_ok = bool(first and last)
    title_ok = len((job_title or "").strip()) >= 3

    qualified = domain_ok and name_ok and title_ok and bucket >= 12
    if not qualified and not domain_ok:
        status = CONTACT_STATUS_COMPANY_MISMATCH
    elif not qualified and not title_ok:
        status = CONTACT_STATUS_TITLE_MISMATCH
    elif not qualified:
        status = CONTACT_STATUS_NOT_FOUND
    else:
        status = CONTACT_STATUS_MOCK

    return {
        "qualified": qualified,
        "verification_status": status,
        "provider": "mock",
        "detail": status,
        "profile": {
            "buyer_name": buyer_name,
            "job_title": job_title,
            "company_name": company_name,
            "domain_match": domain_ok,
        },
    }


def verify_contact(
    *,
    email: str,
    buyer_name: str,
    job_title: str,
    company_name: str,
    website: str,
    linkedin_url: Optional[str] = None,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Cross-reference buyer name and title against a people-data provider.

    Uses PDL Person Enrichment when ``PDL_API_KEY`` is set; otherwise a
    deterministic mock that checks email domain alignment with the company site.
    """
    normalized_email = (email or "").strip()
    normalized_name = (buyer_name or "").strip()
    normalized_title = (job_title or "").strip()
    normalized_company = (company_name or "").strip()

    if not normalized_email or not normalized_name or not normalized_title:
        return {
            "qualified": False,
            "verification_status": CONTACT_STATUS_INVALID_INPUT,
            "provider": None,
            "detail": "missing_contact_fields",
            "profile": {},
        }

    api_key = get_pdl_api_key()
    if not api_key:
        return _mock_contact_verification(
            email=normalized_email,
            buyer_name=normalized_name,
            job_title=normalized_title,
            company_name=normalized_company,
            website=website,
            seed=seed,
        )

    try:
        enrichment = pdl_enrich_person(
            email=normalized_email,
            company_name=normalized_company,
            buyer_name=normalized_name,
            api_key=api_key,
        )
    except PDLPersonError as exc:
        return {
            "qualified": False,
            "verification_status": CONTACT_STATUS_ERROR,
            "provider": "pdl",
            "detail": str(exc),
            "profile": {},
        }

    if not enrichment.get("found"):
        return {
            "qualified": False,
            "verification_status": CONTACT_STATUS_NOT_FOUND,
            "provider": "pdl",
            "detail": "person_not_found",
            "profile": enrichment,
        }

    actual_title = str(enrichment.get("job_title") or "").strip()
    actual_company = str(enrichment.get("job_company_name") or normalized_company).strip()

    if actual_company and not _company_names_match(normalized_company, actual_company):
        return {
            "qualified": False,
            "verification_status": CONTACT_STATUS_COMPANY_MISMATCH,
            "provider": "pdl",
            "detail": f"expected={normalized_company} actual={actual_company}",
            "profile": enrichment,
        }

    if actual_title and not titles_compatible(normalized_title, actual_title):
        return {
            "qualified": False,
            "verification_status": CONTACT_STATUS_TITLE_MISMATCH,
            "provider": "pdl",
            "detail": f"expected={normalized_title} actual={actual_title}",
            "profile": enrichment,
        }

    return {
        "qualified": True,
        "verification_status": CONTACT_STATUS_VERIFIED,
        "provider": "pdl",
        "detail": actual_title or normalized_title,
        "profile": enrichment,
    }
