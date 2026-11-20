# Supabase CSV Migrator

Import CSV exports into PostgreSQL/Supabase and inspect warehouse inventory with a local dashboard. The migrator discovers destination tables, previews CSV compatibility, orders imports around foreign keys, streams data through Psycopg 3, and writes reports. The dashboard reads live inventory, stack movements, and removals in read-only mode.

## Requirements

- Python 3.11 or newer
- A destination database URL with a database password (direct or Supabase Session Pooler)

The destination database must already contain the tables and constraints, unless you explicitly use `--apply-schema`. Schema application runs over the same Psycopg connection and transaction as the migration, and refuses destructive SQL and Supabase-managed schemas. Existing tables can conflict with `schema/schema.sql`; review those conflicts and reconcile the schema manually. The tool never drops tables or schemas.

## Setup in Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `SUPABASE_DB_URL` using the connection string shown in Supabase **Project Settings â†’ Database**. Use `sslmode=require`. If the database password contains reserved URL characters such as `@`, `:`, `/`, or `#`, URL-encode the password before putting it in the URL. Session Pooler URLs are supported; copy the exact host, port, and username from Supabase.

Put exports in `data/`. The filename should be `table.csv` when the table name is unambiguous, or `schema.table.csv` otherwise. The schema SQL file is `schema/schema.sql`.

## Create the destination tables

To create the tables without starting a CSV import, run:

```powershell
python scripts/create_tables.py
```

The script reads `schema/schema.sql`, connects using `.env`, and displays the destination database. It stops if it finds existing tables in the `production` schema. Type `CREATE TABLES` at its prompt to apply the 25 table definitions in one transaction. If any statement fails, the schema changes roll back. This command does not import CSV data.

After it completes, preview the CSV mapping with `python run.py --dry-run`. The schema references Supabase `auth.users`; rows in `production.user_profiles.csv` need matching Auth users already present in the destination.

## Commands

```powershell
# Connect, inspect destination metadata, parse every CSV, and show a plan.
python run.py --dry-run

# Apply reviewed schema first, then import.
python run.py --apply-schema

# Import after the schema already exists.
python run.py

# Compare source counts and validate foreign keys without changing rows.
python run.py --validate-only

# Retry only when destination table contents exactly match the corresponding CSV.
python run.py --resume

# Replace data in matched tables after typing TRUNCATE when prompted.
python run.py --truncate

python run.py --help
```

`--dry-run` cannot be combined with `--apply-schema`. `--truncate` affects only tables with matched CSVs and uses `RESTRICT`, so it stops if an unmatched table depends on one of them. It runs in the same transaction as the import; failed validation rolls back the replacement. `--resume` requires primary keys and compares all supplied CSV columns to the destination before skipping. Existing rows that do not match cause a failure; no automatic merge is attempted.

## Inspect item movement

To trace an item across the local exports, run:

```powershell
python scripts/track_item_movement.py Z1000746
```

The report joins matching stacks with `stack_movements`, cell names, and `removal_lines`. It prints a snapshot summary and up to 50 recent events. Use `--limit 0` to show the full timeline. It reads the CSV files only and does not need a database connection.

## Run the inventory dashboard

With the tables and uploaded data in the destination, start the local dashboard from the project folder:

```powershell
python run_dashboard.py
```

It opens a read-only bonded-warehouse dashboard. Views include:

- **Warehouse map:** interactive cell grid with zone filtering, capacity coloring, and cell occupants.
- **Goods in:** session search and filters with W700 lines, PO lines, receiving issues, containers, and milestones.
- **W700 entries:** entry search and detail tabs for declarations, quantities, containers, and linked receiving sessions, with expiry alerts.
- **Stock cards:** filtered stack cards with QR codes and a printable HTML download.
- **Calendar:** expiry, receiving, movement, and removal events by month, with date details and CSV export.
- **Reports:** stock snapshot, expiry watch, goods-in, W700, in/out flow, removals, and cell movement reports, with CSV downloads.
- **Inventory, item tracking, cell movements, and removal requests:** searchable operational views with CSV exports where applicable.

The dashboard uses read-only database transactions. It does not create or update warehouse records. The database connection uses `.env`; database errors are redacted before display.

## CSV and data behavior

- CSV uses UTF-8 (with or without BOM), a header row, and standard quoted CSV syntax. Quoted commas and embedded newlines are supported.
- Header names are case-sensitive. Unknown columns, missing required columns, malformed rows, invalid encoding, and duplicate primary keys stop the migration before data changes.
- A valid header-only CSV represents zero rows. An empty file without a header is an error.
- The configured `NULL_MARKER` is converted to SQL NULL. The default is `\N`; blank fields remain empty strings. Choose a marker that cannot occur as actual text.
- Generated columns are omitted from import. Supplied primary-key and identity values are preserved.
- CSV rows are parsed and streamed; the complete data file is not loaded into memory. Primary-key duplicate detection does retain key values in memory.
- Parent tables are imported before children. Cycles are accepted only when all constraints involved are deferrable. Foreign-key integrity is checked after import.
- Serial and identity sequences are reset after import, including empty tables.

The current schema references Supabase `auth.users` from `production.user_profiles`. Matching Auth users must already exist. A schema and CSV migration does not copy Auth credentials, RLS policies, Storage objects, Edge Functions, secrets, grants, or project settings.

## Reports and security

Each run writes `reports/migration_summary.json`, `reports/migration_summary.html`, and `reports/migration_errors.log`. Reports include status, timestamps, source and destination counts, keys, durations, relationship checks, warnings, and errors. Connection failures are redacted before being printed or written.

Keep `.env` private. Do not commit database URLs or share a project folder containing credentials. The migration connects as a database user, so choose a destination project/user with only the privileges needed for the reviewed schema and data import.

## Tests

Run the local, database-independent checks with:

```powershell
python -m unittest discover -s tests -v
```

The automated suite covers CSV parsing and validation, primary-key duplicates, dependency ordering/cycles, and connection error redaction. It does not claim to replace a live PostgreSQL integration run.

