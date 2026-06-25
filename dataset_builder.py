import csv
import io
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ingestion import (
    ALLOWED_INDUSTRIES,
    MAX_EMPLOYEE_COUNT,
    MIN_EMPLOYEE_COUNT,
    _passes_filters,
)
from models import Company, Contact, TargetAccount
from city_utils import extract_city_from_record, normalize_city_name
from deduplication import (
    company_identity_key,
    deduplicate_company_records,
)
from seed_data import get_companies
from services.enrichment import enrich_company
from services.url_utils import domain_from_website, normalize_website, website_display_status
from services.industry_filter import passes_financial_icp_filter
from services.lead_validation import LEAD_STATUS_VERIFIED
from sorting_agent import sort_companies_for_final_cut

TARGET_COUNT = 25
MAX_TARGET_ACCOUNTS = 1000
PER_CITY_LIMIT = MAX_TARGET_ACCOUNTS // 4

ORIGINAL_TARGET_CITIES = (
    "Charlotte",
    "Chicago",
    "Miami",
    "New York",
    "San Francisco",
)

PLACEHOLDER_NAME_MARKERS = ("PDL Sample Co", "Sample Co")


def is_placeholder_company(name: object) -> bool:
    value = str(name or "").strip()
    return any(marker in value for marker in PLACEHOLDER_NAME_MARKERS)

# Reference spreadsheet columns (exact order).
REFERENCE_CSV_COLUMNS = [
    "id",
    "company",
    "company_website",
    "industry",
    "city",
    "city_validated",
    "employee_count",
    "funding_status",
    "revenue",
    "funding_amount",
    "buyer_name",
    "job_title",
    "work_email",
    "email_status",
    "lead_verification_status",
    "verification_status",
    "contact_verification_status",
    "linkedin_url",
    "company_ai_signal",
    "risk_signal",
    "buying_signal",
]

TARGET_EXPORT_FIELDNAMES = REFERENCE_CSV_COLUMNS

BUYER_ROLES = {
    "Financial Services": ["CIO", "CTO", "CISO"],
    "Insurance": ["CIO", "CTO", "Head of Risk"],
    "Accounting": ["Managing Partner", "CIO", "Director of Technology"],
}

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

FIELDNAMES = [
    "Company",
    "Website",
    "Industry",
    "Employee Count",
    "Buyer Name",
    "Job Title",
    "LinkedIn",
    "AI Signal",
    "Risk Signal",
    "Trust Opportunity Score",
    "Notes",
]


def _website(company_name: str) -> str:
    slug = "".join(char.lower() for char in company_name if char.isalnum())
    return f"www.{slug}.com"


def _pick_buyer_title(industry: str, index: int) -> str:
    roles = BUYER_ROLES.get(industry, ["CIO"])
    return roles[index % len(roles)]


def _employee_bonus(employee_count: int) -> int:
    if employee_count >= 200:
        return 25
    if employee_count >= 50:
        return 15
    return 8


def _ai_signal(industry: str, employee_count: int) -> int:
    base = INDUSTRY_AI_BASE.get(industry, 40)
    return min(100, base + _employee_bonus(employee_count))


def _risk_signal(industry: str, employee_count: int) -> int:
    base = INDUSTRY_RISK_BASE.get(industry, 60)
    size_bonus = 10 if employee_count >= 200 else (5 if employee_count >= 50 else 0)
    return min(100, base + size_bonus)


def _trust_score(ai_signal: int, risk_signal: int) -> int:
    return round((ai_signal + risk_signal) / 2)


def _target_priority_tier(trust_opportunity_score: int) -> str:
    if trust_opportunity_score >= 85:
        return "Tier 1"
    if trust_opportunity_score >= 70:
        return "Tier 2"
    return "Tier 3"


def _generate_notes(industry: str, ai_signal: int, risk_signal: int) -> str:
    if industry == "Insurance":
        return "Compliance-heavy organization"
    if industry == "Accounting":
        return "Workflow automation candidate"
    if ai_signal >= 75:
        return "AI adoption likely"
    if risk_signal >= 80:
        return "Trust and risk assessment opportunity"
    if ai_signal >= 60:
        return "Potential AI governance opportunity"
    return "Trust and risk assessment opportunity"


def _buyer_lookup(db, company_name: str) -> Tuple[str, str]:
    if db is None:
        return "TBD", "TBD"

    company = db.query(Company).filter(Company.name == company_name).first()
    if not company:
        return "TBD", "TBD"

    buyer = (
        db.query(Contact)
        .filter(Contact.company_id == company.id, Contact.role_type == "Buyer")
        .first()
    )
    if not buyer:
        return "TBD", "TBD"

    return buyer.name, buyer.linkedin_url or "TBD"


def target_account_to_dict(account: TargetAccount) -> dict:
    city = extract_city_from_record(
        {
            "city": account.city,
            "locality": getattr(account, "locality", None),
        }
    )
    return {
        "id": account.id,
        "company_id": account.company_id,
        "company_name": account.company_name,
        "website": normalize_website(account.website),
        "company_website": normalize_website(account.website),
        "domain": domain_from_website(account.website),
        "website_status": website_display_status(account.website),
        "website_link": normalize_website(account.website)
        if website_display_status(account.website) == "ready"
        else "",
        "industry": account.industry,
        "city": city,
        "city_validated": account.city_validated,
        "employee_count": account.employee_count,
        "funding": account.funding,
        "revenue": account.revenue,
        "funding_amount": account.funding_amount,
        "funding_stage": account.funding_stage,
        "revenue_range": account.revenue_range,
        "buyer_name": account.buyer_name,
        "job_title": account.job_title,
        "work_email": account.work_email,
        "email_status": account.email_status,
        "lead_verification_status": account.lead_verification_status,
        "verification_status": account.verification_status,
        "contact_verification_status": account.contact_verification_status,
        "linkedin_url": account.linkedin_url,
        "company_linkedin_url": account.company_linkedin_url,
        "ai_signal": account.ai_signal,
        "company_ai_signal": account.trust_opportunity_score or account.ai_signal or 0,
        "signal_score": account.trust_opportunity_score or account.ai_signal or 0,
        "risk_signal": account.risk_signal,
        "buying_signal": account.buying_signal,
        "trust_opportunity_score": account.trust_opportunity_score,
        "icp_score": account.icp_score,
        "priority_tier": account.priority_tier,
        "notes": account.notes,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }


def format_row_for_reference_csv(row: dict, *, export_id: int) -> dict:
    """Map internal target-account fields to the reference spreadsheet schema."""
    website = normalize_website(row.get("company_website") or row.get("website") or "")

    city = extract_city_from_record(row) or ""
    city_validated = "TRUE" if city in ORIGINAL_TARGET_CITIES else "FALSE"

    def as_int(value: object) -> int:
        try:
            return int(float(value)) if value not in (None, "") else 0
        except (TypeError, ValueError):
            return 0

    return {
        "id": export_id,
        "company": str(row.get("company_name") or row.get("company") or "").strip(),
        "company_website": website,
        "industry": str(row.get("industry") or "").strip(),
        "city": city,
        "city_validated": city_validated,
        "employee_count": as_int(row.get("employee_count")),
        "funding_status": str(row.get("funding_status") or row.get("funding") or "").strip(),
        "revenue": str(row.get("revenue") or "").strip(),
        "funding_amount": str(row.get("funding_amount") or "").strip(),
        "buyer_name": str(row.get("buyer_name") or "").strip(),
        "job_title": str(row.get("job_title") or "").strip(),
        "work_email": str(row.get("work_email") or "").strip(),
        "email_status": str(row.get("email_status") or "").strip(),
        "lead_verification_status": str(row.get("lead_verification_status") or "").strip(),
        "verification_status": str(row.get("verification_status") or "").strip(),
        "contact_verification_status": str(row.get("contact_verification_status") or "").strip(),
        "linkedin_url": str(row.get("linkedin_url") or "").strip(),
        "company_ai_signal": as_int(row.get("company_ai_signal") or row.get("ai_signal")),
        "risk_signal": as_int(row.get("risk_signal")),
        "buying_signal": as_int(row.get("buying_signal")),
    }


def rows_for_reference_csv(rows: list[dict]) -> list[dict]:
    return [
        format_row_for_reference_csv(row, export_id=index)
        for index, row in enumerate(rows, start=1)
    ]


def expand_reference_csv_row(row: dict) -> dict:
    """Add internal aliases when reading reference-format CSV rows."""
    expanded = dict(row)
    expanded.setdefault("company_name", row.get("company"))
    expanded.setdefault("ai_signal", row.get("company_ai_signal"))
    score = row.get("company_ai_signal") or row.get("ai_signal") or row.get("signal_score")
    if score not in (None, ""):
        expanded.setdefault("company_ai_signal", score)
        expanded.setdefault("signal_score", score)
    website = normalize_website(row.get("company_website") or row.get("website") or "")
    if website:
        expanded.setdefault("website", website)
        expanded.setdefault("company_website", website)
        expanded.setdefault("domain", domain_from_website(website))
        expanded.setdefault("website_status", website_display_status(website))
        expanded.setdefault("website_link", website if website_display_status(website) == "ready" else "")
    expanded.setdefault("funding", row.get("funding_status"))
    expanded["city"] = extract_city_from_record(row)
    expanded["city_validated"] = row.get("city_validated") == "TRUE"
    return expanded


expand_standard_csv_row = expand_reference_csv_row


def reference_csv_from_rows(rows: list[dict]) -> str:
    export_rows = rows_for_reference_csv(rows)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=REFERENCE_CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(export_rows)
    return output.getvalue()


def _build_row(company: dict, index: int, db=None) -> dict:
    employee_count = company["employee_count"]
    industry = company["industry"]
    ai_signal = _ai_signal(industry, employee_count)
    risk_signal = _risk_signal(industry, employee_count)
    buyer_name, linkedin = _buyer_lookup(db, company["name"])

    return {
        "Company": company["name"],
        "Website": _website(company["name"]),
        "Industry": industry,
        "Employee Count": employee_count,
        "Buyer Name": buyer_name,
        "Job Title": _pick_buyer_title(industry, index),
        "LinkedIn": linkedin,
        "AI Signal": ai_signal,
        "Risk Signal": risk_signal,
        "Trust Opportunity Score": _trust_score(ai_signal, risk_signal),
        "Notes": _generate_notes(industry, ai_signal, risk_signal),
    }


def _qualifying_companies_from_db(db: Session) -> List[Company]:
    companies: List[Company] = []

    for city in ORIGINAL_TARGET_CITIES:
        rows = (
            db.query(Company)
            .filter(Company.city == city)
            .filter(Company.industry.in_(ALLOWED_INDUSTRIES))
            .filter(Company.employee_count >= MIN_EMPLOYEE_COUNT)
            .filter(Company.employee_count <= MAX_EMPLOYEE_COUNT)
            .order_by(Company.id)
            .all()
        )
        rows = sort_companies_for_final_cut(rows)
        rows = [row for row in rows if not is_placeholder_company(row.name)]
        rows = [
            row
            for row in rows
            if passes_financial_icp_filter(
                {
                    "company": row.name,
                    "industry": row.industry,
                    "website": row.website,
                }
            )[0]
        ]
        companies.extend(rows[:PER_CITY_LIMIT])

    return companies[:MAX_TARGET_ACCOUNTS]


def build_target_dataset(db: Session) -> dict:
    companies = _qualifying_companies_from_db(db)
    existing_company_ids = {
        company_id for (company_id,) in db.query(TargetAccount.company_id).all()
    }

    db.query(TargetAccount).delete()
    db.flush()

    enriched_records: List[dict] = []
    unverified_excluded = 0
    for index, company in enumerate(companies):
        enriched = enrich_company(company, index)
        accepted, _reason = passes_financial_icp_filter(enriched)
        if not accepted:
            unverified_excluded += 1
            continue
        if enriched.get("lead_verification_status") != LEAD_STATUS_VERIFIED:
            unverified_excluded += 1
            continue
        enriched_records.append(enriched)

    deduped_records, dedupe_report = deduplicate_company_records(
        enriched_records,
        score_fields=("trust_opportunity_score", "icp_score", "ai_signal"),
        label="target_accounts",
    )

    inserted = 0
    skipped = (len(companies) - len(enriched_records)) + (len(enriched_records) - len(deduped_records))
    seen_identities: set[tuple[str, str]] = set()

    for enriched in deduped_records:
        if inserted >= MAX_TARGET_ACCOUNTS:
            skipped += 1
            continue

        identity = company_identity_key(enriched)
        if identity in seen_identities:
            skipped += 1
            continue
        seen_identities.add(identity)

        db.add(TargetAccount(**enriched, created_at=datetime.utcnow()))
        inserted += 1

    db.commit()
    verified_in_dataset = inserted
    return {
        "inserted": inserted,
        "skipped": skipped,
        "previous_count": len(existing_company_ids),
        "total": inserted,
        "enriched": inserted,
        "verification": {
            "verified_in_dataset": verified_in_dataset,
            "unverified_excluded": unverified_excluded,
        },
        "deduplication": {
            "input_companies": dedupe_report.input_count,
            "duplicates_removed": dedupe_report.duplicates_removed,
            "final_unique_companies": dedupe_report.final_count,
        },
    }


def enrich_target_dataset(db: Session) -> dict:
    """Re-run enrichment on all existing target accounts (up to 1000)."""
    accounts = (
        db.query(TargetAccount)
        .order_by(TargetAccount.id)
        .limit(MAX_TARGET_ACCOUNTS)
        .all()
    )
    enriched_count = 0

    for index, account in enumerate(accounts):
        company = db.query(Company).filter(Company.id == account.company_id).first()
        if not company:
            continue

        data = enrich_company(company, index)
        for field, value in data.items():
            if field != "company_id":
                setattr(account, field, value)
        enriched_count += 1

    db.commit()
    return {"enriched": enriched_count, "total": enriched_count}


def build_dataset(db=None) -> List[dict]:
    filtered = [company for company in get_companies() if _passes_filters(company)]
    selected = filtered[:TARGET_COUNT]

    return [_build_row(company, index, db) for index, company in enumerate(selected)]


def export_dataset_csv(rows: Optional[List[dict]] = None) -> str:
    dataset = rows if rows is not None else build_dataset()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(dataset)
    return output.getvalue()


MASTER_DATASET_PATH = (
    "/Users/matteisjahde/Projects/scorton-gtm-os/data/target_dataset_with_personas.csv"
)


def save_master_dataset(db: Session, path: str = MASTER_DATASET_PATH) -> dict:
    """Build target dataset and write the master CSV export."""
    result = build_target_dataset(db)
    csv_content = export_target_dataset_csv(db)
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(csv_content, encoding="utf-8")
    row_count = csv_content.count("\n")
    return {
        **result,
        "path": str(output_path.resolve()),
        "csv_lines": row_count,
    }


def deduplicate_target_dataset_csv(csv_path: str) -> int:
    """Drop duplicate companies before writing CSV export."""
    import pandas as pd

    path = Path(csv_path)
    df = pd.read_csv(path)
    records = df.to_dict(orient="records")
    deduped, report = deduplicate_company_records(records, label="csv_export")
    export_rows = rows_for_reference_csv(deduped)
    pd.DataFrame(export_rows, columns=REFERENCE_CSV_COLUMNS).to_csv(path, index=False)
    return report.duplicates_removed


def deduplicate_target_dataset_csv_content(csv_content: str) -> str:
    """Drop duplicate companies in exported CSV content before writing."""
    import io

    import pandas as pd

    df = pd.read_csv(io.StringIO(csv_content))
    deduped, _report = deduplicate_company_records(
        df.to_dict(orient="records"),
        label="csv_export",
    )
    return reference_csv_from_rows(deduped)


def _exportable_target_rows(db: Session) -> list[dict]:
    rows = db.query(TargetAccount).order_by(TargetAccount.id).all()
    internal_rows = [
        target_account_to_dict(account)
        for account in rows
        if account.city in ORIGINAL_TARGET_CITIES
        and not is_placeholder_company(account.company_name)
    ]
    deduped, _report = deduplicate_company_records(
        internal_rows,
        score_fields=("trust_opportunity_score", "icp_score", "ai_signal"),
        label="target_accounts_export",
    )
    return deduped


def export_target_dataset_csv(db: Session) -> str:
    return reference_csv_from_rows(_exportable_target_rows(db))


def export_target_dataset_xlsx(db: Session) -> bytes:
    from openpyxl import Workbook

    export_rows = rows_for_reference_csv(_exportable_target_rows(db))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Target Accounts"
    sheet.append(REFERENCE_CSV_COLUMNS)

    for row in export_rows:
        sheet.append([row[field] for field in REFERENCE_CSV_COLUMNS])

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
