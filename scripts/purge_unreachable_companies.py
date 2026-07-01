#!/usr/bin/env python3
"""One-off cleanup: HEAD-check every company and delete unreachable records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database import SessionLocal  # noqa: E402
from migrations import migrate_db  # noqa: E402
from services.website_maintenance import purge_unreachable_companies_from_database  # noqa: E402


def main() -> int:
    print("[purge-unreachable] running database migrations...", flush=True)
    migrate_db()

    db = SessionLocal()
    try:
        print("[purge-unreachable] checking every company website with HEAD...", flush=True)
        result = purge_unreachable_companies_from_database(db)
        print(json.dumps(result, indent=2, default=str), flush=True)
        print(
            "[purge-unreachable] done — "
            f"checked={result['checked']} kept={result['kept']} removed={result['removed']}",
            flush=True,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
