"""Create the production tables from schema/schema.sql without importing CSVs."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "schema" / "schema.sql"

# Make the package import work both as `python -m scripts.create_tables` and
# `python scripts/create_tables.py` from the project root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.migrator import load_config, safe_error  # noqa: E402


def read_and_check_schema() -> tuple[str, list[str]]:
    if not SCHEMA_FILE.is_file():
        raise RuntimeError(f"Schema file not found: {SCHEMA_FILE}")
    source = SCHEMA_FILE.read_text(encoding="utf-8-sig")
    # This helper is intentionally limited to the supplied CREATE SCHEMA / TABLE
    # export. It must not run destructive or unrelated SQL from an edited file.
    without_comments = re.sub(r"--.*?$", "", source, flags=re.MULTILINE)
    statements = [statement.strip() for statement in without_comments.split(";") if statement.strip()]
    tables: list[str] = []
    for statement in statements:
        if re.fullmatch(r"CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+production", statement, re.I):
            continue
        match = re.match(r"CREATE\s+TABLE\s+production\.([A-Za-z_][A-Za-z_0-9]*)\s*\(", statement, re.I)
        if not match:
            raise RuntimeError("Schema contains a statement other than CREATE SCHEMA/TABLE for production. Review schema/schema.sql.")
        tables.append(match.group(1))
    if len(tables) != 25 or len(set(tables)) != len(tables):
        raise RuntimeError(f"Expected 25 distinct production tables in schema/schema.sql; found {len(tables)}.")
    return source, tables


def main() -> int:
    try:
        schema_sql, expected_tables = read_and_check_schema()
        load_dotenv(ROOT / ".env")
        config = load_config()
    except (RuntimeError, SystemExit) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(
            config["url"],
            sslmode="require",
            connect_timeout=config["connect_timeout"],
            application_name="supabase-csv-create-tables",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), version()")
                database, version = cur.fetchone()
                cur.execute("""SELECT table_name FROM information_schema.tables
                    WHERE table_schema='production' AND table_type='BASE TABLE'
                    ORDER BY table_name""")
                existing_tables = [row[0] for row in cur.fetchall()]
            conn.commit()

            print(f"Connected to database: {database}")
            print(f"Server: {version.splitlines()[0]}")
            print(f"Tables in schema file: {len(expected_tables)}")
            if existing_tables:
                print("Existing production tables detected; refusing to apply CREATE TABLE statements over them:")
                for name in existing_tables:
                    print(f"  - production.{name}")
                print("Review and reconcile the destination schema before running this script.", file=sys.stderr)
                return 1

            print("This creates the production schema and its tables. It does not import CSV data or drop objects.")
            if input("Type CREATE TABLES to continue: ") != "CREATE TABLES":
                print("Cancelled; no schema changes were made.")
                return 1

            with conn.transaction():
                with conn.cursor() as cur:
                    # No parameters are supplied, so Psycopg sends this DDL script using
                    # PostgreSQL's simple query protocol as one atomic transaction.
                    cur.execute(schema_sql, prepare=False)
                    cur.execute("""SELECT table_name FROM information_schema.tables
                        WHERE table_schema='production' AND table_type='BASE TABLE'""")
                    created_tables = {row[0] for row in cur.fetchall()}
                    missing = set(expected_tables) - created_tables
                    if missing:
                        raise RuntimeError("Schema completed without all expected tables: " + ", ".join(sorted(missing)))
            print(f"Created and verified {len(expected_tables)} production tables. CSV files were not imported.")
            return 0
    except Exception as exc:
        print("ERROR: " + safe_error(exc, config["url"]), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
