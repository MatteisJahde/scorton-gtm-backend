"""
People Data Labs Person Enrichment API — contact / title validation.

Docs: https://docs.peopledatalabs.com/docs/person-enrichment-api
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import requests

PDL_PERSON_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"
DEFAULT_TIMEOUT_SECONDS = 20


class PDLPersonError(Exception):
    """Raised when PDL person enrichment fails."""


def get_pdl_api_key() -> Optional[str]:
    return (os.getenv("PDL_API_KEY") or "").strip() or None


def _split_name(full_name: str) -> Tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def _normalize_company_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    for suffix in (" inc", " llc", " ltd", " corp", " co", " group", " holdings"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned


def _company_names_match(expected: str, actual: str) -> bool:
    left = _normalize_company_name(expected)
    right = _normalize_company_name(actual)
    if not left or not right:
        return False
    return left in right or right in left or left.split()[0] == right.split()[0]


def _normalize_title_tokens(title: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (title or "").lower())
    stop = {"of", "the", "and", "for", "a", "an", "at", "to"}
    return {token for token in tokens if token not in stop and len(token) > 1}


TITLE_ACRONYMS: dict[str, set[str]] = {
    "cio": {"cio", "chief", "information", "officer"},
    "cto": {"cto", "chief", "technology", "officer"},
    "ciso": {"ciso", "chief", "information", "security", "officer"},
    "ceo": {"ceo", "chief", "executive", "officer"},
    "cfo": {"cfo", "chief", "financial", "officer"},
    "coo": {"coo", "chief", "operating", "officer"},
    "cro": {"cro", "chief", "revenue", "officer", "risk"},
    "caio": {"caio", "chief", "ai", "artificial", "intelligence", "officer"},
}


def titles_compatible(expected_title: str, actual_title: str) -> bool:
    """Return True when professional titles are plausibly the same role."""
    expected_tokens = _normalize_title_tokens(expected_title)
    actual_tokens = _normalize_title_tokens(actual_title)
    if not expected_tokens or not actual_tokens:
        return False

    overlap = expected_tokens & actual_tokens
    if len(overlap) >= 2:
        return True
    if overlap and any(token in {"head", "director", "president", "partner", "manager"} for token in overlap):
        return True

    for acronym, expansion in TITLE_ACRONYMS.items():
        if acronym in expected_tokens or expansion <= expected_tokens:
            if acronym in actual_tokens or expansion <= actual_tokens:
                return True

    return False


def pdl_enrich_person(
    *,
    email: str,
    company_name: str,
    buyer_name: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    key = api_key or get_pdl_api_key()
    if not key:
        raise PDLPersonError("PDL_API_KEY is not configured")

    first_name, last_name = _split_name(buyer_name)
    params: Dict[str, str] = {
        "api_key": key,
        "email": email.strip(),
        "company": company_name.strip(),
    }
    if first_name:
        params["first_name"] = first_name
    if last_name:
        params["last_name"] = last_name

    response = requests.get(
        PDL_PERSON_ENRICH_URL,
        params=params,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PDLPersonError(f"Invalid JSON from PDL: {response.text[:200]}") from exc

    if response.status_code == 404:
        return {"found": False, "provider": "pdl", "status": 404, "person": None}

    if response.status_code != 200:
        message = payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else response.text[:200]
        raise PDLPersonError(f"PDL HTTP {response.status_code}: {message}")

    person = payload.get("data") or payload
    job_title = (
        person.get("job_title")
        or (person.get("job_title_role") or "")
        or ""
    )
    if isinstance(person.get("experience"), list) and person["experience"]:
        current = person["experience"][0] or {}
        job_title = job_title or current.get("title") or ""

    job_company = (
        person.get("job_company_name")
        or person.get("job_company_website")
        or ""
    )
    full_name = person.get("full_name") or person.get("name") or ""

    return {
        "found": True,
        "provider": "pdl",
        "person": person,
        "full_name": full_name,
        "job_title": job_title,
        "job_company_name": job_company,
        "linkedin_url": person.get("linkedin_url"),
    }


def domain_from_website(website: str) -> str:
    raw = (website or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    host = urlparse(raw).netloc.lower()
    return host[4:] if host.startswith("www.") else host
