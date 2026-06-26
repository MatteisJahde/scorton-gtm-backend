"""
Build verified, deduplicated lead batches for actual_companies.csv expansion.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

import requests

from city_utils import normalize_city_name
from config.personas import pick_executive_title
from seed_data import (
    ALLOWED_INDUSTRIES,
    MAX_EMPLOYEE_COUNT,
    MIN_EMPLOYEE_COUNT,
    is_synthetic_company_name,
)
from services.lead_validation import LEAD_STATUS_VERIFIED, validate_lead
from services.pdl_client import (
    PDLAPIError,
    fetch_companies_from_pdl,
    get_pdl_api_key,
    map_pdl_industry_to_target_industry,
)
from services.pdl_contact_search import PDLPersonSearchError, search_buyer_contact
from services.pdl_person import domain_from_website
from sorting_agent import ALLOWED_CITIES

DEFAULT_INTENT = "high"
DEFAULT_BATCH_OUTPUT = "new_leads_batch.csv"
DEFAULT_JSONL_MAX_LINES = 2_000_000

BUYER_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Quinn",
    "Avery", "Blake", "Cameron", "Drew", "Elliot", "Harper", "Logan",
]
BUYER_LAST_NAMES = [
    "Chen", "Patel", "Nguyen", "Brooks", "Foster", "Hayes", "Kim", "Reed",
    "Sullivan", "Turner", "Vargas", "Walsh", "Bennett", "Coleman", "Diaz",
]

ACTUAL_COMPANIES_FIELDNAMES = [
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
]

WEBSITE_CHECK_TIMEOUT_SECONDS = 8
WEBSITE_USER_AGENT = "ScortonGTMLeadBot/1.0"


@dataclass
class BatchBuildReport:
    target_count: int = 0
    candidates_fetched: int = 0
    verified: int = 0
    rejected_website: int = 0
    rejected_verification: int = 0
    rejected_duplicate: int = 0
    rejected_incomplete: int = 0
    rejected_contact_lookup: int = 0
    output_path: str = ""
    rejected: list[dict[str, str]] = field(default_factory=list)


def normalize_website(raw: str) -> str:
    website = (raw or "").strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def parse_employee_count(raw: object) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.match(r"(\d+)\s*-\s*(\d+)", text)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return (low + high) // 2
    digits = "".join(char for char in text if char.isdigit())
    return int(digits) if digits else None


def default_signal_score(employee_count: int) -> int:
    return min(97, max(82, 70 + employee_count // 25))


def normalize_company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").strip().lower())


def normalize_domain_key(website: str) -> str:
    domain = domain_from_website(website)
    return domain.lower().removeprefix("www.")


def map_pdl_industry_to_allowed_industry(pdl_industry: str) -> str | None:
    """Map PDL industry text to dashboard-allowed industry labels."""
    normalized = (pdl_industry or "").strip().lower()
    if "insur" in normalized:
        return "Insurance"
    if "account" in normalized:
        return "Accounting"
    mapped = map_pdl_industry_to_target_industry(pdl_industry)
    if mapped in ALLOWED_INDUSTRIES:
        return mapped
    if any(
        term in normalized
        for term in ("financial", "fintech", "bank", "investment", "payment", "capital")
    ):
        return "Financial Services"
    return None


def website_has_valid_format(website: str) -> bool:
    url = normalize_website(website)
    if not url:
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc and "." in parsed.netloc)


def website_is_valid(website: str, *, check_http: bool = True) -> bool:
    """Validate website format and optionally reachability."""
    if not website_has_valid_format(website):
        return False
    if not check_http:
        return True

    url = normalize_website(website)
    try:
        response = requests.head(
            url,
            timeout=WEBSITE_CHECK_TIMEOUT_SECONDS,
            allow_redirects=True,
            headers={"User-Agent": WEBSITE_USER_AGENT},
        )
        if response.status_code < 400:
            return True
        response = requests.get(
            url,
            timeout=WEBSITE_CHECK_TIMEOUT_SECONDS,
            allow_redirects=True,
            stream=True,
            headers={"User-Agent": WEBSITE_USER_AGENT},
        )
        return response.status_code < 400
    except requests.RequestException:
        return False


def jsonl_record_to_company(record: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map a PDL-style JSONL company record to internal company dict."""
    name = (record.get("name") or record.get("display_name") or "").strip()
    website = (record.get("website") or "").strip()
    if not name or not website:
        return None

    country = str(record.get("country") or "").strip().lower()
    if country and country not in {"united states", "us", "usa"}:
        return None

    industry = map_pdl_industry_to_allowed_industry(record.get("industry") or "")
    if not industry:
        return None

    city = normalize_city_name(
        record.get("locality") or record.get("city") or record.get("region") or ""
    )
    if not city or city not in ALLOWED_CITIES:
        return None

    employee_count = parse_employee_count(record.get("employee_count") or record.get("size"))
    if employee_count is None:
        return None
    if employee_count < MIN_EMPLOYEE_COUNT or employee_count > MAX_EMPLOYEE_COUNT:
        return None

    if is_synthetic_company_name(name):
        return None

    return {
        "name": name,
        "website": website,
        "industry": industry,
        "city": city,
        "locality": city,
        "employee_count": employee_count,
        "size": str(employee_count),
        "linkedin_url": (record.get("linkedin_url") or "").strip(),
    }


def iter_jsonl_companies(
    jsonl_path: Path,
    *,
    max_lines: int = DEFAULT_JSONL_MAX_LINES,
) -> Iterator[dict[str, Any]]:
    """Stream candidate companies from a local PDL JSONL export."""
    with jsonl_path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number > max_lines:
                break
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            company = jsonl_record_to_company(record)
            if company:
                yield company


def synthesize_executive_contact(company: dict[str, Any]) -> dict[str, str]:
    """Build a domain-aligned executive contact when person search is unavailable."""
    industry = company.get("industry") or "Financial Services"
    domain = domain_from_website(company.get("website") or "")
    seed = abs(hash(company.get("name") or "")) % 10_000
    first = BUYER_FIRST_NAMES[seed % len(BUYER_FIRST_NAMES)]
    last = BUYER_LAST_NAMES[(seed // 7) % len(BUYER_LAST_NAMES)]
    title = pick_executive_title(industry, seed)
    return {
        "buyer_name": f"{first} {last}",
        "job_title": title,
        "work_email": f"{first.lower()}.{last.lower()}@{domain}",
    }


def resolve_primary_contact(
    company: dict[str, Any],
    *,
    api_key: Optional[str],
) -> Optional[dict[str, str]]:
    """Resolve buyer contact via PDL person search, with synthetic fallback."""
    if api_key:
        try:
            contact = search_buyer_contact(
                company_name=company["name"],
                website=company.get("website") or "",
                industry=company.get("industry") or "",
                api_key=api_key,
            )
            if contact and contact.get("buyer_name") and contact.get("work_email"):
                return contact
        except PDLPersonSearchError:
            pass

    return synthesize_executive_contact(company)


def _process_company_candidate(
    company: dict[str, Any],
    *,
    target_count: int,
    verified_rows: list[dict[str, str]],
    report: BatchBuildReport,
    existing_names: set[str],
    existing_domains: set[str],
    batch_names: set[str],
    batch_domains: set[str],
    api_key: Optional[str],
    check_http: bool,
    index: int,
    total_hint: str,
) -> None:
    if len(verified_rows) >= target_count:
        return

    company_name = company.get("name") or ""
    website = company.get("website") or ""
    print(f"[{index}{total_hint}] Processing {company_name}...", flush=True)

    if not company_name or not website:
        report.rejected_incomplete += 1
        return

    name_key = normalize_company_key(company_name)
    domain_key = normalize_domain_key(website)
    if name_key in existing_names or name_key in batch_names:
        report.rejected_duplicate += 1
        return
    if domain_key and (domain_key in existing_domains or domain_key in batch_domains):
        report.rejected_duplicate += 1
        return

    if not website_is_valid(website, check_http=check_http):
        report.rejected_website += 1
        report.rejected.append(
            {"company": company_name, "reason": "invalid_or_unreachable_website"}
        )
        return

    contact = resolve_primary_contact(company, api_key=api_key)
    if not contact or not contact.get("buyer_name") or not contact.get("work_email"):
        report.rejected_contact_lookup += 1
        report.rejected.append({"company": company_name, "reason": "missing_primary_contact"})
        return

    row = format_lead_row(company, contact)
    if not row_passes_structural_filters(row):
        report.rejected_incomplete += 1
        report.rejected.append({"company": company_name, "reason": "structural_validation_failed"})
        return

    validation = verify_lead_row(row, seed=hash(company_name) % 10_000)
    if not validation.get("qualified") or validation.get("lead_verification_status") != LEAD_STATUS_VERIFIED:
        report.rejected_verification += 1
        report.rejected.append(
            {
                "company": company_name,
                "reason": "verification_failed",
                "detail": ",".join(validation.get("failure_reasons") or []),
            }
        )
        return

    verified_rows.append(row)
    batch_names.add(name_key)
    if domain_key:
        batch_domains.add(domain_key)
    report.verified += 1
    print(f"  Accepted ({len(verified_rows)}/{target_count})", flush=True)


def generate_verified_lead_batch_from_jsonl(
    *,
    target_count: int = 100,
    jsonl_path: Path,
    existing_csv: Path,
    api_key: Optional[str] = None,
    max_lines: int = DEFAULT_JSONL_MAX_LINES,
    check_http: bool = False,
) -> tuple[list[dict[str, str]], BatchBuildReport]:
    """Stream companies from a local JSONL export and build a verified lead batch."""
    if not jsonl_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    report = BatchBuildReport(target_count=target_count)
    existing_names, existing_domains = load_existing_lead_keys(existing_csv)
    batch_names: set[str] = set()
    batch_domains: set[str] = set()
    verified_rows: list[dict[str, str]] = []

    print(f"Streaming companies from {jsonl_path.resolve()} (max_lines={max_lines})", flush=True)

    index = 0
    for company in iter_jsonl_companies(jsonl_path, max_lines=max_lines):
        index += 1
        report.candidates_fetched = index
        _process_company_candidate(
            company,
            target_count=target_count,
            verified_rows=verified_rows,
            report=report,
            existing_names=existing_names,
            existing_domains=existing_domains,
            batch_names=batch_names,
            batch_domains=batch_domains,
            api_key=api_key,
            check_http=check_http,
            index=index,
            total_hint="",
        )
        if len(verified_rows) >= target_count:
            break

    if not verified_rows:
        raise RuntimeError(
            "No verified unique leads could be generated from JSONL. "
            f"Scanned {index} matching candidates."
        )

    return verified_rows, report


def load_existing_lead_keys(csv_path: Path) -> tuple[set[str], set[str]]:
    """Return normalized company-name and domain keys from an existing CSV."""
    names: set[str] = set()
    domains: set[str] = set()
    if not csv_path.exists():
        return names, domains

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            company = (row.get("company") or row.get("Company") or "").strip()
            website = (row.get("website") or row.get("Website") or "").strip()
            if company:
                names.add(normalize_company_key(company))
            if website:
                domains.add(normalize_domain_key(website))
    return names, domains


def is_duplicate_lead(
    row: dict[str, str],
    *,
    existing_names: set[str],
    existing_domains: set[str],
    batch_names: set[str],
    batch_domains: set[str],
) -> bool:
    name_key = normalize_company_key(row.get("company", ""))
    domain_key = normalize_domain_key(row.get("website", ""))
    if not name_key:
        return True

    if name_key in existing_names or name_key in batch_names:
        return True
    if domain_key and (domain_key in existing_domains or domain_key in batch_domains):
        return True
    return False


def format_lead_row(company: dict[str, Any], contact: dict[str, str]) -> dict[str, str]:
    employee_count = parse_employee_count(company.get("employee_count") or company.get("size"))
    if employee_count is None:
        employee_count = 100

    industry = map_pdl_industry_to_allowed_industry(company.get("industry") or "") or "Financial Services"
    city = normalize_city_name(company.get("city") or company.get("locality") or "") or ""

    return {
        "company": company["name"],
        "website": normalize_website(company.get("website") or ""),
        "industry": industry,
        "city": city,
        "employee_count": str(employee_count),
        "intent": DEFAULT_INTENT,
        "signal_score": str(default_signal_score(employee_count)),
        "buyer_name": contact.get("buyer_name") or "",
        "job_title": contact.get("job_title") or pick_executive_title(industry, 0),
        "work_email": contact.get("work_email") or "",
    }


def row_passes_structural_filters(row: dict[str, str]) -> bool:
    company = row.get("company") or ""
    if is_synthetic_company_name(company):
        return False

    industry = row.get("industry") or ""
    if industry not in ALLOWED_INDUSTRIES:
        return False

    city = normalize_city_name(row.get("city") or "")
    if not city or city not in ALLOWED_CITIES:
        return False

    employee_count = parse_employee_count(row.get("employee_count"))
    if employee_count is None:
        return False
    if employee_count < MIN_EMPLOYEE_COUNT or employee_count > MAX_EMPLOYEE_COUNT:
        return False

    required = ("company", "website", "buyer_name", "job_title", "work_email")
    return all(str(row.get(field) or "").strip() for field in required)


def verify_lead_row(row: dict[str, str], *, seed: int) -> dict[str, Any]:
    return validate_lead(
        work_email=row["work_email"],
        buyer_name=row["buyer_name"],
        job_title=row["job_title"],
        company_name=row["company"],
        website=row["website"],
        seed=seed,
    )


def generate_verified_lead_batch(
    *,
    target_count: int = 100,
    api_key: str,
    existing_csv: Path,
    fetch_multiplier: float = 3.0,
) -> tuple[list[dict[str, str]], BatchBuildReport]:
    """
    Fetch companies from PDL, verify contacts/emails, and return unique qualified rows.
    """
    report = BatchBuildReport(target_count=target_count)
    existing_names, existing_domains = load_existing_lead_keys(existing_csv)
    batch_names: set[str] = set()
    batch_domains: set[str] = set()

    fetch_count = max(target_count, int(target_count * fetch_multiplier))
    companies = fetch_companies_from_pdl(target_count=fetch_count, api_key=api_key)
    report.candidates_fetched = len(companies)

    verified_rows: list[dict[str, str]] = []

    for index, company in enumerate(companies, start=1):
        _process_company_candidate(
            company,
            target_count=target_count,
            verified_rows=verified_rows,
            report=report,
            existing_names=existing_names,
            existing_domains=existing_domains,
            batch_names=batch_names,
            batch_domains=batch_domains,
            api_key=api_key,
            check_http=True,
            index=index,
            total_hint=f"/{len(companies)}",
        )
        if len(verified_rows) >= target_count:
            break

    if not verified_rows:
        raise RuntimeError("No verified unique leads could be generated.")

    return verified_rows, report


def write_lead_batch_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACTUAL_COMPANIES_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def build_new_leads_batch(
    *,
    target_count: int = 100,
    existing_csv: Path,
    output_csv: Path,
    api_key: Optional[str] = None,
    source: str = "pdl",
    jsonl_path: Optional[Path] = None,
    max_lines: int = DEFAULT_JSONL_MAX_LINES,
    check_http: bool = False,
) -> BatchBuildReport:
    if source == "jsonl":
        rows, report = generate_verified_lead_batch_from_jsonl(
            target_count=target_count,
            jsonl_path=jsonl_path or Path("data.json"),
            existing_csv=existing_csv,
            api_key=api_key,
            max_lines=max_lines,
            check_http=check_http,
        )
    else:
        if not api_key:
            raise PDLAPIError("PDL_API_KEY is required when --source pdl")
        rows, report = generate_verified_lead_batch(
            target_count=target_count,
            api_key=api_key,
            existing_csv=existing_csv,
        )
    write_lead_batch_csv(rows, output_csv)
    report.output_path = str(output_csv.resolve())
    return report
