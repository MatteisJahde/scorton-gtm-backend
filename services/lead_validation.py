"""
Combined lead validation: email deliverability + contact/title verification.
"""

from __future__ import annotations

from typing import Any, Dict

from services.contact_verification import verify_contact
from services.email_verification import verify_lead_email

LEAD_STATUS_VERIFIED = "Verified"
LEAD_STATUS_UNVERIFIED = "Unverified"


def validate_lead(
    *,
    work_email: str,
    buyer_name: str,
    job_title: str,
    company_name: str,
    website: str,
    linkedin_url: str | None = None,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Run email and contact validation for one CSV lead row.

    A lead is qualified only when both checks pass.
    """
    email_result = verify_lead_email(work_email, seed=seed)
    contact_result = verify_contact(
        email=work_email,
        buyer_name=buyer_name,
        job_title=job_title,
        company_name=company_name,
        website=website,
        linkedin_url=linkedin_url,
        seed=seed,
    )

    qualified = bool(email_result.get("qualified") and contact_result.get("qualified"))
    lead_status = LEAD_STATUS_VERIFIED if qualified else LEAD_STATUS_UNVERIFIED

    failure_reasons = []
    if not email_result.get("qualified"):
        failure_reasons.append("email")
    if not contact_result.get("qualified"):
        failure_reasons.append("contact")

    return {
        "qualified": qualified,
        "lead_verification_status": lead_status,
        "verification_status": email_result.get("verification_status"),
        "email_status": email_result.get("email_status"),
        "contact_verification_status": contact_result.get("verification_status"),
        "email_provider": email_result.get("provider"),
        "contact_provider": contact_result.get("provider"),
        "failure_reasons": failure_reasons,
        "email": email_result,
        "contact": contact_result,
    }
