"""
Executive buyer personas for AI, risk, governance, and compliance stakeholders.
"""

INDUSTRY_EXECUTIVE_TITLES = {
    "Financial Services": [
        "CIO",
        "CAIO",
        "CRO",
        "Head of Risk",
        "Director of Compliance",
    ],
    "Insurance": [
        "CIO",
        "CRO",
        "Head of Risk",
        "GRC Manager",
        "Compliance Officer",
    ],
    "Accounting": [
        "CIO",
        "Director of Technology",
        "AI Program Manager",
        "Digital Transformation Lead",
        "Director of Governance",
    ],
}

DEFAULT_EXECUTIVE_TITLES = [
    "CIO",
    "CAIO",
    "CTO",
    "CISO",
    "CRO",
    "Head of Risk",
    "Director of Risk",
    "VP Risk Management",
    "GRC Manager",
    "Director of Governance",
    "Director of Compliance",
    "Compliance Officer",
    "AI Governance Lead",
    "AI Program Manager",
    "Digital Transformation Lead",
]


def pick_executive_title(industry: str, index: int) -> str:
    titles = INDUSTRY_EXECUTIVE_TITLES.get(industry, DEFAULT_EXECUTIVE_TITLES)
    return titles[index % len(titles)]
