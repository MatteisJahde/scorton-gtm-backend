"""Normalize and validate website URLs for API responses."""

from __future__ import annotations

from urllib.parse import urlparse

WEBSITE_STATUS_READY = "ready"
WEBSITE_STATUS_UNAVAILABLE = "unavailable"


def normalize_website(raw: object) -> str:
    """Return a trimmed https URL, or empty string when missing."""
    website = str(raw or "").strip()
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    return website.rstrip("/")


def domain_from_website(website: object) -> str:
    """Return bare hostname from a website URL."""
    normalized = normalize_website(website)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    domain = (parsed.netloc or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or normalized


def website_has_valid_format(website: object) -> bool:
    """True when the URL has a plausible hostname."""
    normalized = normalize_website(website)
    if not normalized:
        return False
    parsed = urlparse(normalized)
    host = (parsed.netloc or "").lower()
    if not host or "." not in host:
        return False
    return True


def website_display_status(website: object) -> str:
    """Dashboard status: ready when URL is formatted, unavailable otherwise."""
    if website_has_valid_format(website):
        return WEBSITE_STATUS_READY
    return WEBSITE_STATUS_UNAVAILABLE
