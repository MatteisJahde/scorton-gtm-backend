#!/usr/bin/env python3
"""
Enrich contact fields in scorton_final_ranked_leads.csv via Hunter.io / PDL.

Usage:
  python scripts/enrich_ranked_leads_contacts.py \\
    --input ~/Desktop/scorton_final_ranked_leads.csv \\
    --output ~/Desktop/scorton_final_ranked_leads_enriched.csv

Environment:
  HUNTER_API_KEY or HUNTER_IO_API_KEY
  PDL_API_KEY
  CONTACT_ENRICHMENT_PROVIDER=auto|hunter|pdl
  CONTACT_ENRICHMENT_DROP_IF_MISSING=true|false
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.contact_enrichment import enrich_contact_for_company, should_drop_lead_without_contact
from services.contact_fields import attach_contact_aliases
from services.url_utils import domain_from_website, normalize_website


def _first(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def enrich_row(row: dict) -> dict | None:
    company_name = _first(row, "company", "company_name", "Company")
    website = normalize_website(_first(row, "website", "company_website", "Website"))
    domain = _first(row, "domain") or domain_from_website(website)
    industry = _first(row, "industry", "Industry") or "Financial Services"

    contact = enrich_contact_for_company(
        company_name=company_name,
        website=website,
        domain=domain,
        industry=industry,
        buyer_name=_first(row, "buyer_name", "contact_name", "Reach Out To"),
        job_title=_first(row, "job_title", "contact_role", "Title"),
        work_email=_first(row, "work_email", "verified_email", "Email"),
        lead_verification_status=row.get("lead_verification_status"),
    )
    if contact is None:
        return None

    merged = {**row, **contact}
    return attach_contact_aliases(merged)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich ranked leads CSV contacts")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path.home() / "Desktop" / "scorton_final_ranked_leads.csv",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="Process only N rows (0 = all)")
    args = parser.parse_args()

    input_path = args.input.expanduser()
    output_path = (args.output or input_path.with_stem(input_path.stem + "_enriched")).expanduser()

    with input_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        for extra in (
            "contact_name",
            "contact_role",
            "verified_email",
            "contact_status",
            "enrichment_provider",
            "contact_verification_status",
        ):
            if extra not in fieldnames:
                fieldnames.append(extra)

        enriched_rows: list[dict] = []
        dropped = 0
        no_contact = 0
        verified = 0

        for index, row in enumerate(reader):
            if args.limit and index >= args.limit:
                enriched_rows.append(row)
                continue

            result = enrich_row(row)
            if result is None:
                dropped += 1
                continue
            if result.get("contact_status") == "no_contact_found":
                no_contact += 1
            elif result.get("contact_status") == "verified":
                verified += 1
            enriched_rows.append(result)

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(enriched_rows)

    print(
        {
            "input": str(input_path),
            "output": str(output_path),
            "rows_written": len(enriched_rows),
            "dropped": dropped,
            "no_contact_marked": no_contact,
            "verified_contacts": verified,
            "drop_if_missing": should_drop_lead_without_contact(),
        }
    )


if __name__ == "__main__":
    main()
