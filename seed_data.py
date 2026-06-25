"""Load real company records from actual_companies.csv (no synthetic/PDL API data)."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from city_utils import normalize_city_name
from services.lead_validation import LEAD_STATUS_VERIFIED, validate_lead
from services.url_utils import normalize_website as _normalize_website_url
from services.industry_filter import passes_financial_icp_filter
from sorting_agent import ALLOWED_CITIES

ROOT_DIR = Path(__file__).resolve().parent
ACTUAL_COMPANIES_CSV = ROOT_DIR / "actual_companies.csv"

PLACEHOLDER_NAME_MARKERS = ("PDL Sample Co", "Sample Co")
ALLOWED_INDUSTRIES = {"Financial Services", "Insurance", "Accounting"}
MIN_EMPLOYEE_COUNT = 20
MAX_EMPLOYEE_COUNT = 500

# Friendly spreadsheet headers -> internal field names.
CSV_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("company", "name", "company_name"),
    "website": ("website", "company_website", "company website"),
    "industry": ("industry",),
    "city": ("city", "locality", "location"),
    "employee_count": (
        "employee_count",
        "employee count",
        "employees",
        "employee_size",
        "size",
    ),
    "intent": ("intent",),
    "signal_score": ("signal_score", "company_ai_signal", "ai_signal", "score"),
    "buyer_name": ("buyer_name", "buyer name", "reach out to"),
    "job_title": ("job_title", "job title", "title"),
    "work_email": ("work_email", "work email", "email"),
    "linkedin_url": ("linkedin_url", "company_linkedin_url", "linkedin", "linkedin url"),
}

_COMPANY_CSV_EXTRAS: dict[str, dict[str, Any]] = {}

SYNTHETIC_NAME_PATTERN = re.compile(
    r"\b(Atlas|Beacon|Crown|Delta|Echo|Falcon|Granite|Harbor|Iron|Juniper|"
    r"Keystone|Lighthouse|Meridian|Nova|Orion|Pioneer|Quantum|River|Sterling|Titan|"
    r"Union|Vertex|Westfield|Zenith|Axiom|Bridgewater|Catalyst|Dominion|Evergreen|Frontier)\b"
    r".*\bGroup\s+\d{4}\b",
    re.IGNORECASE,
)


@dataclass
class VerificationSummary:
    verified: int = 0
    unverified: int = 0
    email_failed: int = 0
    contact_failed: int = 0


@dataclass
class CsvLoadReport:
    accepted: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)
    verification: VerificationSummary = field(default_factory=VerificationSummary)


def actual_companies_path() -> Path:
    return ACTUAL_COMPANIES_CSV


def actual_companies_available() -> bool:
    return ACTUAL_COMPANIES_CSV.exists()


def _normalize_header(header: str) -> str:
    return header.strip().lower().replace("_", " ")


def _first_value(row: Mapping[str, str], aliases: tuple[str, ...]) -> str:
    normalized_row = {_normalize_header(key): (value or "").strip() for key, value in row.items()}
    for alias in aliases:
        value = normalized_row.get(_normalize_header(alias), "")
        if value:
            return value
    return ""


def _parse_employee_count(raw: str) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.match(r"(\d+)\s*-\s*(\d+)", text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return (low + high) // 2
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def is_placeholder_company(name: object) -> bool:
    value = str(name or "").strip()
    return any(marker in value for marker in PLACEHOLDER_NAME_MARKERS)


def is_synthetic_company_name(name: str) -> bool:
    value = str(name or "").strip()
    if not value:
        return True
    if is_placeholder_company(value):
        return True
    return bool(SYNTHETIC_NAME_PATTERN.search(value))


def _parse_int(raw: str) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def get_company_csv_extras(name: str) -> dict[str, Any]:
    return _COMPANY_CSV_EXTRAS.get(name, {})


def verification_report_dict(report: CsvLoadReport) -> dict[str, int]:
    return {
        "verified": report.verification.verified,
        "unverified": report.verification.unverified,
        "email_failed": report.verification.email_failed,
        "contact_failed": report.verification.contact_failed,
    }


def log_verification_summary(report: CsvLoadReport, *, context: str = "lead_verification") -> None:
    summary = verification_report_dict(report)
    print(
        f"[{context}] Verified: {summary['verified']} | "
        f"Unverified: {summary['unverified']} "
        f"(email_failed: {summary['email_failed']}, contact_failed: {summary['contact_failed']})",
        flush=True,
    )


def _record_verification_rejection(
    report: CsvLoadReport,
    company: str,
    validation: dict[str, Any],
) -> None:
    report.verification.unverified += 1
    reasons = validation.get("failure_reasons") or []
    if "email" in reasons:
        report.verification.email_failed += 1
        _reject(
            report,
            company,
            "email_verification_failed",
            detail=str(validation.get("verification_status") or validation["email"].get("detail")),
        )
        return
    if "contact" in reasons:
        report.verification.contact_failed += 1
        _reject(
            report,
            company,
            "contact_verification_failed",
            detail=str(validation.get("contact_verification_status") or validation["contact"].get("detail")),
        )
        return
    _reject(report, company, "lead_verification_failed")


def _reject(report: CsvLoadReport, company: str, reason: str, *, detail: str = "") -> None:
    entry = {"company": company or "(unknown)", "reason": reason}
    if detail:
        entry["detail"] = detail
    report.rejected.append(entry)


def map_csv_row_to_company(
    row: Mapping[str, str],
    *,
    report: Optional[CsvLoadReport] = None,
) -> Optional[dict[str, Any]]:
    """Map a CSV row to an internal company dict. Reject invalid rows (no remapping)."""
    name = _first_value(row, CSV_FIELD_ALIASES["name"])
    if not name:
        if report:
            _reject(report, "", "missing_company_name")
        return None
    if is_synthetic_company_name(name):
        if report:
            _reject(report, name, "synthetic_or_placeholder_name")
        return None

    website = _normalize_website_url(_first_value(row, CSV_FIELD_ALIASES["website"]))
    if not website:
        if report:
            _reject(report, name, "missing_website")
        return None

    industry = _first_value(row, CSV_FIELD_ALIASES["industry"])
    if not industry:
        if report:
            _reject(report, name, "missing_industry")
        return None
    accepted, rejection = passes_financial_icp_filter(
        {"company": name, "industry": industry, "website": website}
    )
    if not accepted:
        if report:
            _reject(report, name, rejection or "industry_not_allowed", detail=industry)
        return None

    raw_city = _first_value(row, CSV_FIELD_ALIASES["city"])
    if not raw_city:
        if report:
            _reject(report, name, "missing_city")
        return None

    city = normalize_city_name(raw_city)
    if not city:
        if report:
            _reject(
                report,
                name,
                "city_not_in_target_list",
                detail=raw_city,
            )
        return None
    if city not in ALLOWED_CITIES:
        if report:
            _reject(
                report,
                name,
                "city_not_in_target_list",
                detail=raw_city,
            )
        return None

    employee_count = _parse_employee_count(
        _first_value(row, CSV_FIELD_ALIASES["employee_count"])
    )
    if employee_count is None:
        if report:
            _reject(report, name, "missing_employee_count")
        return None
    if employee_count < MIN_EMPLOYEE_COUNT or employee_count > MAX_EMPLOYEE_COUNT:
        if report:
            _reject(
                report,
                name,
                "employee_count_out_of_range",
                detail=str(employee_count),
            )
        return None

    signal_score = _parse_int(_first_value(row, CSV_FIELD_ALIASES["signal_score"]))
    buyer_name = _first_value(row, CSV_FIELD_ALIASES["buyer_name"]) or None
    job_title = _first_value(row, CSV_FIELD_ALIASES["job_title"]) or None
    work_email = _first_value(row, CSV_FIELD_ALIASES["work_email"]) or None
    linkedin_url = _first_value(row, CSV_FIELD_ALIASES["linkedin_url"]) or None

    if not buyer_name:
        if report:
            _reject(report, name, "missing_buyer_name")
        return None
    if not job_title:
        if report:
            _reject(report, name, "missing_job_title")
        return None
    if not work_email:
        if report:
            _reject(report, name, "missing_work_email")
        return None

    validation = validate_lead(
        work_email=work_email,
        buyer_name=buyer_name,
        job_title=job_title,
        company_name=name,
        website=website,
        linkedin_url=linkedin_url,
        seed=hash(name) % 10_000,
    )
    if not validation["qualified"]:
        if report:
            _record_verification_rejection(report, name, validation)
        return None

    if report:
        report.verification.verified += 1

    extras = {
        "intent": _first_value(row, CSV_FIELD_ALIASES["intent"]).lower() or None,
        "signal_score": signal_score,
        "buyer_name": buyer_name,
        "job_title": job_title,
        "work_email": work_email,
        "lead_verification_status": validation["lead_verification_status"],
        "verification_status": validation["verification_status"],
        "email_status": validation["email_status"],
        "contact_verification_status": validation["contact_verification_status"],
        "email_provider": validation.get("email_provider"),
        "contact_provider": validation.get("contact_provider"),
        "website": website,
    }
    _COMPANY_CSV_EXTRAS[name] = extras

    return {
        "name": name,
        "website": website,
        "industry": industry,
        "city": city,
        "locality": city,
        "employee_count": employee_count,
        "linkedin_url": linkedin_url,
        "csv_extras": extras,
        "lead_verification_status": LEAD_STATUS_VERIFIED,
    }


def load_actual_companies(
    csv_path: Optional[Path] = None,
    *,
    report: Optional[CsvLoadReport] = None,
) -> list[dict[str, Any]]:
    """Read companies from actual_companies.csv. Invalid rows are dropped."""
    path = csv_path or ACTUAL_COMPANIES_CSV
    load_report = report or CsvLoadReport()

    print("Loading from actual_companies.csv", flush=True)
    print(f"[actual_companies] Reading CSV path: {path.resolve()}", flush=True)

    if not path.exists():
        print(f"[actual_companies] File not found: {path.resolve()}", flush=True)
        return []

    companies: list[dict[str, Any]] = []
    _COMPANY_CSV_EXTRAS.clear()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_index, row in enumerate(reader):
            if row_index < 3:
                print(
                    f"[actual_companies] Raw row {row_index + 1} (before processing): {dict(row)}",
                    flush=True,
                )
            company = map_csv_row_to_company(row, report=load_report)
            if company:
                companies.append(company)

    load_report.accepted = len(companies)
    log_verification_summary(load_report, context="actual_companies")
    if report is None:
        return companies
    return companies


def load_actual_companies_with_report(
    csv_path: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], CsvLoadReport]:
    report = CsvLoadReport()
    companies = load_actual_companies(csv_path, report=report)
    return companies, report


def get_companies() -> list[dict[str, Any]]:
    """Primary data source for ingestion (static CSV only)."""
    companies, _report = load_actual_companies_with_report()
    return companies


# Backward-compatible alias used by ingestion.py / dataset_builder.py.
COMPANIES = get_companies()
