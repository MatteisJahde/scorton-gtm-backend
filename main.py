"""
Scorton GTM API — crash-proof Render entrypoint.

Render start command (recommended):
  uvicorn main:app --host 0.0.0.0 --port $PORT --log-level debug
"""

import asyncio
import csv
import os
import time
import traceback
from collections import Counter
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def log_startup_step(step: str, detail: str = "") -> None:
    """Emit a flush-safe log line for Render startup troubleshooting."""
    message = f"[startup] STEP: {step}"
    if detail:
        message = f"{message} | {detail}"
    print(message, flush=True)


def pause_for_log_inspection(reason: str, exc: Optional[BaseException] = None) -> None:
    """Print diagnostics and keep the process alive briefly for Render log capture."""
    print(f"[startup] DEBUG PAUSE: {reason}", flush=True)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        traceback.print_exc()
    print(
        "[startup] sleeping 60 seconds to keep process alive for Render log inspection...",
        flush=True,
    )
    time.sleep(60)


def verify_dependencies() -> None:
    """Import required third-party libraries and log SUCCESS for each."""
    required_packages = (
        ("httpx", "httpx"),
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("pandas", "pandas"),
        ("sqlalchemy", "sqlalchemy"),
        ("requests", "requests"),
        ("pydantic", "pydantic"),
        ("multipart", "multipart"),
    )
    for label, module_name in required_packages:
        try:
            __import__(module_name)
            log_startup_step("dependency", f"SUCCESS: {label}")
        except Exception as exc:
            log_startup_step("dependency", f"FAILED: {label} — {exc}")
            raise


def check_required_environment() -> List[str]:
    """Return names of required environment variables that are missing."""
    missing: List[str] = []
    zb_key = (os.getenv("ZEROBOUNCE_API_KEY") or os.getenv("ZEROBOUNCE_KEY") or "").strip()
    if not zb_key:
        missing.append("ZEROBOUNCE_API_KEY")
    return missing


def enforce_required_environment() -> None:
    """Log MISSING_ENV lines and pause so Render logs remain readable."""
    missing = check_required_environment()
    if not missing:
        log_startup_step("env", "all required environment variables are present")
        return
    for var_name in missing:
        print(f"MISSING_ENV: {var_name}", flush=True)
    pause_for_log_inspection("required environment variable(s) missing")


log_startup_step("boot", "crash-proof entrypoint — verifying dependencies")
try:
    verify_dependencies()
except Exception as dep_exc:
    pause_for_log_inspection("dependency verification failed", dep_exc)

enforce_required_environment()

import uvicorn
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from city_utils import extract_city_from_record, normalize_city_name
from database import Base, SessionLocal, engine, get_db
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
    is_placeholder_company,
    ORIGINAL_TARGET_CITIES,
    target_account_to_dict,
)
from deduplication import deduplicate_company_records
from ingestion import ingest_companies
from models import Company, Contact, TargetAccount
from seed_data import (
    ACTUAL_COMPANIES_CSV,
    actual_companies_available,
    get_companies,
    get_company_csv_extras,
    load_actual_companies_with_report,
)
from services.weekly_batch import pull_weekly_batch
from services.domain_verification import verify_csv_bytes
from services.industry_filter import passes_financial_icp_filter
from services.url_utils import (
    domain_from_website,
    normalize_website,
    website_display_status,
)
from services.contact_fields import attach_contact_aliases
from services.lead_parity import verify_lead_count_parity
from services.website_maintenance import (
    partition_companies_by_website,
    purge_unreachable_companies_from_database,
    reverify_and_prune_database_leads,
)
from services.zerobounce_async import (
    ZEROBOUNCE_RATE_LIMIT_DELAY_SECONDS,
    apply_zerobounce_to_extras,
    verify_email_with_zerobounce,
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


# Force-load the correct file on startup (background task only — never at import).
DATA_FILE_PATH = "actual_companies.csv"
_db_initialization_complete = False
_db_initialization_finished_at: Optional[str] = None
_initialization_parity: dict[str, Any] = {}


_background_initialization_task: Optional[asyncio.Task[None]] = None


async def _verify_leads_with_zerobounce(companies: list[dict[str, Any]]) -> None:
    """Run ZeroBounce validation for every CSV lead before database ingest."""
    total = len(companies)
    log_startup_step("zerobounce", f"validating {total} lead emails")

    for index, company in enumerate(companies, start=1):
        company_name = str(company.get("name") or "").strip()
        extras = get_company_csv_extras(company_name) or dict(company.get("csv_extras") or {})
        work_email = str(extras.get("work_email") or "").strip()

        if not work_email:
            print(f"[zerobounce] ({index}/{total}) {company_name}: no work_email — skipped", flush=True)
            continue

        result = await verify_email_with_zerobounce(work_email)
        apply_zerobounce_to_extras(extras, result)
        company["csv_extras"] = extras

        print(
            f"[zerobounce] ({index}/{total}) {company_name} <{work_email}> -> "
            f"{result.get('zerobounce_status')} ({extras.get('zerobounce_email_status')})",
            flush=True,
        )

        if index < total:
            await asyncio.sleep(ZEROBOUNCE_RATE_LIMIT_DELAY_SECONDS)


def _setup_database_schema() -> None:
    log_startup_step("database", "creating tables")
    Base.metadata.create_all(bind=engine)
    log_startup_step("database", "running migrations")
    migrate_db()


def _persist_companies_to_database(
    companies: list[dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    """
    Ingest pre-validated companies and rebuild the target dataset.

    Returns (success, parity_report).
    """
    global _initialization_parity
    db = SessionLocal()
    try:
        log_startup_step("database", "clearing existing company records")
        db.query(TargetAccount).delete()
        db.query(Contact).delete()
        db.query(Company).delete()
        db.commit()

        processed_count = len(companies)
        log_startup_step("website", f"HEAD-checking {processed_count} company websites")
        website_partition = partition_companies_by_website(companies)
        companies = website_partition["reachable"]
        log_startup_step(
            "website",
            (
                f"reachable={website_partition['summary']['reachable']} "
                f"unreachable={website_partition['summary']['unreachable']}"
            ),
        )

        processed_count = len(companies)
        log_startup_step("database", f"ingesting {processed_count} companies")
        ingest_result = ingest_companies(db, companies=companies)
        if ingest_result.get("error"):
            print(f"[database] Ingest failed: {ingest_result}", flush=True)
            _initialization_parity = {
                "ok": False,
                "processed": processed_count,
                "saved": 0,
                "message": str(ingest_result.get("error")),
            }
            return False, _initialization_parity

        saved_count = int(ingest_result.get("inserted") or 0)
        parity = verify_lead_count_parity(processed=processed_count, saved=saved_count)
        _initialization_parity = parity
        log_startup_step("parity", parity["message"])

        if not parity["ok"]:
            log_startup_step(
                "parity",
                "ERROR — lead counts do not match; rolling back database writes. "
                f"Skipped during ingest: {ingest_result.get('skipped', 0)}",
            )
            db.query(TargetAccount).delete()
            db.query(Contact).delete()
            db.query(Company).delete()
            db.commit()
            return False, parity

        log_startup_step("database", "building target dataset")
        build_result = build_target_dataset(db)
        log_startup_step("website", "re-verifying existing dashboard websites")
        website_refresh = reverify_and_prune_database_leads(db)
        verification = (ingest_result.get("csv_validation") or {}).get("verification") or {}
        print(
            f"[database] Lead verification — Verified: {verification.get('verified', 0)} | "
            f"Unverified: {verification.get('unverified', 0)}",
            flush=True,
        )
        print(
            {
                "startup_ingest": ingest_result,
                "startup_build_target_dataset": build_result,
                "lead_parity": parity,
                "website_validation": website_partition.get("summary"),
                "website_refresh": website_refresh,
            },
            flush=True,
        )
        return True, parity
    except Exception as exc:
        print(f"[database] Persist FAILED: {exc}", flush=True)
        traceback.print_exc()
        raise
    finally:
        db.close()


async def _initialize_database_async(data_file_path: str) -> None:
    """Async startup path: schema setup → CSV parse → ZeroBounce → DB persist."""
    global _db_initialization_complete, _db_initialization_finished_at
    log_startup_step("initialize", "background database initialization started")
    csv_path = Path(__file__).resolve().parent / data_file_path
    log_startup_step("initialize", f"CSV path: {csv_path}")

    if not csv_path.exists():
        log_startup_step("initialize", f"CSV not found — skipping ingest: {csv_path}")
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _setup_database_schema)

    log_startup_step("initialize", "parsing CSV rows")
    companies, _report = await loop.run_in_executor(
        None, load_actual_companies_with_report, csv_path
    )
    if not companies:
        log_startup_step("initialize", "no valid rows found in CSV")
        return

    scraper_count = len(companies)
    log_startup_step("initialize", f"scraper loaded {scraper_count} leads")
    await _verify_leads_with_zerobounce(companies)

    if len(companies) != scraper_count:
        log_startup_step(
            "parity",
            f"ERROR — ZeroBounce step dropped leads ({scraper_count} -> {len(companies)})",
        )
        return

    log_startup_step("initialize", "persisting companies to database")
    success, parity = await loop.run_in_executor(
        None, _persist_companies_to_database, companies
    )
    if not success:
        log_startup_step(
            "initialize",
            "halted after parity failure — HTTP server remains running",
        )
        return

    _db_initialization_complete = True
    _db_initialization_finished_at = datetime.now(timezone.utc).isoformat()
    log_startup_step("initialize", "completed successfully")


def initialize_database(data_file_path: str) -> None:
    """Connect to SQLite, run migrations, and load seed CSV. Never crash startup."""
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_initialize_database_async(data_file_path))
        else:
            loop.create_task(_initialize_database_async(data_file_path))
    except Exception as exc:
        log_startup_step("initialize", f"FAILED: {exc}")
        traceback.print_exc()


def _load_seed_data(data_file_path: str) -> None:
    """Backward-compatible sync CSV load wrapper."""
    initialize_database(data_file_path)


def load_data(data_file_path: str) -> None:
    """Backward-compatible alias for startup CSV load."""
    initialize_database(data_file_path)


async def _run_background_initialization() -> None:
    """Load CSV data after the HTTP server is already accepting connections."""
    try:
        log_startup_step("background", "CSV initialization task started")
        await _initialize_database_async(DATA_FILE_PATH)
        log_startup_step(
            "background",
            "CSV initialization task finished — uvicorn process stays alive",
        )
    except Exception as exc:
        log_startup_step("background", f"CSV initialization FAILED: {exc}")
        traceback.print_exc()
        pause_for_log_inspection("background CSV initialization failed", exc)


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    """Keep uvicorn running while CSV/ZeroBounce init happens in the background."""
    global _background_initialization_task
    try:
        log_startup_step("lifespan", "FastAPI lifespan startup — binding HTTP server")
        _background_initialization_task = asyncio.create_task(_run_background_initialization())
        log_startup_step("lifespan", "yielding to uvicorn — process will remain running")
        yield
    except Exception as exc:
        log_startup_step("lifespan", f"startup failed: {exc}")
        traceback.print_exc()
        pause_for_log_inspection("FastAPI lifespan startup failed", exc)
        raise
    finally:
        log_startup_step("lifespan", "shutdown requested — cancelling background task")
        if _background_initialization_task is not None:
            _background_initialization_task.cancel()
            with suppress(asyncio.CancelledError):
                await _background_initialization_task
        log_startup_step("lifespan", "shutdown complete")


try:
    log_startup_step("boot", "creating FastAPI application")
    app = FastAPI(title="Scorton GTM API", version="1.0.0", lifespan=app_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
except Exception as app_exc:
    pause_for_log_inspection("FastAPI application initialization failed", app_exc)
    app = FastAPI(title="Scorton GTM API (degraded)", version="1.0.0")

    @app.get("/health")
    def degraded_health():
        return {
            "status": "degraded",
            "error": "Application failed to initialize — check Render logs.",
        }


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


@app.get("/health")
def health():
    return json_success(
        {
            "status": "ok",
            "db_ready": _db_initialization_complete,
            "initialization_finished_at": _db_initialization_finished_at,
            "lead_parity": _initialization_parity,
        },
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
        "signal_score",
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


def _lead_website(lead: dict) -> str:
    """Resolve website URL from CSV extras, then stored lead fields."""
    company_name = str(lead.get("company_name") or lead.get("company") or "").strip()
    csv_website = ""
    if company_name:
        csv_website = get_company_csv_extras(company_name).get("website") or ""
    return normalize_website(
        csv_website or lead.get("company_website") or lead.get("website") or ""
    )


def _lead_domain(lead: dict) -> str:
    """Extract hostname from a lead website for dashboard contact display."""
    return domain_from_website(_lead_website(lead))


def _enrich_lead_score_fields(lead: dict) -> dict:
    """Normalize score and website fields for dashboard API consumers."""
    enriched = dict(lead)
    score = _lead_score(enriched)
    enriched["company_ai_signal"] = score
    enriched["signal_score"] = score
    website = _lead_website(enriched)
    enriched["website"] = website
    enriched["company_website"] = website
    domain = _lead_domain(enriched)
    enriched["domain"] = domain
    if enriched.get("website_reachable") is False:
        enriched["website_status"] = "unavailable"
        enriched["website_link"] = ""
    else:
        enriched["website_status"] = website_display_status(website)
        enriched["website_link"] = website if enriched["website_status"] == "ready" else ""
    return attach_contact_aliases(enriched)


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


def _raw_lead_city(lead: dict) -> Optional[str]:
    """Read city from a lead record without applying location defaults."""
    city = extract_city_from_record(lead)
    return city or None


def _format_lead_city_display(raw_city: Optional[str]) -> Optional[str]:
    """Format city for API output; return null when no specific city is known."""
    return normalize_city_name(raw_city)


def _log_leads_summary_city_debug(leads: list[dict], *, context: str) -> None:
    """Print unique city values to help debug location column mapping."""
    raw_cities = [_raw_lead_city(lead) for lead in leads]
    unique = sorted({city for city in raw_cities if city}, key=str.lower)
    missing = sum(1 for city in raw_cities if not city)
    counts = Counter(city or "(empty)" for city in raw_cities)
    print(f"[leads-summary] {context}: {len(unique)} unique non-empty city values: {unique}")
    print(f"[leads-summary] {context}: {missing} leads with missing/empty city (of {len(leads)} total)")
    print(f"[leads-summary] {context}: city value counts: {dict(sorted(counts.items()))}")


def _leads_to_dataframe(leads: list[dict]) -> pd.DataFrame:
    """Convert lead records to a dataframe with CSV-aligned column names."""
    records = []
    for lead in leads:
        company = str(lead.get("company") or lead.get("company_name") or "").strip()
        raw_city = _raw_lead_city(lead)
        records.append(
            {
                "id": lead.get("id"),
                "company": company,
                "website": _lead_website(lead),
                "company_website": _lead_website(lead),
                "domain": _lead_domain(lead),
                "website_status": website_display_status(_lead_website(lead)),
                "website_link": _lead_website(lead)
                if website_display_status(_lead_website(lead)) == "ready"
                else "",
                "industry": lead.get("industry"),
                "city": _format_lead_city_display(raw_city),
                "intent": _lead_intent(lead),
                LEADS_SUMMARY_SCORE_FIELD: _lead_score(lead),
                "signal_score": _lead_score(lead),
                "buyer_name": lead.get("buyer_name"),
                "job_title": lead.get("job_title"),
                "work_email": lead.get("work_email"),
                "contact_name": lead.get("contact_name"),
                "contact_role": lead.get("contact_role"),
                "verified_email": lead.get("verified_email"),
                "contact_status": lead.get("contact_status"),
                "email_status": lead.get("email_status"),
                "zerobounce_status": lead.get("zerobounce_status"),
                "zerobounce_sub_status": lead.get("zerobounce_sub_status"),
                "zerobounce_email_status": lead.get("zerobounce_email_status"),
                "lead_verification_status": lead.get("lead_verification_status"),
                "verification_status": lead.get("verification_status"),
                "contact_verification_status": lead.get("contact_verification_status"),
            }
        )
    return pd.DataFrame(records)


def build_leads_summary(leads: list[dict], *, top_n: int = TOP_LEADS_LIMIT) -> dict:
    """Build dashboard summary: counts plus top high-intent leads by score."""
    if not leads:
        return {
            "total_leads": 0,
            "high_intent_leads": 0,
            "top_leads": [],
            "diagnostics": {
                "db_ready": _db_initialization_complete,
                "initialization_finished_at": _db_initialization_finished_at,
                "lead_parity": _initialization_parity,
            },
        }

    unique_leads, _report = deduplicate_company_records(leads, label="leads_summary")
    _log_leads_summary_city_debug(unique_leads, context="all deduplicated leads")

    df = _leads_to_dataframe(unique_leads)
    total_leads = len(df)

    high_intent_df = df[df["intent"] == HIGH_INTENT_VALUE].copy()
    high_intent_df = high_intent_df.sort_values(
        LEADS_SUMMARY_SCORE_FIELD,
        ascending=False,
    )
    top_100_leads = high_intent_df.head(top_n)

    high_intent_source = sorted(
        (lead for lead in unique_leads if _lead_intent(lead) == HIGH_INTENT_VALUE),
        key=_lead_score,
        reverse=True,
    )[:top_n]
    _log_leads_summary_city_debug(
        high_intent_source,
        context=f"top {len(high_intent_source)} high-intent leads (raw source values)",
    )

    # Temporary debug: verify city diversity before serializing the API response.
    print(df["city"].unique())
    top_100_leads = top_100_leads.copy()

    return {
        "total_leads": total_leads,
        "high_intent_leads": len(high_intent_df),
        "top_leads": _serialize_lead_records(top_100_leads),
        "diagnostics": {
            "db_ready": _db_initialization_complete,
            "initialization_finished_at": _db_initialization_finished_at,
            "lead_parity": _initialization_parity,
        },
    }


def calculate_dashboard_metrics(leads: list[dict]) -> dict:
    """Compute total vs high-intent lead counts from the full lead dataset."""
    total_leads = len(leads)
    high_intent_leads = sum(1 for lead in leads if _is_high_intent_lead(lead))
    return {
        "total_leads": total_leads,
        "high_intent_leads": high_intent_leads,
    }


def _is_allowed_lead_city(city: Optional[str]) -> bool:
    if not city:
        return True
    return city in ORIGINAL_TARGET_CITIES


def _serialize_lead_records(df: pd.DataFrame) -> list[dict]:
    """Serialize dataframe rows; use JSON null when city is unknown."""
    records = df.to_dict(orient="records")
    for record in records:
        city = record.get("city")
        if city is None or (isinstance(city, float) and pd.isna(city)):
            record["city"] = None
        else:
            record["city"] = normalize_city_name(city)
    return records


def _filter_allowed_leads(leads: list[dict]) -> list[dict]:
    """Exclude invalid cities, blocklisted companies, and unreachable websites."""
    filtered: list[dict] = []
    for lead in leads:
        if lead.get("website_reachable") is False:
            continue
        city = _raw_lead_city(lead)
        if city and city not in ORIGINAL_TARGET_CITIES:
            continue
        accepted, _reason = passes_financial_icp_filter(lead)
        if not accepted:
            continue
        normalized = dict(lead)
        normalized["city"] = city
        filtered.append(normalized)
    return filtered


def _load_leads_from_db(db: Session) -> list[dict]:
    rows = (
        db.query(TargetAccount, Company)
        .join(Company, TargetAccount.company_id == Company.id)
        .order_by(
            TargetAccount.trust_opportunity_score.desc(),
            TargetAccount.icp_score.desc(),
            TargetAccount.id,
        )
        .all()
    )
    leads: list[dict] = []
    for account, company in rows:
        if is_placeholder_company(account.company_name):
            continue
        city = extract_city_from_record(
            {
                "city": account.city,
                "locality": company.locality,
            }
        )
        if city and city not in ORIGINAL_TARGET_CITIES:
            continue
        lead = target_account_to_dict(account)
        lead["city"] = city
        lead["website_reachable"] = company.website_reachable
        lead["website_http_status"] = company.website_http_status
        lead["website_checked_at"] = (
            company.website_checked_at.isoformat() if company.website_checked_at else None
        )
        if company.website_reachable is False:
            continue
        if city:
            lead["locality"] = city
        leads.append(lead)
    return sort_companies_for_final_cut(leads)


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

        leads = _filter_allowed_leads(leads)
        unique_leads, _report = deduplicate_company_records(leads, label="api_leads")
        return [_enrich_lead_score_fields(lead) for lead in unique_leads]
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
    enriched = [_enrich_lead_score_fields(lead) for lead in unique_leads]
    if limit is None:
        return enriched[offset:]
    return enriched[offset : offset + limit]


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
                "signal_score",
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
    """Ingest companies from actual_companies.csv in the project root."""
    result = ingest_companies(db)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result)
    return result


@api_router.post("/ingest")
def api_ingest(db: Session = Depends(get_db)):
    return ingest(db)


@api_router.post("/reload-from-csv")
def api_reload_from_csv(db: Session = Depends(get_db)):
    """Re-ingest actual_companies.csv and rebuild the target dataset."""
    print("Loading from actual_companies.csv", flush=True)
    print(f"[reload-from-csv] CSV path: {ACTUAL_COMPANIES_CSV.resolve()}", flush=True)

    if not actual_companies_available():
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Missing {ACTUAL_COMPANIES_CSV.name} in project root",
                "expected_columns": [
                    "company",
                    "website",
                    "industry",
                    "city",
                    "employee_count",
                    "intent",
                    "signal_score",
                    "buyer_name",
                    "job_title",
                    "work_email",
                ],
                "allowed_cities": sorted(ALLOWED_CITIES),
            },
        )

    db.query(TargetAccount).delete()
    db.query(Contact).delete()
    db.query(Company).delete()
    db.commit()

    ingest_result = ingest_companies(db)
    if ingest_result.get("error"):
        raise HTTPException(status_code=400, detail=ingest_result)

    verification = (ingest_result.get("csv_validation") or {}).get("verification") or {}
    dataset_verification = {}
    build_result = build_target_dataset(db)
    website_refresh = reverify_and_prune_database_leads(db)
    dataset_verification = build_result.get("verification") or {}

    verified_count = verification.get("verified", 0)
    unverified_count = verification.get("unverified", 0)
    print(
        f"[reload-from-csv] Lead verification — Verified: {verified_count} | "
        f"Unverified: {unverified_count} "
        f"(email_failed: {verification.get('email_failed', 0)}, "
        f"contact_failed: {verification.get('contact_failed', 0)})",
        flush=True,
    )
    print(
        f"[reload-from-csv] Target dataset — verified_in_dataset: "
        f"{dataset_verification.get('verified_in_dataset', 0)} | "
        f"unverified_excluded: {dataset_verification.get('unverified_excluded', 0)}",
        flush=True,
    )

    return {
        "source": str(ACTUAL_COMPANIES_CSV),
        "csv_rows": len(get_companies()),
        "ingest": ingest_result,
        "build_target_dataset": build_result,
        "website_refresh": website_refresh,
        "verification_summary": {
            "csv_verified": verified_count,
            "csv_unverified": unverified_count,
            "email_failed": verification.get("email_failed", 0),
            "contact_failed": verification.get("contact_failed", 0),
            "verified_in_dataset": dataset_verification.get("verified_in_dataset", 0),
            "unverified_excluded_from_dataset": dataset_verification.get(
                "unverified_excluded", 0
            ),
        },
    }


@api_router.post("/reverify-websites")
def api_reverify_websites(db: Session = Depends(get_db)):
    """Re-check stored lead websites and remove unreachable companies from the dashboard."""
    result = purge_unreachable_companies_from_database(db)
    return json_success(
        result,
        meta={"message": "Website re-verification and purge complete"},
    )


@api_router.post("/purge-unreachable-companies")
def api_purge_unreachable_companies(db: Session = Depends(get_db)):
    """
    One-off maintenance: HEAD-check every company in the database and delete
    records where the website is unreachable (non-200, timeout, or invalid URL).
    """
    result = purge_unreachable_companies_from_database(db)
    return json_success(
        result,
        meta={
            "message": (
                "Unreachable companies deleted. Refresh the Lovable dashboard to "
                "see cleaned data."
            ),
        },
    )


@api_router.post("/verify-leads-csv")
async def api_verify_leads_csv(file: UploadFile = File(...)):
    """
    Temporary endpoint: verify website domains in an uploaded leads CSV.

    Accepts a CSV with a `website` column, checks each URL (HEAD then GET),
    and returns only rows with active domains.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv file.")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded CSV is empty.")

    try:
        result = verify_csv_bytes(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Domain verification failed: {exc}",
        ) from exc

    summary = result["summary"]
    print(
        f"[verify-leads-csv] read={summary['read']} kept={summary['kept']} "
        f"discarded={summary['discarded']}",
        flush=True,
    )

    return json_success(
        {
            "csv": result["csv"],
            "summary": summary,
            "discarded": [
                {
                    "company": row.get("company"),
                    "website": row.get("website"),
                    "validation_status": row.get("validation_status"),
                    "validation_detail": row.get("validation_detail"),
                }
                for row in result["discarded_rows"]
            ],
        }
    )


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


def run_crash_proof_server() -> None:
    """Start uvicorn in the foreground with verbose logging for Render debugging."""
    try:
        enforce_required_environment()
        port = int(os.environ.get("PORT", "10000"))
        log_startup_step(
            "main",
            f"starting uvicorn main:app on 0.0.0.0:{port} with --log-level debug",
        )
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=port,
            reload=False,
            log_level="debug",
        )
    except Exception as exc:
        pause_for_log_inspection("uvicorn startup failed", exc)


if __name__ == "__main__":
    try:
        log_startup_step("boot", "running crash-proof __main__ entrypoint")
        run_crash_proof_server()
    except Exception as exc:
        pause_for_log_inspection("unhandled error in __main__ entrypoint", exc)
