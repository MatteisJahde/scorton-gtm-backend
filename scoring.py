from sorting_agent import CITY_RANK_WEIGHTS

INDUSTRY_SCORES = {
    "Financial Services": 30,
    "Insurance": 25,
    "Accounting": 25,
}

CITY_SCORES = CITY_RANK_WEIGHTS


def _get(company, field):
    return company[field] if isinstance(company, dict) else getattr(company, field)


def score_company(company) -> int:
    score = INDUSTRY_SCORES.get(_get(company, "industry"), 0)

    count = _get(company, "employee_count")
    if count is not None:
        if 200 <= count <= 500:
            score += 20
        elif 50 <= count <= 199:
            score += 10
        elif 20 <= count <= 49:
            score += 5

    score += CITY_SCORES.get(_get(company, "city"), 0)
    return min(score, 100)


def priority_tier(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 50:
        return "medium"
    return "low"
