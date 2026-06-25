"""Strict financial-industry filtering for GTM lead pipelines."""

from __future__ import annotations

import re
from typing import Mapping, Optional

# Exact industry labels accepted from CSV / DB rows.
ALLOWED_INDUSTRIES = frozenset(
    {
        "Financial Services",
        "Insurance",
        "Accounting",
        "Financial Technology",
        "FinTech",
    }
)

# Industry text must contain at least one of these (case-insensitive substring).
FINANCIAL_INDUSTRY_KEYWORDS: tuple[str, ...] = (
    "finance",
    "financial",
    "fintech",
    "banking",
    "bank",
    "insurance",
    "insur",
    "accounting",
    "accountant",
    "investment",
    "wealth",
    "asset management",
    "capital",
    "securities",
    "brokerage",
    "broker",
    "lending",
    "loan",
    "mortgage",
    "payment",
    "credit",
    "treasury",
    "audit",
    "actuarial",
    "underwriting",
    "reinsurance",
)

# Reject when any of these appear in company name, industry, or description.
NON_FINANCIAL_BLOCKLIST_KEYWORDS: tuple[str, ...] = (
    "sandwich",
    "cafe",
    "café",
    "coffee",
    "restaurant",
    "bakery",
    "deli",
    "pizza",
    "bagel",
    "bistro",
    "grill",
    "diner",
    "eatery",
    "catering",
    "tavern",
    "brewery",
    "brewpub",
    "pub ",
    "food truck",
    "smoothie",
    "juice bar",
    "donut",
    "doughnut",
    "pastry",
    "sushi",
    "taco",
    "burger",
    "wings",
    "noodle",
    "ramen",
    "tea house",
    "florist",
    "salon",
    "barbershop",
    "spa ",
    "yoga",
    "pilates",
    "gym",
    "fitness studio",
    "veterinar",
    "veterinary",
    " vet ",
    "clinic",
    "hospital",
    "medical",
    "dental",
    "chiropractic",
    "landscaping",
    "plumbing",
    "roofing",
    "cleaning service",
    "laundromat",
    "dry clean",
    "pet groom",
    "dog walk",
    "daycare",
    "preschool",
    "nursery school",
    "hotel",
    "motel",
    "hostel",
    "travel agency",
    "tour operator",
    "real estate agent",
    "realtor",
    "car wash",
    "auto repair",
    "tire shop",
    "convenience store",
    "grocery",
    "supermarket",
    "farmers market",
    "butcher",
    "fish market",
    "ice cream",
    "gelato",
    "candy shop",
    "chocolate shop",
    "wine bar",
    "cocktail bar",
    "nightclub",
    "liquor store",
)



_WORD_BOUNDARY_KEYWORDS = frozenset(
    {
        "deli",
        "hospital",
        "spa",
        "pub",
        "bar",
        "gym",
        "vet",
        "inn",
        "grill",
    }
)


def _blocklist_pattern(keyword: str) -> re.Pattern[str]:
    token = keyword.strip()
    if " " in token:
        return re.compile(re.escape(token), re.IGNORECASE)
    if len(token) <= 4 or token in _WORD_BOUNDARY_KEYWORDS:
        return re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    return re.compile(re.escape(token), re.IGNORECASE)


_BLOCKLIST_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (keyword, _blocklist_pattern(keyword))
    for keyword in NON_FINANCIAL_BLOCKLIST_KEYWORDS
)


def _field_text(record: Mapping[str, object], *keys: str) -> str:
    parts: list[str] = []
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts)


def matched_blocklist_keyword(*texts: str) -> Optional[str]:
    """Return the first blocklisted keyword found in the provided text blobs."""
    blob = " ".join(text.strip() for text in texts if text and str(text).strip())
    if not blob:
        return None
    for keyword, pattern in _BLOCKLIST_PATTERNS:
        if pattern.search(blob):
            return keyword
    return None


def industry_matches_financial_icp(industry: str) -> bool:
    """True when industry label is allowlisted or contains a finance keyword."""
    normalized = (industry or "").strip()
    if not normalized:
        return False
    if normalized in ALLOWED_INDUSTRIES:
        return True
    lowered = normalized.lower()
    return any(keyword in lowered for keyword in FINANCIAL_INDUSTRY_KEYWORDS)


def passes_financial_icp_filter(
    record: Mapping[str, object],
) -> tuple[bool, Optional[str]]:
    """
    Return (accepted, rejection_reason).

    Checks company name, industry, website, and optional description/notes.
    """
    company = _field_text(record, "company", "company_name", "name")
    industry = _field_text(record, "industry")
    description = _field_text(record, "description", "notes", "company_description")
    website = _field_text(record, "website", "company_website")

    blocked = matched_blocklist_keyword(company, industry, description, website)
    if blocked:
        return False, f"blocklist:{blocked}"

    if not industry_matches_financial_icp(industry):
        return False, f"industry_not_financial:{industry or '(empty)'}"

    return True, None
