"""Company deduplication utilities for the GTM dataset pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)

DEFAULT_SCORE_FIELDS: Sequence[str] = (
    "qualification_score",
    "trust_opportunity_score",
    "company_ai_signal",
    "ai_signal",
    "icp_score",
    "score",
)


def get_company_name(record: dict) -> str:
    return str(record.get("company_name") or record.get("company") or "").strip()


def get_company_website(record: dict) -> str:
    return str(
        record.get("company_website") or record.get("website") or ""
    ).strip()


def normalize_company_name(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_company_website(value: object) -> str:
    website = str(value or "").strip().lower()
    if website.startswith("https://"):
        website = website[len("https://") :]
    elif website.startswith("http://"):
        website = website[len("http://") :]
    if website.startswith("www."):
        website = website[4:]
    return website.rstrip("/")


def company_identity_key(record: dict) -> tuple[str, str]:
    """Normalized (company_name, company_website) pair used for uniqueness checks."""
    return (
        normalize_company_name(get_company_name(record)),
        normalize_company_website(get_company_website(record)),
    )


def canonical_company_key(record: dict) -> str:
    """Primary dedupe key: normalized website when present, otherwise normalized name."""
    name, website = company_identity_key(record)
    if website:
        return f"website:{website}"
    if name:
        return f"name:{name}"
    return ""


def resolve_record_score(
    record: dict,
    score_fields: Sequence[str] = DEFAULT_SCORE_FIELDS,
) -> float:
    for field in score_fields:
        value = record.get(field)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


@dataclass(frozen=True)
class DeduplicationReport:
    input_count: int
    duplicates_removed: int
    final_count: int
    label: str = "companies"

    def log(self) -> None:
        logger.info(
            "%s: Loaded=%s Duplicates removed=%s Final unique=%s",
            self.label,
            self.input_count,
            self.duplicates_removed,
            self.final_count,
        )
        print(f"Loaded: {self.input_count}")
        print(f"Duplicates removed: {self.duplicates_removed}")
        print(f"Final unique companies: {self.final_count}")


def deduplicate_company_records(
    records: Iterable[dict],
    *,
    score_fields: Sequence[str] = DEFAULT_SCORE_FIELDS,
    label: str = "companies",
) -> tuple[list[dict], DeduplicationReport]:
    """Keep the highest-scoring record for each unique company identity."""
    items = list(records)
    input_count = len(items)
    if not items:
        report = DeduplicationReport(0, 0, 0, label=label)
        report.log()
        return [], report

    ranked = sorted(
        items,
        key=lambda record: resolve_record_score(record, score_fields),
        reverse=True,
    )

    unique: list[dict] = []
    seen_keys: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    for record in ranked:
        pair = company_identity_key(record)
        key = canonical_company_key(record)
        if not key:
            unique.append(record)
            continue
        if key in seen_keys or pair in seen_pairs:
            continue
        seen_keys.add(key)
        seen_pairs.add(pair)
        unique.append(record)

    report = DeduplicationReport(
        input_count=input_count,
        duplicates_removed=input_count - len(unique),
        final_count=len(unique),
        label=label,
    )
    report.log()
    return unique, report
