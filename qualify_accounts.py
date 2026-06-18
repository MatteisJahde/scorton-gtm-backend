#!/usr/bin/env python3
"""
Standalone qualification script for the GTM dataset.

Usage:
    python qualify_accounts.py

Reads:  data/target_dataset_1000_companies.csv
Writes: data/top_250_qualified_accounts.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataset_builder import expand_standard_csv_row
from sorting_agent import qualification_score_with_city, sort_companies_for_final_cut

ROOT = Path(__file__).resolve().parent
INPUT_FILE = ROOT / "data" / "target_dataset_1000_companies.csv"
OUTPUT_FILE = ROOT / "data" / "top_250_qualified_accounts.csv"
TOP_N = 250


def assign_priority_tier(score: float) -> str:
    if score >= 85:
        return "Tier 1"
    if score >= 75:
        return "Tier 2"
    if score >= 65:
        return "Tier 3"
    return "Tier 4"


def assign_qualification_reason(row: pd.Series) -> str:
    ai_signal = float(row.get("ai_signal", 0) or 0)
    risk_signal = float(row.get("risk_signal", 0) or 0)
    trust_score = float(row.get("trust_opportunity_score", 0) or 0)

    if trust_score >= max(ai_signal, risk_signal):
        return "Strong trust and governance fit"
    if risk_signal >= ai_signal:
        if risk_signal >= 80:
            return "High compliance exposure"
        return "Elevated AI risk profile"
    if ai_signal >= 80:
        return "Strong AI governance opportunity"
    return "Workflow monitoring opportunity"


def main() -> None:
    input_path = INPUT_FILE.resolve()
    output_path = OUTPUT_FILE.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = pd.read_csv(input_path)
    if df.empty:
        raise ValueError("Input file is empty.")

    df = pd.DataFrame([expand_standard_csv_row(row) for row in df.to_dict(orient="records")])

    ai_signal = df.get("ai_signal")
    if ai_signal is None:
        ai_signal = df.get("company_ai_signal")
    required_columns = ("ai_signal", "risk_signal", "buying_signal")
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    if "trust_opportunity_score" not in df.columns:
        df["trust_opportunity_score"] = (
            df["ai_signal"].astype(float) + df["risk_signal"].astype(float)
        ) / 2
    if "icp_score" not in df.columns:
        df["icp_score"] = 50

    df = df.copy()
    df["qualification_score"] = df.apply(
        lambda row: qualification_score_with_city(row.to_dict()),
        axis=1,
    )

    qualified = sort_companies_for_final_cut(df.to_dict(orient="records"))[:TOP_N]
    qualified = pd.DataFrame(qualified).reset_index(drop=True)

    qualified["priority_tier"] = qualified["qualification_score"].apply(assign_priority_tier)
    qualified["qualification_reason"] = qualified.apply(assign_qualification_reason, axis=1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        qualified.to_csv(handle, index=False)
        handle.flush()
        handle.close()

    print(
        f"SUCCESS: Read {len(df)} rows from data/target_dataset_1000_companies.csv "
        f"and wrote {len(qualified)} rows to data/top_250_qualified_accounts.csv"
    )
    print(f"Total input companies: {len(df)}")
    print(f"Total qualified companies: {len(qualified)}")
    print(f"Average qualification score: {qualified['qualification_score'].mean():.2f}")


if __name__ == "__main__":
    main()
