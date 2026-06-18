"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR / ".env"

if load_dotenv and ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# Default Lovable + local dev origins. Override with CORS_ORIGINS in .env.
DEFAULT_CORS_ORIGINS = (
    "https://lovable.app",
    "https://lovable.dev",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://lovable.app")

# Matches https://your-project.lovable.app and preview subdomains.
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https://([a-zA-Z0-9-]+\.)*lovable\.app",
)


def get_cors_origins() -> list[str]:
    """Comma-separated CORS_ORIGINS, plus FRONTEND_URL when set."""
    configured = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if configured:
        return configured

    origins = list(DEFAULT_CORS_ORIGINS)
    if FRONTEND_URL and FRONTEND_URL not in origins:
        origins.append(FRONTEND_URL)
    return origins
