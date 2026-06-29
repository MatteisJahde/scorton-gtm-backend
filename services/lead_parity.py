"""Lead-count parity checks during CSV initialization."""

from __future__ import annotations

from typing import Any


def verify_lead_count_parity(*, processed: int, saved: int) -> dict[str, Any]:
    """
    Compare scraper output count to rows persisted in the companies table.

    Returns a small report dict with ``ok`` True only when counts match exactly.
    """
    dropped = processed - saved
    ok = processed == saved
    message = (
        "Lead parity OK: "
        f"{processed} scraper leads == {saved} database rows."
        if ok
        else (
            "Lead parity FAILED: scraper processed "
            f"{processed} leads but database saved {saved} "
            f"({dropped} lead(s) missing)."
        )
    )
    return {
        "ok": ok,
        "processed": processed,
        "saved": saved,
        "dropped": dropped,
        "message": message,
    }
