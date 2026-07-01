"""Website reachability filtering during ingest and periodic dashboard maintenance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import Company, TargetAccount
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


def reverify_and_prune_database_leads(db: Session) -> dict[str, Any]:
    """
    Re-check websites for dashboard leads and remove unreachable target accounts.

    Called during CSV refresh / startup so dead sites disappear on the next rebuild.
    """
    rows = (
        db.query(TargetAccount, Company)
        .join(Company, TargetAccount.company_id == Company.id)
        .order_by(TargetAccount.id)
        .all()
    )
    if not rows:
        return {"checked": 0, "kept": 0, "removed": 0, "removed_accounts": []}

    kept = 0
    removed_accounts: list[dict[str, Any]] = []
    checked_at = datetime.now(timezone.utc)

    for account, company in rows:
        website = normalize_website(account.website or company.website or "")
        is_reachable, detail, status_code = check_website_head_status_200(website)
        company.website_reachable = is_reachable
        company.website_http_status = status_code
        company.website_checked_at = checked_at

        if is_reachable:
            kept += 1
            print(
                f"[website-maint] kept {account.company_name} ({website}) -> {detail}",
                flush=True,
            )
            continue

        removed_accounts.append(
            {
                "company": account.company_name,
                "website": website,
                "detail": detail,
                "http_status": status_code,
            }
        )
        db.delete(account)
        print(
            f"[website-maint] removed {account.company_name} ({website}) -> {detail}",
            flush=True,
        )

    db.commit()
    return {
        "checked": len(rows),
        "kept": kept,
        "removed": len(removed_accounts),
        "removed_accounts": removed_accounts,
    }
