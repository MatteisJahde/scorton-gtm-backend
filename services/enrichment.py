"""
Mock external enrichment workflow.

Simulates Apollo (company/contact), LinkedIn (company + buyer profiles),
and HubSpot (buying signals) without live API calls.
"""

import hashlib
from typing import Any, Dict

from city_utils import extract_city_from_record
from config.personas import pick_executive_title
from models import Company
from scoring import score_company
from seed_data import get_company_csv_extras
from sorting_agent import ALLOWED_CITIES, city_priority_bonus
from services.verifier import verify_and_resolve_work_email

BUYER_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Quinn",
    "Avery", "Blake", "Cameron", "Drew", "Elliot", "Harper", "Logan",
]
BUYER_LAST_NAMES = [
    "Chen", "Patel", "Nguyen", "Brooks", "Foster", "Hayes", "Kim", "Reed",
    "Sullivan", "Turner", "Vargas", "Walsh", "Bennett", "Coleman", "Diaz",
]

INDUSTRY_AI_BASE = {
    "Financial Services": 72,
    "Insurance": 58,
    "Accounting": 50,
}
INDUSTRY_RISK_BASE = {
    "Financial Services": 78,
    "Insurance": 88,
    "Accounting": 70,
}

OPPORTUNITY_NOTES = [
    "AI Governance Opportunity",
    "AI Risk Review Opportunity",
    "Compliance Opportunity",
    "Workflow Monitoring Opportunity",
    "Trust and Risk Opportunity",
    "AI Adoption Opportunity",
]


def _slug(text: str) -> str:
    return "".join(char.lower() for char in text if char.isalnum())


def _domain(company_name: str) -> str:
    return f"{_slug(company_name)[:28]}.com"


def _seed(company: Company) -> int:
    raw = f"{company.id}:{company.name}:{company.city}"
    return int(hashlib.md5(raw.encode()).hexdigest(), 16)


def _funding_amount(employee_count: int) -> str:
    if employee_count < 50:
        return "$1M-$10M"
    if employee_count < 200:
        return "$10M-$50M"
    if employee_count < 350:
        return "$50M-$100M"
    return "$100M+"


def _funding_stage(employee_count: int, seed: int) -> str:
    if employee_count < 50:
        return "Bootstrapped" if seed % 2 == 0 else "Private"
    if employee_count < 200:
        return "Private" if seed % 2 == 0 else "Growth"
    if employee_count < 400:
        return "Growth" if seed % 2 == 0 else "Enterprise"
    return "Enterprise" if seed % 2 == 0 else "Public"


def _revenue_range(employee_count: int) -> str:
    if employee_count < 50:
        return "$5M-$10M"
    if employee_count < 100:
        return "$10M-$50M"
    if employee_count < 200:
        return "$50M-$100M"
    if employee_count < 400:
        return "$100M-$500M"
    return "$500M+"


def mock_company_financials(company: Company) -> Dict[str, str]:
    """Generate structured funding and revenue intelligence from company size."""
    seed = _seed(company)
    employee_count = company.employee_count or 100
    funding_amount = _funding_amount(employee_count)
    funding_stage = _funding_stage(employee_count, seed)
    revenue_range = _revenue_range(employee_count)
    return {
        "funding_amount": funding_amount,
        "funding_stage": funding_stage,
        "revenue_range": revenue_range,
        "funding": f"{funding_amount} ({funding_stage})",
        "revenue": revenue_range,
    }


def mock_apollo_enrich(domain: str, company: Company) -> Dict[str, Any]:
    """Simulates Apollo.io company + contact enrichment."""
    seed = _seed(company)
    financials = mock_company_financials(company)
    return {
        "source": "apollo",
        "domain": domain,
        "employee_count": company.employee_count,
        "industry": company.industry,
        "funding_summary": financials["funding"],
        "funding": financials["funding"],
        "funding_amount": financials["funding_amount"],
        "funding_stage": financials["funding_stage"],
        "annual_revenue": financials["revenue_range"],
        "revenue": financials["revenue"],
        "revenue_range": financials["revenue_range"],
        "technologies": ["Salesforce", "HubSpot", "Snowflake"][seed % 3 : seed % 3 + 1],
        "confidence": 0.85 + (seed % 15) / 100,
    }


def mock_linkedin_company(company_name: str, domain: str) -> Dict[str, Any]:
    """Simulates LinkedIn/Clay company page lookup."""
    slug = _slug(company_name)
    return {
        "source": "linkedin_company",
        "company_linkedin_url": f"https://linkedin.com/company/{slug}",
        "followers": 500 + (len(company_name) * 37) % 50000,
    }


def mock_linkedin_buyer(company: Company, domain: str, index: int) -> Dict[str, Any]:
    """Simulates LinkedIn executive buyer profile discovery."""
    seed = _seed(company) + index
    first = BUYER_FIRST_NAMES[seed % len(BUYER_FIRST_NAMES)]
    last = BUYER_LAST_NAMES[(seed // 7) % len(BUYER_LAST_NAMES)]
    slug = f"{first}-{last}".lower()
    return {
        "source": "linkedin_buyer",
        "buyer_name": f"{first} {last}",
        "job_title": pick_executive_title(company.industry, index),
        "buyer_linkedin_url": f"https://linkedin.com/in/{slug}-{company.id}",
        "work_email": f"{first.lower()}.{last.lower()}@{domain}",
    }


def mock_hubspot_signals(domain: str, company: Company) -> Dict[str, Any]:
    """Simulates governance and risk intent signals."""
    seed = _seed(company)
    intent_score = 40 + (seed % 61)
    return {
        "source": "hubspot",
        "buying_signal": intent_score,
        "last_activity_days_ago": seed % 30,
    }


def _employee_bonus(employee_count: int) -> int:
    if employee_count >= 200:
        return 25
    if employee_count >= 50:
        return 15
    return 8


def _mid_market_bonus(employee_count: int) -> int:
    if 50 <= employee_count <= 300:
        return 8
    if 20 <= employee_count < 50:
        return 3
    return 5


def _city_priority_bonus(city: str, city_validated: bool) -> int:
    return city_priority_bonus(city, city_validated)


def calculate_ai_signal(industry: str, employee_count: int) -> int:
    base = INDUSTRY_AI_BASE.get(industry, 40)
    return min(100, base + _employee_bonus(employee_count))


def calculate_risk_signal(industry: str, employee_count: int) -> int:
    base = INDUSTRY_RISK_BASE.get(industry, 60)
    size_bonus = 10 if employee_count >= 200 else (5 if employee_count >= 50 else 0)
    return min(100, base + size_bonus)


def calculate_trust_opportunity_score(
    company: Company,
    ai_signal: int,
    risk_signal: int,
    buying_signal: int,
    city_validated: bool,
) -> int:
    employee_count = company.employee_count or 0
    base = (ai_signal + risk_signal) / 2
    base += _mid_market_bonus(employee_count)
    base += _city_priority_bonus(company.city, city_validated)
    base += round(buying_signal * 0.12)
    return min(100, round(base))


def _priority_tier(trust_opportunity_score: int) -> str:
    if trust_opportunity_score >= 85:
        return "Tier 1"
    if trust_opportunity_score >= 70:
        return "Tier 2"
    return "Tier 3"


def _generate_notes(industry: str, ai_signal: int, risk_signal: int, seed: int) -> str:
    if industry == "Insurance":
        pool = [
            OPPORTUNITY_NOTES[2],
            OPPORTUNITY_NOTES[4],
            OPPORTUNITY_NOTES[1],
        ]
    elif industry == "Accounting":
        pool = [
            OPPORTUNITY_NOTES[3],
            OPPORTUNITY_NOTES[0],
            OPPORTUNITY_NOTES[5],
        ]
    elif ai_signal >= 75:
        pool = [OPPORTUNITY_NOTES[0], OPPORTUNITY_NOTES[5], OPPORTUNITY_NOTES[1]]
    elif risk_signal >= 80:
        pool = [OPPORTUNITY_NOTES[4], OPPORTUNITY_NOTES[1], OPPORTUNITY_NOTES[2]]
    else:
        pool = OPPORTUNITY_NOTES
    return pool[seed % len(pool)]


def enrich_company(company: Company, index: int = 0) -> Dict[str, Any]:
    """Run the full enrichment workflow for one company."""
    domain = _domain(company.name)
    website = f"https://www.{domain}"

    apollo = mock_apollo_enrich(domain, company)
    linkedin_co = mock_linkedin_company(company.name, domain)
    linkedin_buyer = mock_linkedin_buyer(company, domain, index)
    hubspot = mock_hubspot_signals(domain, company)
    financials = mock_company_financials(company)
    csv_extras = get_company_csv_extras(company.name)

    city = extract_city_from_record(
        {
            "city": company.city,
            "locality": company.locality,
        }
    )
    city_validated = city in ALLOWED_CITIES
    employee_count = company.employee_count or 0
    ai_signal = calculate_ai_signal(company.industry, employee_count)
    risk_signal = calculate_risk_signal(company.industry, employee_count)
    buying_signal = hubspot["buying_signal"]
    trust_opportunity_score = calculate_trust_opportunity_score(
        company, ai_signal, risk_signal, buying_signal, city_validated
    )
    if csv_extras.get("signal_score") is not None:
        trust_opportunity_score = int(csv_extras["signal_score"])

    buyer_name = csv_extras.get("buyer_name") or linkedin_buyer["buyer_name"]
    job_title = csv_extras.get("job_title") or linkedin_buyer["job_title"]
    primary_email = csv_extras.get("work_email") or linkedin_buyer["work_email"]

    email_result = verify_and_resolve_work_email(
        primary_email=primary_email,
        buyer_name=buyer_name,
        domain=domain,
        seed=_seed(company),
    )

    notes = _generate_notes(company.industry, ai_signal, risk_signal, _seed(company) + index)
    if email_result.get("notes_flag"):
        notes = f"{notes} | {email_result['notes_flag']}"

    return {
        "company_id": company.id,
        "company_name": company.name,
        "website": website,
        "industry": apollo["industry"],
        "city": city,
        "employee_count": apollo["employee_count"],
        "funding": apollo.get("funding") or financials["funding"],
        "revenue": apollo.get("revenue") or financials["revenue"],
        "funding_amount": apollo.get("funding_amount") or financials["funding_amount"],
        "funding_stage": apollo.get("funding_stage") or financials["funding_stage"],
        "revenue_range": apollo.get("revenue_range") or financials["revenue_range"],
        "city_validated": city_validated,
        "buyer_name": buyer_name,
        "job_title": job_title,
        "work_email": email_result["work_email"],
        "email_status": email_result["email_status"],
        "linkedin_url": linkedin_buyer["buyer_linkedin_url"],
        "company_linkedin_url": linkedin_co["company_linkedin_url"],
        "ai_signal": ai_signal,
        "risk_signal": risk_signal,
        "buying_signal": buying_signal,
        "trust_opportunity_score": trust_opportunity_score,
        "icp_score": score_company(company),
        "priority_tier": _priority_tier(trust_opportunity_score),
        "notes": notes,
    }
