#!/usr/bin/env python3
"""
Standalone database initialization script for GJPP.

Importing app.py already runs load_data() automatically as a side effect
(see the `load_data()` call near the top-level code in app.py) — so simply
starting the server with `python app.py` or gunicorn already creates and/or
loads the SQLite database. This script exists so that step can be run
explicitly and separately from booting the web server, which is useful for:

  - A deployment "release" / "pre-deploy" command (e.g. Railway's Deploy
    Settings > Custom Start Command, run once before the web process starts)
  - A Docker build/entrypoint step
  - CI pipelines that just need to verify the schema
  - Manually confirming what's in the database, from a terminal

Usage:
    python init_db.py

It does NOT start the Flask/gunicorn web server — it only touches the
database, then exits.
"""
import sys

# Importing app triggers load_data() automatically (creates the DB + tables
# on first run, or restores existing data on subsequent runs) — see app.py.
from app import DB_PATH, _TABLE_SPECS, get_db_connection, save_data


def main():
    print(f"GJPP database: {DB_PATH}\n")

    # Belt-and-braces: make sure whatever is currently in memory (freshly
    # loaded or freshly seeded, per app.py's own load_data() call above)
    # is definitely flushed to disk before we report on it.
    save_data()

    conn = get_db_connection()
    print(f"{'TABLE':<26} ROWS")
    print("-" * 34)
    total_rows = 0
    for table in _TABLE_SPECS:
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            total_rows += count
            print(f"{table:<26} {count}")
        except Exception as e:
            print(f"{table:<26} ERROR: {e}")
    conn.close()

    print("-" * 34)
    print(f"{len(_TABLE_SPECS)} tables, {total_rows} total rows.\n")
    print("Database is ready.")


if __name__ == '__main__':
    sys.exit(main())
