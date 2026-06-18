#!/usr/bin/env python3
"""
Build the 1,100-company master dataset with Apollo AI Cybersecurity payload.

Writes a slim 6-column CSV for downstream scoring agents.
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.personas import DEFAULT_EXECUTIVE_TITLES  # noqa: E402
from database import SessionLocal  # noqa: E402
from dataset_builder import (  # noqa: E402
    MASTER_DATASET_PATH,
    MAX_TARGET_ACCOUNTS,
    build_target_dataset,
)
from migrations import migrate_db  # noqa: E402
from models import TargetAccount  # noqa: E402

# Apollo API request payload — AI Cybersecurity focus
APOLLO_API_PAYLOAD = {
    "person_titles": DEFAULT_EXECUTIVE_TITLES,
    "title_keywords": DEFAULT_EXECUTIVE_TITLES,
    "enrichment_parameters": {
        "funding_summary": True,
        "funding": True,
        "annual_revenue": True,
        "revenue": True,
    },
    "limit": MAX_TARGET_ACCOUNTS,
}

OUTPUT_COLUMNS = [
    "company_name",
    "funding",
    "revenue",
    "job_title",
    "work_email",
    "linkedin_url",
]


def build_and_save_master_csv(db, output_path: str = MASTER_DATASET_PATH) -> dict:
    """Run enrichment pipeline and export the slim master CSV."""
    build_result = build_target_dataset(db)

    accounts = (
        db.query(TargetAccount)
        .order_by(TargetAccount.id)
        .limit(APOLLO_API_PAYLOAD["limit"])
        .all()
    )

    rows = [
        {
            "company_name": account.company_name,
            "funding": account.funding or "",
            "revenue": account.revenue or "",
            "job_title": account.job_title or "",
            "work_email": account.work_email or "",
            "linkedin_url": account.linkedin_url or "",
        }
        for account in accounts
    ]

    df = pd.DataFrame(rows)
    df.columns = OUTPUT_COLUMNS

    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False)

    return {
        **build_result,
        "apollo_payload": APOLLO_API_PAYLOAD,
        "path": str(output.resolve()),
        "rows_written": len(df),
        "columns": list(df.columns),
    }


def main() -> int:
    migrate_db()
    db = SessionLocal()
    try:
        result = build_and_save_master_csv(db)
        print(result)
        if result.get("rows_written") != MAX_TARGET_ACCOUNTS:
            print(
                f"Warning: expected {MAX_TARGET_ACCOUNTS} rows, got {result.get('rows_written')}",
                file=sys.stderr,
            )
            return 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
