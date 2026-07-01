from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from city_utils import normalize_city_name
from models import Company, Contact
from scoring import priority_tier, score_company
from seed_data import (
    actual_companies_available,
    is_synthetic_company_name,
    load_actual_companies_with_report,
    verification_report_dict,
)
from sorting_agent import ALLOWED_CITIES
from services.domain_verification import check_website_head_status_200
from services.url_utils import normalize_website
from services.industry_filter import passes_financial_icp_filter
from stakeholders import QUALIFIED_SCORE_THRESHOLD, generate_stakeholders

ALLOWED_INDUSTRIES = {"Financial Services", "Insurance", "Accounting"}
MIN_EMPLOYEE_COUNT = 20
MAX_EMPLOYEE_COUNT = 500


def _passes_filters(company: dict) -> bool:
    if is_synthetic_company_name(company.get("name", "")):
        return False
    city = normalize_city_name(company.get("city") or company.get("locality"))
    if not city or city not in ALLOWED_CITIES:
        return False
    accepted, _reason = passes_financial_icp_filter(
        {
            "company": company.get("name"),
            "industry": company.get("industry"),
            "website": company.get("website"),
        }
    )
    if not accepted:
        return False
    count = company.get("employee_count")
    if count is None or count < MIN_EMPLOYEE_COUNT or count > MAX_EMPLOYEE_COUNT:
        return False
    return True


def ingest_companies(db: Session, companies: Optional[List[dict]] = None) -> dict:
    if not actual_companies_available():
        return {
            "inserted": 0,
            "skipped": 0,
            "error": "Missing CSV file: actual_companies.csv (expected in project root)",
            "source": "actual_companies.csv",
        }

    if companies is None:
        companies, csv_report = load_actual_companies_with_report()
    else:
        csv_report = None
    if not companies:
        return {
            "inserted": 0,
            "skipped": 0,
            "error": "actual_companies.csv contains no valid company rows",
            "source": "actual_companies.csv",
            "csv_validation": {
                "accepted": csv_report.accepted if csv_report else 0,
                "rejected": csv_report.rejected if csv_report else [],
                "allowed_cities": sorted(ALLOWED_CITIES),
                "verification": verification_report_dict(csv_report) if csv_report else {},
            },
        }

    existing_names = {name for (name,) in db.query(Company.name).all()}
    inserted = 0
    skipped = 0
    website_unreachable = 0
    checked_at = datetime.now(timezone.utc)

    for company in companies:
        if not _passes_filters(company):
            skipped += 1
            continue
        if company["name"] in existing_names:
            skipped += 1
            continue

        website = normalize_website(company.get("website") or "")
        is_reachable, detail, status_code = check_website_head_status_200(website)
        if not is_reachable:
            website_unreachable += 1
            skipped += 1
            print(
                f"[ingest] skipped {company['name']} ({website}) — unreachable ({detail})",
                flush=True,
            )
            continue

        company_score = score_company(company)
        city = normalize_city_name(company.get("city") or company.get("locality"))
        if not city:
            skipped += 1
            continue

        employee_count = company.get("employee_count")
        linkedin_url = company.get("linkedin_url")

        company_obj = Company(
            name=company["name"],
            website=website or None,
            industry=company["industry"],
            size=str(employee_count) if employee_count is not None else None,
            locality=city,
            country="united states",
            linkedin_url=linkedin_url,
            is_targeted=False,
            week_assigned=None,
            city=city,
            employee_count=employee_count,
            score=company_score,
            priority_tier=priority_tier(company_score),
            website_reachable=True,
            website_http_status=status_code,
            website_checked_at=checked_at,
        )
        db.add(company_obj)
        db.flush()

        if company_obj.score >= QUALIFIED_SCORE_THRESHOLD:
            for stakeholder in generate_stakeholders(company_obj):
                db.add(Contact(**stakeholder))

        existing_names.add(company["name"])
        inserted += 1

    db.commit()
    return {
        "inserted": inserted,
        "skipped": skipped,
        "website_unreachable": website_unreachable,
        "source": "actual_companies.csv",
        "csv_validation": {
            "accepted": csv_report.accepted if csv_report else len(companies),
            "rejected": csv_report.rejected if csv_report else [],
            "allowed_cities": sorted(ALLOWED_CITIES),
            "verification": verification_report_dict(csv_report) if csv_report else {},
        },
    }
