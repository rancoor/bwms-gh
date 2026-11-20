"""Show stock, cell movements, and removal history for an item from exported CSVs."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_DATE = datetime.min.replace(tzinfo=timezone.utc)


def read_csv(data_dir: Path, filename: str, required: set[str]) -> list[dict[str, str]]:
    path = data_dir / filename
    if not path.is_file():
        raise FileNotFoundError(f"Required export not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        headers = set(reader.fieldnames or [])
        missing = required - headers
        if missing:
            raise ValueError(f"{filename} is missing required columns: {', '.join(sorted(missing))}")
        return list(reader)


def parse_date(value: str | None) -> datetime:
    if not value:
        return MIN_DATE
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return MIN_DATE


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace an item through exported stack movement and removal CSVs.")
    parser.add_argument("item_code", nargs="?", default="Z1000746", help="Item code to trace (default: Z1000746)")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Folder containing production.*.csv exports")
    parser.add_argument("--limit", type=int, default=50, help="Maximum timeline events to display (default: 50; use 0 for all)")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or greater")

    try:
        stacks = read_csv(args.data_dir, "production.stacks.csv", {"id", "stack_code", "item_code", "qty_pcs", "status", "cell_id", "entry_date"})
        movements = read_csv(args.data_dir, "production.stack_movements.csv", {"stack_id", "from_cell_id", "to_cell_id", "reason", "moved_at"})
        removals = read_csv(args.data_dir, "production.removal_lines.csv", {"stack_id", "removal_request_id", "qty_to_remove", "scanned_out_at"})
        cells = read_csv(args.data_dir, "production.cells.csv", {"id", "cell_code"})
        requests = read_csv(args.data_dir, "production.removal_requests.csv", {"id", "removal_number", "removal_type", "status", "created_at", "completed_at"})
    except (OSError, csv.Error, ValueError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")

    item_code = args.item_code.strip().upper()
    matches = [row for row in stacks if row.get("item_code", "").strip().upper() == item_code]
    if not matches:
        print(f"No stacks for item {item_code} were found in {args.data_dir}.")
        return 0

    cells_by_id = {row["id"]: row["cell_code"] for row in cells}
    requests_by_id = {row["id"]: row for row in requests}
    stack_by_id = {row["id"]: row for row in matches}
    events: list[tuple[datetime, str, str, str]] = []
    movement_counts: Counter[str] = Counter()
    removal_counts: Counter[str] = Counter()

    for stack_id, stack in stack_by_id.items():
        code = stack["stack_code"]
        events.append((parse_date(stack.get("entry_date")), "Stock entry", code,
                       f"{stack.get('qty_pcs', '?')} pcs; recorded status {stack.get('status', '?')}"))

    for movement in movements:
        stack = stack_by_id.get(movement.get("stack_id", ""))
        if not stack:
            continue
        movement_counts[stack["stack_code"]] += 1
        source = cells_by_id.get(movement.get("from_cell_id", ""), "unknown cell")
        destination = cells_by_id.get(movement.get("to_cell_id", ""), "unknown cell")
        reason = movement.get("reason") or "no reason recorded"
        events.append((parse_date(movement.get("moved_at")), "Cell move", stack["stack_code"],
                       f"{source} -> {destination}; {reason}"))

    for removal in removals:
        stack = stack_by_id.get(removal.get("stack_id", ""))
        if not stack:
            continue
        request = requests_by_id.get(removal.get("removal_request_id", ""), {})
        removal_counts[stack["stack_code"]] += 1
        timestamp = removal.get("scanned_out_at") or request.get("completed_at") or request.get("created_at", "")
        event_type = "Stock removed" if removal.get("scanned_out_at") else "Removal request"
        ref = request.get("removal_number") or removal.get("removal_request_id", "unknown request")
        status = request.get("status", "unknown status")
        events.append((parse_date(timestamp), event_type, stack["stack_code"],
                       f"{removal.get('qty_to_remove', '?')} pcs; {ref}; {status}"))

    statuses = Counter(row.get("status", "UNKNOWN") for row in matches)
    in_bond = [row for row in matches if row.get("status") == "IN_BOND"]
    total_pieces = sum(int(row.get("qty_pcs") or 0) for row in matches)
    in_bond_pieces = sum(int(row.get("qty_pcs") or 0) for row in in_bond)
    print(f"Item: {item_code}")
    print(f"Stacks in export: {len(matches)} | historical pieces across these stacks: {total_pieces}")
    print(f"Currently marked IN_BOND: {len(in_bond)} stacks | {in_bond_pieces} pcs")
    print("Stack status: " + ", ".join(f"{status}={count}" for status, count in sorted(statuses.items())))
    print(f"Physical cell moves: {sum(movement_counts.values())} | removal lines: {sum(removal_counts.values())}")
    print("\nStacks:")
    for stack in sorted(matches, key=lambda row: row.get("stack_code", "")):
        cell = cells_by_id.get(stack.get("cell_id", ""), "unknown cell")
        print(f"  {stack.get('stack_code')} | {stack.get('status')} | {stack.get('qty_pcs')} pcs | current cell {cell} | entry {stack.get('entry_date')}")

    events.sort(key=lambda item: item[0])
    shown = events if args.limit == 0 else events[-args.limit:]
    print(f"\nTimeline (latest {len(shown)} of {len(events)} events, chronological order):")
    for timestamp, event_type, stack_code, detail in shown:
        date_text = timestamp.isoformat(sep=" ") if timestamp != MIN_DATE else "date unknown"
        print(f"  {date_text} | {stack_code} | {event_type} | {detail}")
    if len(shown) < len(events):
        print(f"  ... {len(events) - len(shown)} earlier event(s) omitted; use --limit 0 to show all.")
    print("\nData source: local CSV exports. This is a snapshot, not a live database query.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
