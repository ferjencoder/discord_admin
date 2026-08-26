from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from state import AdminState

load_dotenv()

TABLES = (
    "member_links",
    "away",
    "daily_schedule_posts",
    "welcomed_members",
    "verification_requests",
    "verification_history",
    "member_profiles",
    "bot_state",
)


def main() -> None:
    sqlite_path = Path(os.getenv("STATE_DB", "data/ozy_admin.sqlite3")).expanduser()
    database_url = (os.getenv("STATE_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip())
    if not database_url:
        raise SystemExit("Set STATE_DATABASE_URL (or DATABASE_URL) to the destination PostgreSQL database.")
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite source does not exist: {sqlite_path}")

    # Initialize/migrate both schemas before copying rows.
    AdminState(sqlite_path).close()
    destination = AdminState(sqlite_path, database_url)

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    copied_total = 0
    try:
        for table in TABLES:
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                print(f"{table}: 0 rows")
                continue
            columns = list(rows[0].keys())
            column_sql = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            inserted = 0
            with destination._conn() as conn:  # one migration-only transaction per table
                for row in rows:
                    cursor = conn.execute(
                        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                        tuple(row[column] for column in columns),
                    )
                    if getattr(cursor, "rowcount", 0) > 0:
                        inserted += 1
            copied_total += inserted
            print(f"{table}: {inserted}/{len(rows)} rows copied")
    finally:
        source.close()
        destination.close()

    print(f"Migration complete. {copied_total} rows inserted into PostgreSQL.")
    print("Existing destination rows were preserved on primary/unique-key conflicts.")


if __name__ == "__main__":
    main()
