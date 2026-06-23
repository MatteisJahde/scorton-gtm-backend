"""Lead sorting and ranking for final dataset cuts."""

from __future__ import annotations

from typing import Any, Iterable

ALLOWED_CITIES = frozenset({"New York", "San Francisco", "Charlotte", "Miami"})

# Per-city target account quotas (must sum to MAX_TARGET_ACCOUNTS).
CITY_TARGET_QUOTAS = {
    "New York": 400,
    "San Francisco": 300,
    "Charlotte": 200,
    "Miami": 100,
}
MAX_TARGET_ACCOUNTS = sum(CITY_TARGET_QUOTAS.values())

# Tier-1 hubs — equal weight in ranking and enrichment bonuses.
TIER_ONE_CITIES = frozenset({"New York", "San Francisco"})
TIER_TWO_CITIES = frozenset(ALLOWED_CITIES - TIER_ONE_CITIES)

# Shared city weights used by scoring, enrichment, and export ranking.
CITY_RANK_WEIGHTS = {
    "New York": 15,
    "San Francisco": 15,
    "Charlotte": 10,
    "Miami": 10,
}

PRIORITY_CITIES = TIER_ONE_CITIES


def city_rank_weight(city: str | None) -> int:
    return CITY_RANK_WEIGHTS.get(city or "", 0)


def city_priority_bonus(city: str | None, city_validated: bool) -> int:
    if not city_validated:
        return 0
    if city in TIER_ONE_CITIES:
        return 10
    return 5


def _company_name(company: Any) -> str:
    if isinstance(company, dict):
        return str(company.get("company_name") or company.get("name") or "")
    return str(getattr(company, "name", "") or getattr(company, "company_name", ""))


def _company_city(company: Any) -> str:
    if isinstance(company, dict):
        return str(company.get("city") or "")
    return str(getattr(company, "city", "") or "")


def _company_score(company: Any) -> int:
    if isinstance(company, dict):
        for key in ("trust_opportunity_score", "score", "qualification_score"):
            value = company.get(key)
            if value not in (None, ""):
                return int(float(value))
        return 0
    for attr in ("trust_opportunity_score", "score"):
        value = getattr(company, attr, None)
        if value is not None:
            return int(value)
    return 0


def rank_company_for_final_cut(
    company: Any,
    *,
    priority_names: set[str] | None = None,
) -> tuple[int, int, int, int]:
    """Lower tuple values sort earlier (higher priority)."""
    priority_names = priority_names or set()
    name = _company_name(company)
    city = _company_city(company)
    company_id = int(getattr(company, "id", 0) or company.get("id", 0) or 0)

    return (
        0 if name in priority_names else 1,
        -city_rank_weight(city),
        -_company_score(company),
        company_id,
    )


def sort_companies_for_final_cut(
    companies: Iterable[Any],
    *,
    priority_names: set[str] | None = None,
) -> list[Any]:
    ranked = list(companies)
    ranked.sort(key=lambda company: rank_company_for_final_cut(company, priority_names=priority_names))
    return ranked


def qualification_score_with_city(row: dict[str, Any]) -> float:
    base = (
        float(row.get("ai_signal") or 0) * 0.30
        + float(row.get("risk_signal") or 0) * 0.25
        + float(row.get("trust_opportunity_score") or 0) * 0.25
        + float(row.get("icp_score") or 0) * 0.20
    )
    return base + city_rank_weight(str(row.get("city") or ""))


def select_balanced_leads(
    leads: Iterable[dict[str, Any]],
    *,
    total_limit: int,
    chicago_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return the top-ranked leads up to total_limit."""
    ranked = sort_companies_for_final_cut(leads)
    return ranked[:total_limit]
