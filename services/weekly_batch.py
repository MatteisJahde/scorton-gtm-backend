from typing import List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Company

WEEKLY_BATCH_SIZE = 1000


def company_to_pdl_dict(company: Company) -> dict:
    return {
        "id": company.id,
        "name": company.name,
        "website": company.website,
        "industry": company.industry,
        "size": company.size,
        "locality": company.locality,
        "country": company.country,
        "linkedin_url": company.linkedin_url,
        "is_targeted": company.is_targeted,
        "week_assigned": company.week_assigned,
        "city": company.city,
        "employee_count": company.employee_count,
    }


def pull_weekly_batch(db: Session, current_week: int) -> dict:
    if current_week < 1:
        raise HTTPException(status_code=400, detail="current_week must be >= 1")

    available = (
        db.query(Company)
        .filter(Company.is_targeted.is_(False))
        .order_by(Company.id)
        .limit(WEEKLY_BATCH_SIZE)
        .all()
    )

    if len(available) < WEEKLY_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Only {len(available)} untargeted records available; "
                f"{WEEKLY_BATCH_SIZE} required for a weekly batch."
            ),
        )

    for company in available:
        company.is_targeted = True
        company.week_assigned = current_week

    db.commit()

    for company in available:
        db.refresh(company)

    rows = [company_to_pdl_dict(company) for company in available]
    return {
        "current_week": current_week,
        "count": len(rows),
        "companies": rows,
    }
