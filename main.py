import csv
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from migrations import migrate_db
from settings import (
    API_BASE_URL,
    ENVIRONMENT,
    FRONTEND_URL,
)
from sorting_agent import (
    ALLOWED_CITIES,
    CITY_TARGET_QUOTAS,
    MAX_TARGET_ACCOUNTS,
    sort_companies_for_final_cut,
)
from dataset_builder import (
    expand_standard_csv_row,
    build_dataset,
    build_target_dataset,
    enrich_target_dataset,
    export_dataset_csv,
    export_target_dataset_csv,
    export_target_dataset_xlsx,
    target_account_to_dict,
)
from ingestion import ingest_companies
from models import Company, Contact, TargetAccount
from services.weekly_batch import pull_weekly_batch

app = FastAPI(title="Scorton GTM API", version="1.0.0")
api_router = APIRouter(prefix="/api")

QUALIFIED_BATCH_SIZE = 100
HIGH_INTENT_SCORE_THRESHOLD = 80
CYBERSECURITY_LEADS_PATH = (
    Path(__file__).resolve().parent / "data" / "target_dataset_1000_companies.csv"
)

# Must match your Lovable frontend origin exactly.
origins = [
    "https://lovable.dev",
    "https://lovable.app",
    "https://lovable.dev/projects/a07c73da-b286-487a-9379-0b9720716cd5",  # Your specific project
    "http://localhost:3000",
    "http://localhost:5173",
]

# Add your Lovable preview URL via .env: FRONTEND_URL=https://your-project.lovable.app
if FRONTEND_URL and FRONTEND_URL not in origins:
    origins.append(FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def json_error(
    *,
    status_code: int,
    error: str,
    detail: Any = None,
    path: Optional[str] = None,
) -> JSONResponse:
    payload: Dict[str, Any] = {
        "success": False,
        "error": error,
        "status_code": status_code,
    }
    if detail is not None:
        payload["detail"] = detail
    if path is not None:
        payload["path"] = path
    return JSONResponse(status_code=status_code, content=payload)


def json_success(data: Any, *, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"success": True, "data": data}
    if isinstance(data, list):
        payload["count"] = len(data)
    if meta:
        payload["meta"] = meta
    return payload


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
    return json_error(
        status_code=exc.status_code,
        error=detail,
        path=str(request.url.path),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return json_error(
        status_code=422,
        error="Validation error",
        detail=exc.errors(),
        path=str(request.url.path),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    detail = str(exc) if ENVIRONMENT == "development" else None
    return json_error(
        status_code=500,
        error="Internal server error",
        detail=detail,
        path=str(request.url.path),
    )


def _company_to_dict(company: Company) -> dict:
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
        "score": company.score,
        "priority_tier": company.priority_tier,
    }


def _contact_to_dict(contact: Contact) -> dict:
    return {
        "id": contact.id,
        "company_id": contact.company_id,
        "name": contact.name,
        "title": contact.title,
        "role_type": contact.role_type,
        "email": contact.email,
        "linkedin_url": contact.linkedin_url,
    }


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    migrate_db()


@app.get("/health")
def health():
    return json_success(
        {"status": "ok"},
        meta={"environment": ENVIRONMENT, "api_base_url": API_BASE_URL},
    )


@api_router.get("/health")
def api_health():
    return json_success(
        {"status": "ok"},
        meta={"environment": ENVIRONMENT, "api_base_url": API_BASE_URL},
    )


@api_router.get("/config")
def api_config():
    """Frontend bootstrap: API URL and environment for Lovable."""
    return json_success(
        {
            "api_base_url": API_BASE_URL,
            "frontend_url": FRONTEND_URL,
            "environment": ENVIRONMENT,
            "cors_origins": origins,
        }
    )


def _city_summary(db: Session) -> dict:
    target_rows = db.query(TargetAccount).all()
    company_rows = db.query(Company).all()

    target_by_city = Counter(account.city or "Unknown" for account in target_rows)
    company_by_city = Counter(company.city or "Unknown" for company in company_rows)

    return {
        "allowed_cities": sorted(ALLOWED_CITIES),
        "target_dataset_size": MAX_TARGET_ACCOUNTS,
        "city_target_quotas": dict(sorted(CITY_TARGET_QUOTAS.items())),
        "target_accounts_total": len(target_rows),
        "target_accounts_by_city": dict(sorted(target_by_city.items())),
        "companies_total": len(company_rows),
        "companies_by_city": dict(sorted(company_by_city.items())),
    }


@api_router.get("/city-summary")
def api_city_summary(db: Session = Depends(get_db)):
    return json_success(_city_summary(db))


@app.get("/city-summary")
def city_summary(db: Session = Depends(get_db)):
    return _city_summary(db)


@api_router.get("/companies")
def api_companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
    all_companies = [_company_to_dict(c) for c in rows]
    return json_success(all_companies[:250])


@app.get("/companies")
def companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
    all_companies = [_company_to_dict(c) for c in rows]
    return all_companies


def _lead_score(lead: dict) -> float:
    """Resolve a comparable score from DB rows or reference CSV rows."""
    for key in (
        "score",
        "trust_opportunity_score",
        "qualification_score",
        "company_ai_signal",
        "ai_signal",
    ):
        value = lead.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _filter_high_intent_leads(leads: list[dict]) -> list[dict]:
    """Return only High Intent leads (score >= 80)."""
    return [lead for lead in leads if _lead_score(lead) >= HIGH_INTENT_SCORE_THRESHOLD]


def get_qualified_companies(db: Session) -> list[dict]:
    """Load vetted cybersecurity leads from TargetAccount or the master CSV."""
    try:
        rows = (
            db.query(TargetAccount)
            .order_by(
                TargetAccount.trust_opportunity_score.desc(),
                TargetAccount.icp_score.desc(),
                TargetAccount.id,
            )
            .all()
        )

        if rows:
            leads = [target_account_to_dict(account) for account in rows]
            return _filter_high_intent_leads(sort_companies_for_final_cut(leads))

        if CYBERSECURITY_LEADS_PATH.exists():
            with CYBERSECURITY_LEADS_PATH.open(encoding="utf-8") as leads_file:
                all_qualified_companies = [
                    expand_standard_csv_row(row) for row in csv.DictReader(leads_file)
                ]
            all_qualified_companies.sort(
                key=lambda lead: float(
                    lead.get("trust_opportunity_score")
                    or lead.get("company_ai_signal")
                    or lead.get("ai_signal")
                    or 0
                ),
                reverse=True,
            )
            return _filter_high_intent_leads(all_qualified_companies)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load qualified companies: {exc}",
        ) from exc

    return []


@api_router.get("/qualified-companies")
def api_qualified_companies(db: Session = Depends(get_db)):
    all_qualified_companies = get_qualified_companies(db)
    balanced = sort_companies_for_final_cut(all_qualified_companies)[:QUALIFIED_BATCH_SIZE]
    return json_success(
        balanced,
        meta={"high_intent_threshold": HIGH_INTENT_SCORE_THRESHOLD},
    )


@api_router.get("/target-dataset")
def api_target_dataset(
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(TargetAccount).order_by(TargetAccount.id).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    leads = _filter_high_intent_leads(
        [target_account_to_dict(account) for account in rows]
    )
    return json_success(leads)


@api_router.get("/contacts")
def api_contacts(db: Session = Depends(get_db)):
    rows = db.query(Contact).all()
    return json_success([_contact_to_dict(c) for c in rows])


@api_router.get("/contacts/{company_id}")
def api_contacts_by_company(company_id: int, db: Session = Depends(get_db)):
    rows = db.query(Contact).filter(Contact.company_id == company_id).all()
    return json_success([_contact_to_dict(c) for c in rows])


@api_router.post("/pull-weekly-batch")
def pull_weekly_batch_endpoint(current_week: int, db: Session = Depends(get_db)):
    return json_success(pull_weekly_batch(db, current_week))


@app.get("/qualified-companies")
def qualified_companies(db: Session = Depends(get_db)):
    all_qualified_companies = get_qualified_companies(db)
    return all_qualified_companies


@app.get("/contacts")
def contacts(db: Session = Depends(get_db)):
    rows = db.query(Contact).all()
    return [_contact_to_dict(c) for c in rows]


@app.get("/contacts/{company_id}")
def contacts_by_company(company_id: int, db: Session = Depends(get_db)):
    rows = db.query(Contact).filter(Contact.company_id == company_id).all()
    return [_contact_to_dict(c) for c in rows]


@app.post("/ingest")
def ingest(db: Session = Depends(get_db)):
    return ingest_companies(db)


app.include_router(api_router)


@app.get("/dataset")
def dataset(db: Session = Depends(get_db)):
    return build_dataset(db)


@app.get("/export-dataset")
def export_dataset(db: Session = Depends(get_db)):
    rows = build_dataset(db)
    csv_content = export_dataset_csv(rows)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="target_accounts.csv"'},
    )


@app.post("/build-target-dataset")
def build_target_dataset_endpoint(db: Session = Depends(get_db)):
    return build_target_dataset(db)


@app.post("/enrich-target-dataset")
def enrich_target_dataset_endpoint(db: Session = Depends(get_db)):
    return enrich_target_dataset(db)


@app.get("/target-dataset")
def target_dataset(
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    query = db.query(TargetAccount).order_by(TargetAccount.id).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    return _filter_high_intent_leads(
        [target_account_to_dict(account) for account in rows]
    )


EXPORT_CSV_PATH = Path(__file__).resolve().parent / "data" / "export-target-dataset.csv"


@app.get("/export-target-dataset")
def export_target_dataset(db: Session = Depends(get_db)):
    if EXPORT_CSV_PATH.exists():
        csv_content = EXPORT_CSV_PATH.read_text(encoding="utf-8")
    else:
        csv_content = export_target_dataset_csv(db)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="target_dataset.csv"'},
    )


@app.get("/export-target-dataset-xlsx")
def export_target_dataset_xlsx_endpoint(db: Session = Depends(get_db)):
    xlsx_content = export_target_dataset_xlsx(db)
    return Response(
        content=xlsx_content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="target_dataset.xlsx"'},
    )
