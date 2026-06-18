# Simulated People Data Labs company dataset

# 20 high-quality Chicago anchors: Fintech, Insurance, and Data/Security firms.
CHICAGO_SEED_COMPANIES = [
    # Fintech / Financial Services
    {"name": "Northern Trust", "industry": "Financial Services", "city": "Chicago", "employee_count": 95},
    {"name": "Morningstar", "industry": "Financial Services", "city": "Chicago", "employee_count": 450},
    {"name": "CME Group", "industry": "Financial Services", "city": "Chicago", "employee_count": 380},
    {"name": "TransUnion", "industry": "Financial Services", "city": "Chicago", "employee_count": 420},
    {"name": "William Blair", "industry": "Financial Services", "city": "Chicago", "employee_count": 175},
    {"name": "Envestnet", "industry": "Financial Services", "city": "Chicago", "employee_count": 480},
    {"name": "M1 Finance", "industry": "Financial Services", "city": "Chicago", "employee_count": 350},
    {"name": "Braintree", "industry": "Financial Services", "city": "Chicago", "employee_count": 140},
    {"name": "Akuna Capital", "industry": "Financial Services", "city": "Chicago", "employee_count": 75},
    # Insurance
    {"name": "Old Republic International", "industry": "Insurance", "city": "Chicago", "employee_count": 210},
    {"name": "Progressive", "industry": "Insurance", "city": "Chicago", "employee_count": 120},
    {"name": "CNA Financial", "industry": "Insurance", "city": "Chicago", "employee_count": 400},
    {"name": "HUB International", "industry": "Insurance", "city": "Chicago", "employee_count": 280},
    {"name": "Zurich North America", "industry": "Insurance", "city": "Chicago", "employee_count": 190},
    {"name": "Country Financial", "industry": "Insurance", "city": "Chicago", "employee_count": 95},
    {"name": "AccuQuote", "industry": "Insurance", "city": "Chicago", "employee_count": 45},
    # Data / Security (mapped to qualifying GTM industries)
    {"name": "Relativity", "industry": "Financial Services", "city": "Chicago", "employee_count": 150},
    {"name": "Trustwave", "industry": "Financial Services", "city": "Chicago", "employee_count": 320},
    {"name": "Keeper Security", "industry": "Financial Services", "city": "Chicago", "employee_count": 185},
    {"name": "Jellyvision", "industry": "Accounting", "city": "Chicago", "employee_count": 260},
]

_BASE_COMPANIES = [
    # Passes all filters (city, industry, employee_count 20–500)
    {"name": "Apex Capital Partners", "industry": "Financial Services", "city": "New York", "employee_count": 120},
    {"name": "Hudson Street Advisors", "industry": "Financial Services", "city": "New York", "employee_count": 45},
    {"name": "Harbor Mutual", "industry": "Insurance", "city": "New York", "employee_count": 280},
    {"name": "MetroSure Insurance", "industry": "Insurance", "city": "New York", "employee_count": 85},
    {"name": "LedgerPoint CPA", "industry": "Accounting", "city": "New York", "employee_count": 60},
    {"name": "Summit Audit Group", "industry": "Accounting", "city": "New York", "employee_count": 150},
    {"name": "Bayline Financial", "industry": "Financial Services", "city": "San Francisco", "employee_count": 95},
    {"name": "Pacific Ledger Co", "industry": "Financial Services", "city": "San Francisco", "employee_count": 320},
    {"name": "Golden Gate Underwriters", "industry": "Insurance", "city": "San Francisco", "employee_count": 40},
    {"name": "Norcal Tax Partners", "industry": "Accounting", "city": "San Francisco", "employee_count": 210},
    {"name": "Queen City Capital", "industry": "Financial Services", "city": "Charlotte", "employee_count": 175},
    {"name": "Piedmont Securities", "industry": "Financial Services", "city": "Charlotte", "employee_count": 30},
    {"name": "Carolinas Coverage", "industry": "Insurance", "city": "Charlotte", "employee_count": 400},
    {"name": "Blue Ridge Accountants", "industry": "Accounting", "city": "Charlotte", "employee_count": 55},
    {"name": "Biscayne Advisors", "industry": "Financial Services", "city": "Miami", "employee_count": 88},
    {"name": "Coral Capital", "industry": "Financial Services", "city": "Miami", "employee_count": 250},
    {"name": "Sunshine Risk Group", "industry": "Insurance", "city": "Miami", "employee_count": 35},
    {"name": "Palm Auditors", "industry": "Accounting", "city": "Miami", "employee_count": 190},
    {"name": "Wall Street Micro Fund", "industry": "Financial Services", "city": "New York", "employee_count": 20},
    {"name": "SoMa FinTech Collective", "industry": "Financial Services", "city": "San Francisco", "employee_count": 500},
    {"name": "East River Finance", "industry": "Financial Services", "city": "New York", "employee_count": 110},
    {"name": "Silicon Valley Insure", "industry": "Insurance", "city": "San Francisco", "employee_count": 155},
    {"name": "Charlotte Ledger Works", "industry": "Accounting", "city": "Charlotte", "employee_count": 90},
    {"name": "Miami Trust Advisors", "industry": "Financial Services", "city": "Miami", "employee_count": 42},
    {"name": "Bayfront Actuaries", "industry": "Insurance", "city": "Miami", "employee_count": 220},
] + CHICAGO_SEED_COMPANIES + [
    # Fails employee_count filter (too low)
    {"name": "Tiny Brokerage", "industry": "Financial Services", "city": "New York", "employee_count": 12},
    {"name": "Solo Actuary LLC", "industry": "Insurance", "city": "Miami", "employee_count": 8},
    # Fails employee_count filter (too high)
    {"name": "JPMorgan Chase", "industry": "Financial Services", "city": "New York", "employee_count": 250000},
    {"name": "Goldman Sachs", "industry": "Financial Services", "city": "New York", "employee_count": 50000},
    {"name": "Bank of America", "industry": "Financial Services", "city": "Charlotte", "employee_count": 200000},
    {"name": "Deloitte", "industry": "Accounting", "city": "Miami", "employee_count": 150000},
    # Fails industry filter
    {"name": "TechFin Labs", "industry": "Technology", "city": "San Francisco", "employee_count": 80},
    {"name": "Mayo Clinic", "industry": "Healthcare", "city": "Miami", "employee_count": 200},
]

from sorting_agent import ALLOWED_CITIES

_CITIES = sorted(ALLOWED_CITIES)
_INDUSTRIES = ["Financial Services", "Insurance", "Accounting"]
_PREFIXES = [
    "Atlas", "Beacon", "Crown", "Delta", "Echo", "Falcon", "Granite", "Harbor", "Iron", "Juniper",
    "Keystone", "Lighthouse", "Meridian", "Nova", "Orion", "Pioneer", "Quantum", "River", "Sterling", "Titan",
    "Union", "Vertex", "Westfield", "Zenith", "Axiom", "Bridgewater", "Catalyst", "Dominion", "Evergreen", "Frontier",
]
_STEMS = {
    "Financial Services": ["Capital", "Advisors", "Securities", "Partners", "Trust", "Holdings", "Finance", "Wealth"],
    "Insurance": ["Mutual", "Underwriters", "Coverage", "Risk", "Assurance", "Indemnity", "Actuarial", "Brokers"],
    "Accounting": ["CPA", "Audit", "Ledger", "Tax Partners", "Advisors", "Associates", "Compliance", "Advisory"],
}


def _is_qualifying(company: dict) -> bool:
    count = company.get("employee_count")
    return (
        company["city"] in _CITIES
        and company["industry"] in _INDUSTRIES
        and count is not None
        and 20 <= count <= 500
    )


def _expand_to_target(target_qualifying: int = 1000) -> list:
    companies = list(_BASE_COMPANIES)
    existing_names = {company["name"] for company in companies}
    qualifying_count = sum(1 for company in companies if _is_qualifying(company))
    index = 1

    while qualifying_count < target_qualifying:
        industry = _INDUSTRIES[index % len(_INDUSTRIES)]
        city = _CITIES[index % len(_CITIES)]
        employee_count = 20 + ((index * 13) % 481)
        stem = _STEMS[industry][(index // len(_CITIES)) % len(_STEMS[industry])]
        prefix = _PREFIXES[index % len(_PREFIXES)]
        name = f"{prefix} {stem} Group {index:04d}"

        if name not in existing_names:
            companies.append(
                {
                    "name": name,
                    "industry": industry,
                    "city": city,
                    "employee_count": employee_count,
                }
            )
            existing_names.add(name)
            qualifying_count += 1
        index += 1

    return companies


COMPANIES = _expand_to_target(1000)
