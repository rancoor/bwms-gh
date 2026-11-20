import argparse
from pathlib import Path
from scripts.migrator import Migrator, load_config

def main():
    parser = argparse.ArgumentParser(
        description="Import CSV files from data/ into a Supabase PostgreSQL database."
    )
    parser.add_argument("--data-dir", default="data", help="Folder containing CSV files (default: data)")
    parser.add_argument("--schema-file", default="schema/schema.sql", help="Schema SQL file")
    parser.add_argument("--report-dir", default="reports", help="Folder for reports")
    parser.add_argument("--apply-schema", action="store_true", help="Apply reviewed schema SQL transactionally before importing")
    parser.add_argument("--dry-run", action="store_true", help="Inspect mappings and dependencies; do not import")
    parser.add_argument("--truncate", action="store_true", help="Replace rows in matched tables after explicit confirmation (RESTRICT; transactional)")
    parser.add_argument("--validate-only", action="store_true", help="Validate the current database and CSV plan without importing")
    parser.add_argument("--resume", action="store_true", help="Skip only tables whose supplied data exactly matches (requires primary keys)")
    args = parser.parse_args()

    if args.truncate and args.dry_run:
        parser.error("--truncate cannot be combined with --dry-run")
    if args.dry_run and args.apply_schema:
        parser.error("--dry-run cannot be combined with --apply-schema because applying schema changes the database")
    if args.validate_only and (args.truncate or args.apply_schema):
        parser.error("--validate-only cannot be combined with --truncate or --apply-schema")

    config = load_config()
    migrator = Migrator(config, Path(args.data_dir), Path(args.schema_file), Path(args.report_dir))
    try:
        migrator.run(apply_schema=args.apply_schema, dry_run=args.dry_run, truncate=args.truncate,
                     validate_only=args.validate_only, resume=args.resume)
    except RuntimeError:
        return 1

if __name__ == "__main__":
    main()
