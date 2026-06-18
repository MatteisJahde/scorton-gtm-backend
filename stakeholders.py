NAMES = [
    ("John", "Smith"),
    ("Sarah", "Johnson"),
    ("Michael", "Lee"),
    ("Emily", "Brown"),
    ("David", "Wilson"),
    ("Laura", "Martinez"),
    ("James", "Taylor"),
    ("Anna", "Garcia"),
]

ROLE_SPECS = [
    ("Hero", ["CTO", "VP Engineering", "Chief Architect"], 1, 2),
    ("Buyer", ["CFO", "VP Finance", "Head of Procurement"], 1, 1),
    ("Ambassador", ["Director of Partnerships", "VP Strategy", "Chief of Staff"], 1, 1),
    ("Early Adopter", ["Innovation Lead", "Digital Transformation Manager", "Head of Emerging Tech"], 1, 1),
]

QUALIFIED_SCORE_THRESHOLD = 70


def _email_domain(company_name: str) -> str:
    slug = "".join(c for c in company_name.lower() if c.isalnum())
    return f"{slug[:24]}.com"


def generate_stakeholders(company) -> list[dict]:
    hero_count = 2 if company.id % 2 == 0 else 1
    domain = _email_domain(company.name)
    stakeholders = []
    name_idx = 0

    for role_type, titles, min_count, max_count in ROLE_SPECS:
        count = hero_count if role_type == "Hero" else min_count
        for i in range(count):
            first, last = NAMES[name_idx % len(NAMES)]
            name_idx += 1
            slug = f"{first}-{last}".lower()
            stakeholders.append(
                {
                    "company_id": company.id,
                    "name": f"{first} {last}",
                    "title": titles[i % len(titles)],
                    "role_type": role_type,
                    "email": f"{first.lower()}.{last.lower()}@{domain}",
                    "linkedin_url": f"https://linkedin.com/in/{slug}-{company.id}",
                }
            )

    return stakeholders
