"""Normalize contact fields for API and dashboard consumers."""

from __future__ import annotations

import re
from typing import Mapping, Optional

CONTACT_STATUS_VERIFIED = "verified"
CONTACT_STATUS_REVIEW = "review"
CONTACT_STATUS_NO_CONTACT = "no_contact_found"
CONTACT_STATUS_PLACEHOLDER = "placeholder"

_PLACEHOLDER_NAMES = frozenset({"", "tbd", "unknown", "n/a", "none"})
_SYNTHETIC_GROUP_PATTERN = re.compile(r"\bgroup\s+\d{3,4}\b", re.IGNORECASE)


def is_placeholder_contact_name(name: object) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in _PLACEHOLDER_NAMES


def is_likely_synthetic_contact(name: object, email: object, company_name: object) -> bool:
    """Heuristic for mock/synthetic buyer rows (e.g. PDL batch placeholders)."""
    contact_name = str(name or "").strip()
    contact_email = str(email or "").strip().lower()
    company = str(company_name or "").strip()
    if is_placeholder_contact_name(contact_name):
        return True
    if not contact_email or "@" not in contact_email:
        return True
    if _SYNTHETIC_GROUP_PATTERN.search(company):
        local, _, domain = contact_email.partition("@")
        company_slug = re.sub(r"[^a-z0-9]", "", company.lower())
        domain_slug = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
        if company_slug and domain_slug and company_slug[:12] in domain_slug:
            if local.count(".") == 1 and len(local) < 24:
                return True
    return False


def resolve_contact_status(record: Mapping[str, object]) -> str:
    explicit = str(record.get("contact_status") or "").strip().lower()
    if explicit in {
        CONTACT_STATUS_VERIFIED,
        CONTACT_STATUS_REVIEW,
        CONTACT_STATUS_NO_CONTACT,
        CONTACT_STATUS_PLACEHOLDER,
    }:
        return explicit

    name = record.get("contact_name") or record.get("buyer_name")
    email = record.get("verified_email") or record.get("work_email")
    company = record.get("company_name") or record.get("company")

    if is_placeholder_contact_name(name) or not str(email or "").strip():
        return CONTACT_STATUS_NO_CONTACT
    if is_likely_synthetic_contact(name, email, company):
        return CONTACT_STATUS_PLACEHOLDER
    if str(record.get("lead_verification_status") or "").strip().lower() == "verified":
        return CONTACT_STATUS_VERIFIED
    if str(record.get("email_status") or "").strip().lower() == "verified":
        return CONTACT_STATUS_VERIFIED
    return CONTACT_STATUS_PLACEHOLDER


def attach_contact_aliases(record: dict) -> dict:
    """Add dashboard-friendly contact_name, contact_role, verified_email aliases."""
    enriched = dict(record)
    contact_name = str(
        enriched.get("contact_name") or enriched.get("buyer_name") or ""
    ).strip()
    contact_role = str(
        enriched.get("contact_role") or enriched.get("job_title") or ""
    ).strip()
    verified_email = str(
        enriched.get("verified_email") or enriched.get("work_email") or ""
    ).strip()

    status = resolve_contact_status(
        {
            **enriched,
            "contact_name": contact_name,
            "contact_role": contact_role,
            "verified_email": verified_email,
        }
    )

    if status == CONTACT_STATUS_NO_CONTACT:
        contact_name = "No Contact Found"
        contact_role = contact_role or "—"
        verified_email = ""

    enriched["contact_name"] = contact_name
    enriched["contact_role"] = contact_role
    enriched["verified_email"] = verified_email
    enriched["contact_status"] = status
    enriched["needs_review"] = status == CONTACT_STATUS_REVIEW or bool(enriched.get("needs_review"))
    enriched["buyer_name"] = contact_name if contact_name != "No Contact Found" else enriched.get("buyer_name", "")
    enriched["job_title"] = contact_role
    enriched["work_email"] = verified_email
    return enriched
