"""Lead scoring helpers for the weekly GTM engine."""

from __future__ import annotations

import re
from typing import Iterable

from models import TargetAccount
from sorting_agent import CITY_RANK_WEIGHTS

ICP_WEIGHT = 0.40
INTENT_WEIGHT = 0.40
DATA_QUALITY_WEIGHT = 0.20
TOP_N = 250

INTENT_KEYWORDS = ("software", "tech", "sales", "marketing")


def is_populated(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip() != ""


def parse_headcount(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    numbers = [float(match) for match in re.findall(r"\d+\.?\d*", text)]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def score_icp(headcount: float | None) -> int:
    if headcount is None:
        return 0
    if 50 <= headcount <= 200:
        return 100
    if 10 <= headcount <= 49:
        return 60
    if headcount >= 1000:
        return 30
    return 0


def score_intent(industry: object) -> int:
    if not is_populated(industry):
        return 40
    lowered = str(industry).lower()
    if any(keyword in lowered for keyword in INTENT_KEYWORDS):
        return 100
    return 40


def score_data_quality(website: object, linkedin_url: object) -> int:
    if is_populated(website) and is_populated(linkedin_url):
        return 100
    return 0


def total_score_for_account(account: TargetAccount) -> float:
    icp = score_icp(parse_headcount(account.employee_count))
    intent = score_intent(account.industry)
    data_quality = score_data_quality(account.website, account.linkedin_url)
    city_bonus = CITY_RANK_WEIGHTS.get(account.city or "", 0)
    return (
        icp * ICP_WEIGHT
        + intent * INTENT_WEIGHT
        + data_quality * DATA_QUALITY_WEIGHT
        + city_bonus
    )


def select_top_accounts(
    accounts: Iterable[TargetAccount],
    top_n: int = TOP_N,
) -> list[TargetAccount]:
    ranked = sorted(
        accounts,
        key=lambda account: (-total_score_for_account(account), account.id),
    )
    return ranked[:top_n]
