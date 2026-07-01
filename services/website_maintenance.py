"""Website reachability filtering during ingest and periodic dashboard maintenance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import Company, Contact, TargetAccount
from services.domain_verification import check_website_head_status_200
from services.url_utils import normalize_website
from settings import WEBSITE_VERIFY_MAX_WORKERS


def _company_website(company: dict[str, Any]) -> str:
    extras = company.get("csv_extras") or {}
    return normalize_website(
        company.get("website") or extras.get("website") or ""
    )


def _check_company_row(company: dict[str, Any]) -> tuple[dict[str, Any], bool, str, Optional[int]]:
    website = _company_website(company)
    is_reachable, detail, status_code = check_website_head_status_200(website)
    return company, is_reachable, detail, status_code


def partition_companies_by_website(
    companies: list[dict[str, Any]],
    *,
    max_workers: int = WEBSITE_VERIFY_MAX_WORKERS,
) -> dict[str, Any]:
    """HEAD-check every company website and split reachable vs invalid records."""
    if not companies:
        return {
            "reachable": [],
            "unreachable": [],
            "summary": {"checked": 0, "reachable": 0, "unreachable": 0},
        }

    reachable: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    workers = max(1, min(max_workers, len(companies)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_check_company_row, company) for company in companies]
        for future in as_completed(futures):
            company, is_reachable, detail, status_code = future.result()
            company_name = str(company.get("name") or "").strip()
            website = _company_website(company)
            company["website_reachable"] = is_reachable
            company["website_http_status"] = status_code
            company["website_check_detail"] = detail
            company["website_checked_at"] = datetime.now(timezone.utc).isoformat()

            if is_reachable:
                reachable.append(company)
                print(
                    f"[website] {company_name} ({website}) -> VALID ({detail})",
                    flush=True,
                )
            else:
                unreachable.append(
                    {
                        "company": company_name,
                        "website": website,
                        "validation_status": "UNREACHABLE",
                        "validation_detail": detail,
                        "http_status": status_code,
                    }
                )
                print(
                    f"[website] {company_name} ({website}) -> INVALID ({detail})",
                    flush=True,
                )

    reachable.sort(key=lambda row: str(row.get("name") or "").lower())
    unreachable.sort(key=lambda row: str(row.get("company") or "").lower())

    return {
        "reachable": reachable,
        "unreachable": unreachable,
        "summary": {
            "checked": len(companies),
            "reachable": len(reachable),
            "unreachable": len(unreachable),
        },
    }


def _delete_company_and_related_rows(db: Session, company: Company) -> None:
    """Remove dashboard rows and contacts before deleting the company record."""
    db.query(TargetAccount).filter(TargetAccount.company_id == company.id).delete(
        synchronize_session=False
    )
    db.query(Contact).filter(Contact.company_id == company.id).delete(
        synchronize_session=False
    )
    db.delete(company)


def purge_unreachable_companies_from_database(db: Session) -> dict[str, Any]:
    """
    One-off style cleanup: HEAD-check every company row and delete unreachable records.

    Unreachable means invalid URL format, non-200 HTTP status, timeout, or connection error.
    Deletes matching TargetAccount, Contact, and Company rows so the dashboard stays clean.
    """
    companies = db.query(Company).order_by(Company.id).all()
    if not companies:
        return {
            "checked": 0,
            "kept": 0,
            "removed": 0,
            "kept_companies": [],
            "removed_companies": [],
        }

    checked_at = datetime.now(timezone.utc)
    kept_companies: list[dict[str, Any]] = []
    removed_companies: list[dict[str, Any]] = []

    for company in companies:
        website = normalize_website(company.website or "")
        is_reachable, detail, status_code = check_website_head_status_200(website)
        company.website_reachable = is_reachable
        company.website_http_status = status_code
        company.website_checked_at = checked_at

        record = {
            "company_id": company.id,
            "company": company.name,
            "website": website,
            "detail": detail,
            "http_status": status_code,
            "website_reachable": is_reachable,
        }

        if is_reachable:
            kept_companies.append(record)
            print(
                f"[website-purge] kept {company.name} ({website}) -> {detail}",
                flush=True,
            )
            continue

        removed_companies.append(record)
        _delete_company_and_related_rows(db, company)
        print(
            f"[website-purge] deleted {company.name} ({website}) -> {detail}",
            flush=True,
        )

    db.commit()
    return {
        "checked": len(companies),
        "kept": len(kept_companies),
        "removed": len(removed_companies),
        "kept_companies": kept_companies,
        "removed_companies": removed_companies,
    }


def reverify_and_prune_database_leads(db: Session) -> dict[str, Any]:
    """Backward-compatible alias for full company purge + website re-check."""
    return purge_unreachable_companies_from_database(db)
