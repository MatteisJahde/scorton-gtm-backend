import sqlite3

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from database import engine


def _add_column(conn, table: str, column_name: str, column_type: str) -> None:
    """Add a column, ignoring duplicate-column errors on redeploy."""
    statement = text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
    try:
        conn.execute(statement)
    except (OperationalError, sqlite3.OperationalError) as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def migrate_db() -> None:
    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    if "companies" in table_names:
        columns = {col["name"] for col in inspector.get_columns("companies")}
        with engine.begin() as conn:
            if "score" not in columns:
                _add_column(conn, "companies", "score", "INTEGER DEFAULT 0")
            if "priority_tier" not in columns:
                _add_column(conn, "companies", "priority_tier", "VARCHAR DEFAULT 'low'")
            company_migrations = [
                ("website", "VARCHAR"),
                ("size", "VARCHAR"),
                ("locality", "VARCHAR"),
                ("country", "VARCHAR"),
                ("linkedin_url", "VARCHAR"),
                ("is_targeted", "BOOLEAN DEFAULT 0"),
                ("week_assigned", "INTEGER"),
            ]
            for column_name, column_type in company_migrations:
                if column_name not in columns:
                    _add_column(conn, "companies", column_name, column_type)

    if "target_accounts" in table_names:
        columns = {col["name"] for col in inspector.get_columns("target_accounts")}
        migrations = [
            ("work_email", "VARCHAR"),
            ("company_linkedin_url", "VARCHAR"),
            ("city_validated", "BOOLEAN DEFAULT 0"),
            ("buying_signal", "INTEGER DEFAULT 0"),
            ("email_status", "VARCHAR DEFAULT 'Unverified'"),
            ("lead_verification_status", "VARCHAR DEFAULT 'Unverified'"),
            ("verification_status", "VARCHAR"),
            ("contact_verification_status", "VARCHAR"),
            ("funding", "VARCHAR"),
            ("revenue", "VARCHAR"),
            ("funding_amount", "VARCHAR"),
            ("funding_stage", "VARCHAR"),
            ("revenue_range", "VARCHAR"),
        ]
        with engine.begin() as conn:
            for column_name, column_type in migrations:
                if column_name not in columns:
                    _add_column(conn, "target_accounts", column_name, column_type)

            try:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS "
                        "uq_target_accounts_company_identity "
                        "ON target_accounts (company_name, website)"
                    )
                )
            except Exception:
                # Existing duplicate rows prevent index creation until data is cleaned.
                pass
