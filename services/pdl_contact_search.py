"""
PDL Person Search — find buyer contacts for company list building.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import requests

from config.personas import INDUSTRY_EXECUTIVE_TITLES, DEFAULT_EXECUTIVE_TITLES
from services.pdl_client import map_pdl_industry_to_target_industry
from services.pdl_person import domain_from_website

PDL_PERSON_SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"
DEFAULT_TIMEOUT_SECONDS = 60


class PDLPersonSearchError(RuntimeError):
    """Raised when PDL person search fails."""


def _safe_sql_token(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.\- ]", "", (value or "").strip())


def _extract_work_email(person: Dict[str, Any]) -> Optional[str]:
    direct = (person.get("work_email") or "").strip()
    if direct:
        return direct

    emails = person.get("emails") or []
    for entry in emails:
        if isinstance(entry, dict):
            address = (entry.get("address") or "").strip()
            if address:
                return address
        elif isinstance(entry, str) and entry.strip():
            return entry.strip()
    return None


def _extract_error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return response.text
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("message") or json.dumps(body)
    return json.dumps(body)


def _pick_person(records: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for person in records:
        name = (person.get("full_name") or person.get("name") or "").strip()
        title = (person.get("job_title") or "").strip()
        email = _extract_work_email(person)
        if name and title and email:
            return person
    for person in records:
        name = (person.get("full_name") or person.get("name") or "").strip()
        title = (person.get("job_title") or "").strip()
        if name and title:
            return person
    return records[0] if records else None


def search_buyer_contact(
    *,
    company_name: str,
    website: str,
    industry: str,
    api_key: str,
) -> Optional[Dict[str, str]]:
    """
    Find a senior buyer contact at a company using PDL Person Search.

    Returns buyer_name, job_title, work_email (email may be empty if PDL omits it).
    """
    domain = domain_from_website(website)
    safe_domain = _safe_sql_token(domain)
    safe_company = _safe_sql_token(company_name)
    if not safe_domain and not safe_company:
        return None

    target_industry = map_pdl_industry_to_target_industry(industry)
    title_pool = INDUSTRY_EXECUTIVE_TITLES.get(target_industry, DEFAULT_EXECUTIVE_TITLES)
    title_terms = ", ".join(f"'{term.lower()}'" for term in title_pool[:5])

    domain_clause = f"job_company_website LIKE '%{safe_domain}%'" if safe_domain else ""
    company_clause = f"job_company_name = '{safe_company}'" if safe_company else ""
    company_filter = domain_clause or company_clause
    if domain_clause and company_clause:
        company_filter = f"({domain_clause} OR {company_clause})"

    sql = (
        "SELECT * FROM person WHERE "
        f"{company_filter} AND "
        "location.country = 'united states' AND "
        f"(job_title_role IN ({title_terms}) OR job_title_levels IN ('cxo', 'vp', 'director'))"
    )

    headers = {
        "Content-Type": "application/json",
        "X-api-key": api_key,
    }
    payload = {"sql": sql, "size": 5, "titlecase": True}

    try:
        response = requests.post(
            PDL_PERSON_SEARCH_URL,
            headers=headers,
            json=payload,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise PDLPersonSearchError(f"PDL person search connection error: {exc}") from exc

    if response.status_code != 200:
        raise PDLPersonSearchError(
            f"PDL person search failed for {company_name}: "
            f"HTTP {response.status_code} — {_extract_error_message(response)}"
        )

    body = response.json()
    if body.get("status") != 200:
        return None

    person = _pick_person(body.get("data") or [])
    if not person:
        return None

    buyer_name = (person.get("full_name") or person.get("name") or "").strip()
    job_title = (person.get("job_title") or title_pool[0]).strip()
    work_email = _extract_work_email(person) or ""

    return {
        "buyer_name": buyer_name,
        "job_title": job_title,
        "work_email": work_email,
        "linkedin_url": (person.get("linkedin_url") or "").strip(),
    }
