#!/usr/bin/env python3
"""Build a fresh, deduplicated, score-ranked dataset from the database."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from database import SessionLocal
from dataset_builder import (
    ORIGINAL_TARGET_CITIES,
    REFERENCE_CSV_COLUMNS,
    is_placeholder_company,
    rows_for_reference_csv,
    target_account_to_dict,
)
from migrations import migrate_db
from models import TargetAccount
from sorting_agent import qualification_score_with_city

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "target_dataset_1000_companies.csv"


def clear_data_csv_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for csv_path in DATA_DIR.glob("*.csv"):
        csv_path.unlink()
        print(f"cleared: {csv_path.name}")


def normalize_website(url: object) -> str:
    if url is None or pd.isna(url):
        return ""

    value = str(url).strip().lower()
    if value.startswith("https://"):
        value = value[len("https://") :]
    elif value.startswith("http://"):
        value = value[len("http://") :]
    if value.startswith("www."):
        value = value[len("www.") :]
    return value.rstrip("/")


def build_clean_dataset() -> pd.DataFrame:
    clear_data_csv_files()
    migrate_db()

    db = SessionLocal()
    try:
        target_rows = (
            db.query(TargetAccount)
            .filter(TargetAccount.city.in_(ORIGINAL_TARGET_CITIES))
            .order_by(TargetAccount.id)
            .all()
        )
        records = [
            target_account_to_dict(account)
            for account in target_rows
            if not is_placeholder_company(account.company_name)
        ]
    finally:
        db.close()

    if not records:
        raise RuntimeError("No target account records found for original target cities.")

    df = pd.DataFrame(records)
    if "company_website" not in df.columns:
        df["company_website"] = df.get("website", "")

    df["lead_score"] = df.apply(
        lambda row: qualification_score_with_city(row.to_dict()),
        axis=1,
    )
    df["company_website"] = df["company_website"].map(normalize_website)
    df = df.drop_duplicates(subset=["company_website"], keep="first")
    df = df.sort_values("lead_score", ascending=False)

    export_rows = rows_for_reference_csv(df.to_dict(orient="records"))
    export_df = pd.DataFrame(export_rows, columns=REFERENCE_CSV_COLUMNS)
    export_df.to_csv(OUTPUT_PATH, index=False)
    print(f"saved: {OUTPUT_PATH.resolve()}")
    return export_df


def main() -> None:
    df = build_clean_dataset()

    unique_count = df["company_website"].nunique()
    top_score = df["company_ai_signal"].max()
    bottom_score = df["company_ai_signal"].min()

    assert list(df.columns) == REFERENCE_CSV_COLUMNS
    assert set(df["city"].unique()).issubset(set(ORIGINAL_TARGET_CITIES))
    assert df["company_website"].nunique() == len(df)

    print(
        f"FINAL COUNT: {unique_count} unique companies. "
        f"TOP SCORE: {top_score}. BOTTOM SCORE: {bottom_score}."
    )


if __name__ == "__main__":
    main()
