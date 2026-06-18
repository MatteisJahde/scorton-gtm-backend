#!/usr/bin/env python3
"""Final cleanup: normalize websites, dedupe, and rank by lead_score."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from dataset_builder import (
    ORIGINAL_TARGET_CITIES,
    REFERENCE_CSV_COLUMNS,
    is_placeholder_company,
    rows_for_reference_csv,
)
from deduplication import deduplicate_company_records
from sorting_agent import qualification_score_with_city

DATASET_PATH = Path(__file__).resolve().parent / "data" / "target_dataset_1000_companies.csv"


def normalize_website(url: object) -> str:
    """Normalize website URLs for consistent deduplication."""
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


def _expand_reference_row(row: dict) -> dict:
    expanded = dict(row)
    expanded.setdefault("company_name", row.get("company"))
    expanded.setdefault("ai_signal", row.get("company_ai_signal"))
    expanded.setdefault("funding", row.get("funding_status"))
    return expanded


def main() -> None:
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")

    raw_df = pd.read_csv(DATASET_PATH)
    working_rows = [_expand_reference_row(row) for row in raw_df.to_dict(orient="records")]
    df = pd.DataFrame(working_rows)

    if "company_website" not in df.columns:
        raise KeyError("Missing required column: company_website")

    if "lead_score" not in df.columns:
        df["lead_score"] = df.apply(
            lambda row: qualification_score_with_city(row.to_dict()),
            axis=1,
        )

    df["company_website"] = df["company_website"].map(normalize_website)
    df = df[df["city"].isin(ORIGINAL_TARGET_CITIES)]
    if "company" in df.columns:
        df = df[~df["company"].map(is_placeholder_company)]
    elif "company_name" in df.columns:
        df = df[~df["company_name"].map(is_placeholder_company)]

    deduped_records, dedupe_report = deduplicate_company_records(
        df.to_dict(orient="records"),
        score_fields=("lead_score", "company_ai_signal", "ai_signal"),
        label="final_cleanup",
    )
    df = pd.DataFrame(deduped_records)
    df = df.sort_values("lead_score", ascending=False)

    export_rows = rows_for_reference_csv(df.to_dict(orient="records"))
    export_df = pd.DataFrame(export_rows, columns=REFERENCE_CSV_COLUMNS)

    output_path = str(DATASET_PATH.resolve())
    print(f"WRITING CSV NOW: {output_path} ({len(export_df)} rows)")
    export_df.to_csv(output_path, index=False)
    print(f"CSV WRITE COMPLETE: {output_path}")

    if os.path.exists(output_path):
        print("FILE SUCCESSFULLY WRITTEN TO DISK")
    else:
        print("CRITICAL ERROR: FILE WAS NOT SAVED")

    assert list(export_df.columns) == REFERENCE_CSV_COLUMNS
    unique_companies = export_df["company_website"].nunique()
    print(
        f"SUCCESS: Dataset now has {unique_companies} unique companies "
        f"with {len(REFERENCE_CSV_COLUMNS)} reference columns."
    )
    print(f"Loaded: {dedupe_report.input_count}")
    print(f"Duplicates removed: {dedupe_report.duplicates_removed}")
    print(f"Final unique companies: {dedupe_report.final_count}")
    print("City counts:")
    print(export_df["city"].value_counts().sort_index().to_string())


if __name__ == "__main__":
    main()
