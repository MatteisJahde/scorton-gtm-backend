"""
Email suppression list for hard bounces and do-not-mail addresses.

Sources (checked in order):
  1. In-memory cache loaded from DB + file
  2. ``data/suppression_list.txt`` (one email per line, # comments allowed)
  3. ``email_suppressions`` SQLite table
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from database import SessionLocal
from models import EmailSuppression

ROOT_DIR = Path(__file__).resolve().parents[1]
SUPPRESSION_FILE = ROOT_DIR / "data" / "suppression_list.txt"

_cache: set[str] = set()
_cache_loaded = False
_cache_lock = threading.Lock()


def _normalize(email: object) -> str:
    return str(email or "").strip().lower()


def _load_file_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    emails: set[str] = set()
    for line in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        emails.add(_normalize(stripped))
    return emails


def _load_db_suppressions(db: Session) -> set[str]:
    try:
        rows = db.query(EmailSuppression.email).all()
        return {_normalize(row[0]) for row in rows if row[0]}
    except Exception:
        return set()


def refresh_suppression_cache(db: Optional[Session] = None) -> int:
    """Reload suppression list from file + database."""
    global _cache_loaded
    session = db or SessionLocal()
    close_session = db is None
    try:
        combined = _load_file_suppressions() | _load_db_suppressions(session)
        with _cache_lock:
            _cache.clear()
            _cache.update(combined)
            _cache_loaded = True
        return len(_cache)
    finally:
        if close_session:
            session.close()


def is_suppressed(email: str) -> bool:
    normalized = _normalize(email)
    if not normalized:
        return False
    with _cache_lock:
        if not _cache_loaded:
            refresh_suppression_cache()
        return normalized in _cache


def add_suppression(
    email: str,
    *,
    reason: str = "hard_bounce",
    source: Optional[str] = None,
    db: Optional[Session] = None,
) -> bool:
    """Record a hard bounce / do-not-mail address. Returns True if newly added."""
    normalized = _normalize(email)
    if not normalized or "@" not in normalized:
        return False

    session = db or SessionLocal()
    close_session = db is None
    try:
        existing = (
            session.query(EmailSuppression)
            .filter(EmailSuppression.email == normalized)
            .first()
        )
        if existing:
            return False
        session.add(
            EmailSuppression(
                email=normalized,
                reason=reason,
                source=source,
                created_at=datetime.utcnow(),
            )
        )
        session.commit()
        with _cache_lock:
            _cache.add(normalized)
        return True
    finally:
        if close_session:
            session.close()


def remove_suppression(email: str, *, db: Optional[Session] = None) -> bool:
    normalized = _normalize(email)
    session = db or SessionLocal()
    close_session = db is None
    try:
        deleted = (
            session.query(EmailSuppression)
            .filter(EmailSuppression.email == normalized)
            .delete()
        )
        session.commit()
        with _cache_lock:
            _cache.discard(normalized)
        return deleted > 0
    finally:
        if close_session:
            session.close()


def list_suppressions(*, db: Optional[Session] = None) -> list[dict]:
    session = db or SessionLocal()
    close_session = db is None
    try:
        rows = session.query(EmailSuppression).order_by(EmailSuppression.created_at.desc()).all()
        return [
            {
                "email": row.email,
                "reason": row.reason,
                "source": row.source,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    finally:
        if close_session:
            session.close()
