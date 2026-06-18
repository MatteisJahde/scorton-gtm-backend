import csv
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
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
from deduplication import deduplicate_company_records
from ingestion import ingest_companies
from models import Company, Contact, TargetAccount
from services.weekly_batch import pull_weekly_batch

app = FastAPI(title="Scorton GTM API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api")

QUALIFIED_BATCH_SIZE = 100
HIGH_INTENT_SCORE_THRESHOLD = 80
HIGH_INTENT_VALUE = "high"
TOP_LEADS_LIMIT = 100
LEADS_SUMMARY_SCORE_FIELD = "company_ai_signal"
CYBERSECURITY_LEADS_PATH = (
    Path(__file__).resolve().parent / "data" / "target_dataset_1000_companies.csv"
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
            "cors_origins": ["*"],
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
    return [lead for lead in leads if _is_high_intent_lead(lead)]


def _is_high_intent_lead(lead: dict) -> bool:
    """High intent when resolved lead score meets the configured threshold."""
    if str(lead.get("intent", "")).strip().lower() == HIGH_INTENT_VALUE:
        return True
    return _lead_score(lead) >= HIGH_INTENT_SCORE_THRESHOLD


def _lead_intent(lead: dict) -> str:
    if str(lead.get("intent", "")).strip().lower() == HIGH_INTENT_VALUE:
        return HIGH_INTENT_VALUE
    return HIGH_INTENT_VALUE if _lead_score(lead) >= HIGH_INTENT_SCORE_THRESHOLD else "low"


def _leads_to_dataframe(leads: list[dict]) -> pd.DataFrame:
    """Convert lead records to a dataframe with CSV-aligned column names."""
    records = []
    for lead in leads:
        company = str(lead.get("company") or lead.get("company_name") or "").strip()
        records.append(
            {
                "id": lead.get("id"),
                "company": company,
                "company_website": lead.get("company_website") or lead.get("website"),
                "industry": lead.get("industry"),
                "city": lead.get("city"),
                "intent": _lead_intent(lead),
                LEADS_SUMMARY_SCORE_FIELD: _lead_score(lead),
                "buyer_name": lead.get("buyer_name"),
                "job_title": lead.get("job_title"),
                "work_email": lead.get("work_email"),
            }
        )
    return pd.DataFrame(records)


def build_leads_summary(leads: list[dict], *, top_n: int = TOP_LEADS_LIMIT) -> dict:
    """Build dashboard summary: counts plus top high-intent leads by score."""
    if not leads:
        return {"total_leads": 0, "high_intent_leads": 0, "top_leads": []}

    unique_leads, _report = deduplicate_company_records(leads, label="leads_summary")
    df = _leads_to_dataframe(unique_leads)
    total_leads = len(df)

    high_intent_df = df[df["intent"] == HIGH_INTENT_VALUE].copy()
    high_intent_df = high_intent_df.sort_values(
        LEADS_SUMMARY_SCORE_FIELD,
        ascending=False,
    )
    top_100_leads = high_intent_df.head(top_n)

    return {
        "total_leads": total_leads,
        "high_intent_leads": len(high_intent_df),
        "top_leads": top_100_leads.to_dict(orient="records"),
    }


def calculate_dashboard_metrics(leads: list[dict]) -> dict:
    """Compute total vs high-intent lead counts from the full lead dataset."""
    total_leads = len(leads)
    high_intent_leads = sum(1 for lead in leads if _is_high_intent_lead(lead))
    return {
        "total_leads": total_leads,
        "high_intent_leads": high_intent_leads,
    }


def _load_leads_from_db(db: Session) -> list[dict]:
    rows = (
        db.query(TargetAccount)
        .order_by(
            TargetAccount.trust_opportunity_score.desc(),
            TargetAccount.icp_score.desc(),
            TargetAccount.id,
        )
        .all()
    )
    return sort_companies_for_final_cut(
        [target_account_to_dict(account) for account in rows]
    )


def get_all_leads(db: Session) -> list[dict]:
    """Load all leads from TargetAccount or the master CSV (no intent filter)."""
    try:
        if db.query(TargetAccount.id).first():
            leads = _load_leads_from_db(db)
        elif CYBERSECURITY_LEADS_PATH.exists():
            with CYBERSECURITY_LEADS_PATH.open(encoding="utf-8") as leads_file:
                leads = [
                    expand_standard_csv_row(row) for row in csv.DictReader(leads_file)
                ]
            leads.sort(
                key=lambda lead: float(
                    lead.get("trust_opportunity_score")
                    or lead.get("company_ai_signal")
                    or lead.get("ai_signal")
                    or 0
                ),
                reverse=True,
            )
        else:
            leads = []

        unique_leads, _report = deduplicate_company_records(leads, label="api_leads")
        return unique_leads
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load leads: {exc}",
        ) from exc


def get_unique_target_dataset(
    db: Session,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    leads = _load_leads_from_db(db)
    unique_leads, _report = deduplicate_company_records(leads, label="target_dataset")
    if limit is None:
        return unique_leads[offset:]
    return unique_leads[offset : offset + limit]


def get_qualified_companies(db: Session) -> list[dict]:
    """Load high-intent leads only (score >= 80)."""
    return _filter_high_intent_leads(get_all_leads(db))


@api_router.get("/dashboard-metrics")
def api_dashboard_metrics(db: Session = Depends(get_db)):
    metrics = calculate_dashboard_metrics(get_all_leads(db))
    return json_success(
        metrics,
        meta={
            "score_fields": [
                "score",
                "trust_opportunity_score",
                "qualification_score",
                "company_ai_signal",
                "ai_signal",
            ],
            "high_intent_rule": f"score >= {HIGH_INTENT_SCORE_THRESHOLD}",
        },
    )


@app.get("/api/leads-summary")
def leads_summary(db: Session = Depends(get_db)):
    """Dashboard summary: counts and top 100 high-intent leads by score."""
    return build_leads_summary(get_all_leads(db))


@api_router.get("/qualified-companies")
def api_qualified_companies(db: Session = Depends(get_db)):
    all_qualified_companies = get_qualified_companies(db)
    balanced = sort_companies_for_final_cut(all_qualified_companies)[:QUALIFIED_BATCH_SIZE]
    return json_success(
        balanced,
        meta={"high_intent_threshold": HIGH_INTENT_SCORE_THRESHOLD},
    )


@api_router.get("/qualified-accounts")
def api_qualified_accounts(db: Session = Depends(get_db)):
    return api_qualified_companies(db)


@api_router.get("/target-dataset")
def api_target_dataset(
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    leads = get_unique_target_dataset(db, limit=limit, offset=offset)
    return json_success(_filter_high_intent_leads(leads))


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


@app.get("/qualified-accounts")
def qualified_accounts(db: Session = Depends(get_db)):
    return qualified_companies(db)


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
    leads = get_unique_target_dataset(db, limit=limit, offset=offset)
    return _filter_high_intent_leads(leads)


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
