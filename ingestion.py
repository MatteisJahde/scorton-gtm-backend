from sqlalchemy.orm import Session

from city_utils import extract_city_from_record, normalize_city_name
from models import Company, Contact
from scoring import priority_tier, score_company
from seed_data import COMPANIES
from sorting_agent import ALLOWED_CITIES
from stakeholders import QUALIFIED_SCORE_THRESHOLD, generate_stakeholders

ALLOWED_INDUSTRIES = {"Financial Services", "Insurance", "Accounting"}
MIN_EMPLOYEE_COUNT = 20
MAX_EMPLOYEE_COUNT = 500


def _passes_filters(company: dict) -> bool:
    city = normalize_city_name(company.get("city") or company.get("locality"))
    if not city or city not in ALLOWED_CITIES:
        return False
    if company["industry"] not in ALLOWED_INDUSTRIES:
        return False
    count = company.get("employee_count")
    if count is None or count < MIN_EMPLOYEE_COUNT or count > MAX_EMPLOYEE_COUNT:
        return False
    return True


def ingest_companies(db: Session) -> dict:
    existing_names = {name for (name,) in db.query(Company.name).all()}
    inserted = 0
    skipped = 0

    for company in COMPANIES:
        if not _passes_filters(company):
            skipped += 1
            continue
        if company["name"] in existing_names:
            skipped += 1
            continue
        company_score = score_company(company)
        slug = "".join(c.lower() for c in company["name"] if c.isalnum())
        city = normalize_city_name(company.get("city") or company.get("locality"))
        if not city:
            skipped += 1
            continue
        employee_count = company["employee_count"]
        company_obj = Company(
            name=company["name"],
            website=f"https://www.{slug}.com",
            industry=company["industry"],
            size=str(employee_count),
            locality=city,
            country="united states",
            linkedin_url=f"https://linkedin.com/company/{slug}",
            is_targeted=False,
            week_assigned=None,
            city=city,
            employee_count=employee_count,
            score=company_score,
            priority_tier=priority_tier(company_score),
        )
        db.add(company_obj)
        db.flush()

        if company_obj.score >= QUALIFIED_SCORE_THRESHOLD:
            for stakeholder in generate_stakeholders(company_obj):
                db.add(Contact(**stakeholder))

        existing_names.add(company["name"])
        inserted += 1

    db.commit()
    return {"inserted": inserted, "skipped": skipped}
