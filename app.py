"""Read-only local inventory and movement dashboard for the Supabase database."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import csv
import io
import base64
import calendar
import html
from collections import Counter
from pathlib import Path
import sys

import altair as alt
import psycopg
import qrcode
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrator import load_config, safe_error  # noqa: E402

load_dotenv(ROOT / ".env")
st.set_page_config(page_title="Warehouse Inventory", page_icon="📦", layout="wide")


def fetch_all(statement: str, params: tuple = ()) -> list[dict]:
    """Open a read-only connection for one query; never expose the URL in errors."""
    config = load_config()
    with psycopg.connect(
        config["url"],
        sslmode="require",
        connect_timeout=config["connect_timeout"],
        application_name="supabase-inventory-dashboard",
        options="-c default_transaction_read_only=on",
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            names = [column.name for column in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]


def show_connection_error(exc: Exception) -> None:
    try:
        config = load_config()
        message = safe_error(exc, config["url"])
    except Exception:
        message = "Check that SUPABASE_DB_URL is configured in the project .env file."
    st.error(f"Database connection failed: {message}")


def csv_export(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    if not rows:
        return b""
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()), extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def export_button(label: str, rows: list[dict], filename: str, key: str) -> None:
    if rows:
        st.download_button(label, csv_export(rows), file_name=filename, mime="text/csv", key=key)


def navigate_to_item_tracker() -> None:
    term = st.session_state.get("home_item_search", "").strip()
    if term:
        st.session_state["sidebar_search"] = term
        st.session_state["navigation"] = "Item tracker"


def item_overview() -> None:
    rows = fetch_all("""SELECT
        count(*) FILTER (WHERE status='IN_BOND') AS in_bond_stacks,
        coalesce(sum(qty_pcs) FILTER (WHERE status='IN_BOND'),0) AS in_bond_pieces,
        count(DISTINCT item_code) AS item_codes,
        count(*) FILTER (WHERE status='IN_TRANSIT') AS in_transit_stacks,
        count(*) FILTER (WHERE status='IN_BOND' AND expiry_date < current_date) AS expired_stacks,
        count(*) FILTER (WHERE status='IN_BOND' AND expiry_date >= current_date
                         AND expiry_date < current_date + 31) AS expiring_soon
        FROM production.stacks""")
    summary = rows[0]
    removals = fetch_all("""SELECT count(*) AS open_requests
        FROM production.removal_requests
        WHERE status NOT IN ('COMPLETED','REJECTED')""")[0]["open_requests"]
    recent_count = fetch_all("""SELECT count(*) AS movements
        FROM production.stack_movements
        WHERE moved_at >= now() - interval '7 days'""")[0]["movements"]

    st.markdown("""<style>
    .welcome-panel {
      padding: 1.7rem 1.9rem; border-radius: 16px;
      border: 1px solid rgba(128,128,128,.20);
      border-left: 5px solid var(--primary-color);
      background: linear-gradient(115deg, var(--secondary-background-color), var(--background-color));
      margin: .3rem 0 1.2rem 0;
    }
    .welcome-eyebrow { color: var(--primary-color); font-size: .78rem; font-weight: 700;
      letter-spacing: .12em; text-transform: uppercase; margin-bottom: .35rem; }
    .welcome-title { color: var(--text-color); font-size: clamp(1.8rem, 3vw, 2.6rem);
      line-height: 1.1; font-weight: 750; margin: 0; }
    .welcome-copy { color: var(--text-color); opacity: .78; font-size: 1.02rem;
      margin: .6rem 0 0 0; }
    </style>
    <section class="welcome-panel">
      <div class="welcome-eyebrow">Warehouse overview · live database</div>
      <h1 class="welcome-title">Your stock, in view.</h1>
      <p class="welcome-copy">See what is in bond, where it is stored, and what has moved recently.</p>
    </section>""", unsafe_allow_html=True)

    with st.form("quick_item_search", border=False):
        search_cols = st.columns([4, 1])
        with search_cols[0]:
            st.text_input("Find an item or stack", placeholder="Try Z1000746 or STK-2026-1476", key="home_item_search")
        with search_cols[1]:
            st.markdown("<div style='height: 1.85rem'></div>", unsafe_allow_html=True)
            st.form_submit_button("Track item", type="primary", width="stretch", on_click=navigate_to_item_tracker)

    cards = st.columns(5)
    cards[0].metric("In-bond pieces", f"{summary['in_bond_pieces']:,}")
    cards[1].metric("In-bond stacks", f"{summary['in_bond_stacks']:,}")
    cards[2].metric("Expiring in 30 days", f"{summary['expiring_soon']:,}",
                    delta=f"{summary['expired_stacks']} already expired" if summary["expired_stacks"] else None,
                    delta_color="inverse")
    cards[3].metric("Moves · last 7 days", f"{recent_count:,}")
    cards[4].metric("Open removals", f"{removals:,}")

    status_rows = fetch_all("""SELECT status, count(*) AS stacks,
        coalesce(sum(qty_pcs),0) AS pieces
        FROM production.stacks GROUP BY status ORDER BY status""")
    zone_rows = fetch_all("""SELECT coalesce(c.zone,'Unassigned') AS zone,
        count(*) AS stacks, coalesce(sum(s.qty_pcs),0) AS pieces
        FROM production.stacks s LEFT JOIN production.cells c ON c.id=s.cell_id
        WHERE s.status='IN_BOND' GROUP BY coalesce(c.zone,'Unassigned')
        ORDER BY pieces DESC LIMIT 12""")

    left, right = st.columns(2)
    with left:
        st.subheader("Stock by status")
        st.bar_chart(status_rows, x="status", y="stacks", color="#5487C7", horizontal=True,
                     x_label="Stacks", y_label="Status", height=260)
    with right:
        st.subheader("In-bond pieces by zone")
        st.bar_chart(zone_rows, x="zone", y="pieces", color="#5487C7",
                     x_label="Zone", y_label="Pieces", height=260)

    flow_rows = fetch_all("""WITH flow AS (
        SELECT 'IN'::text AS direction, coalesce(sum(qty_pcs),0)::numeric AS pieces
        FROM production.stacks WHERE entry_date >= current_date - 30 AND entry_date <= current_date
        UNION ALL
        SELECT 'OUT'::text, coalesce(sum(qty_to_remove),0)::numeric
        FROM production.removal_lines WHERE scanned_out_at >= now() - interval '30 days'
    ) SELECT direction, pieces FROM flow ORDER BY direction""")
    st.subheader("Goods in and out · last 30 days")
    st.bar_chart(flow_rows, x="direction", y="pieces", color="direction",
                 x_label="Movement", y_label="Pieces", height=240)
    st.caption("In uses stack entry dates; out uses completed scan-out quantities.")

    lower_left, lower_right = st.columns([1.05, 1.3])
    with lower_left:
        st.subheader("Expiring soon")
        expiry_rows = fetch_all("""SELECT s.stack_code, s.item_code, s.qty_pcs,
            s.expiry_date, c.cell_code, c.zone
            FROM production.stacks s LEFT JOIN production.cells c ON c.id=s.cell_id
            WHERE s.status='IN_BOND' AND s.expiry_date >= current_date
              AND s.expiry_date < current_date + 31
            ORDER BY s.expiry_date, s.stack_code LIMIT 15""")
        if expiry_rows:
            st.dataframe(expiry_rows, hide_index=True, width="stretch")
        else:
            st.success("No in-bond stacks expire in the next 30 days.")
    with lower_right:
        st.subheader("Busiest cells")
        cell_rows = fetch_all("""SELECT cell_code, zone, current_stack_count, max_stacks,
            round(100.0 * current_stack_count / nullif(max_stacks,0),1)::double precision AS capacity_pct
            FROM production.cells WHERE max_stacks > 0
            ORDER BY capacity_pct DESC, current_stack_count DESC LIMIT 12""")
        st.bar_chart(cell_rows, x="cell_code", y="capacity_pct", color="#7B9B72",
                     x_label="Cell", y_label="Capacity used (%)", height=260)
        st.dataframe(cell_rows, hide_index=True, width="stretch")

    st.subheader("Latest activity")
    recent = fetch_all("""SELECT sm.moved_at, s.item_code, s.stack_code,
        fc.cell_code AS from_cell, tc.cell_code AS to_cell, sm.reason
        FROM production.stack_movements sm
        JOIN production.stacks s ON s.id=sm.stack_id
        LEFT JOIN production.cells fc ON fc.id=sm.from_cell_id
        LEFT JOIN production.cells tc ON tc.id=sm.to_cell_id
        ORDER BY sm.moved_at DESC LIMIT 12""")
    st.dataframe(recent, hide_index=True, width="stretch")


def inventory_page() -> None:
    st.title("Inventory")
    options = fetch_all("""SELECT DISTINCT status FROM production.stacks ORDER BY status""")
    statuses = [row["status"] for row in options]
    zones = fetch_all("""SELECT DISTINCT zone FROM production.cells WHERE zone IS NOT NULL ORDER BY zone""")
    zone_options = [row["zone"] for row in zones]
    filters = st.columns([1, 1, 2, 1])
    with filters[0]:
        selected_statuses = st.multiselect("Status", statuses, default=statuses)
    with filters[1]:
        selected_zones = st.multiselect("Zone", zone_options)
    with filters[2]:
        search = st.text_input("Item or stack code", key="inventory_search")
    with filters[3]:
        row_limit = st.selectbox("Rows", [100, 500, 1000, 5000], index=1)

    rows = fetch_all("""SELECT s.stack_code, s.item_code, s.qty_pcs, s.status,
        s.entry_date, s.expiry_date, c.cell_code, c.location_code, c.zone,
        s.scanned_at, s.d365_description
        FROM production.stacks s
        LEFT JOIN production.cells c ON c.id=s.cell_id
        WHERE s.status = ANY(%s)
          AND (cardinality(%s::text[])=0 OR c.zone = ANY(%s::text[]))
          AND (%s='' OR s.item_code ILIKE '%%' || %s || '%%' OR s.stack_code ILIKE '%%' || %s || '%%')
        ORDER BY s.item_code, s.stack_code LIMIT %s""",
        (selected_statuses, selected_zones, selected_zones, search.strip(), search.strip(), search.strip(), row_limit))
    st.caption(f"Showing {len(rows):,} matching stack records.")
    st.dataframe(rows, hide_index=True, width="stretch")
    export_button("Download filtered inventory CSV", rows, "inventory.csv", "inventory-download")


def warehouse_map_page() -> None:
    st.title("Warehouse map")
    st.caption("Cells are positioned from the row and column coordinates in the database. Hover a cell for capacity and location details.")
    zones = fetch_all("""SELECT DISTINCT zone FROM production.cells
        WHERE grid_row IS NOT NULL AND grid_col IS NOT NULL AND zone IS NOT NULL
        ORDER BY zone""")
    zone_options = ["All zones"] + [row["zone"] for row in zones]
    selected_zone = st.selectbox("Zone", zone_options)
    cell_rows = fetch_all("""SELECT cell_code, zone, position_type, grid_row,
        grid_col::double precision AS grid_col, current_stack_count, max_stacks,
        round(100.0 * current_stack_count / nullif(max_stacks,0),1)::double precision AS capacity_pct
        FROM production.cells
        WHERE grid_row IS NOT NULL AND grid_col IS NOT NULL
          AND (%s='All zones' OR zone=%s)
        ORDER BY grid_row, grid_col""", (selected_zone, selected_zone))
    unmapped = fetch_all("""SELECT count(*) AS count FROM production.cells
        WHERE grid_row IS NULL OR grid_col IS NULL""")[0]["count"]
    if not cell_rows:
        st.info("No mapped cells match this zone.")
        return

    chart_data = [{**row, "capacity_pct": float(row["capacity_pct"] or 0)} for row in cell_rows]
    columns_count = len({row["grid_col"] for row in chart_data})
    rows_count = len({row["grid_row"] for row in chart_data})
    base = alt.Chart(alt.Data(values=chart_data)).encode(
        x=alt.X("grid_col:O", title="Grid column", sort="ascending", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("grid_row:O", title="Grid row", sort="descending"),
        color=alt.Color("capacity_pct:Q", title="Capacity used %", scale=alt.Scale(domain=[0, 100], scheme="redyellowgreen", reverse=True)),
        tooltip=["cell_code:N", "zone:N", "position_type:N", "current_stack_count:Q", "max_stacks:Q", "capacity_pct:Q"],
    )
    map_chart = (base.mark_rect(stroke="white", strokeWidth=1) +
                 base.mark_text(fontSize=8, color="#1F2937").encode(text="cell_code:N")).properties(
                     width=max(448, columns_count * 28), height=max(560, rows_count * 28))
    st.altair_chart(map_chart, width="stretch")
    st.caption(f"Mapped cells: {len(cell_rows):,} · cells without map coordinates: {unmapped:,}")

    selected_code = st.selectbox("Inspect cell", [row["cell_code"] for row in cell_rows], key="map_cell")
    selected = next(row for row in cell_rows if row["cell_code"] == selected_code)
    stats = st.columns(4)
    stats[0].metric("Cell", selected["cell_code"])
    stats[1].metric("Zone", selected["zone"] or "—")
    stats[2].metric("Stacks / capacity", f"{selected['current_stack_count']} / {selected['max_stacks']}")
    stats[3].metric("Position type", selected["position_type"])
    occupants = fetch_all("""SELECT s.stack_code, s.item_code, s.qty_pcs, s.status,
        s.expiry_date FROM production.stacks s
        JOIN production.cells c ON c.id=s.cell_id
        WHERE c.cell_code=%s ORDER BY s.item_code, s.stack_code""", (selected_code,))
    st.subheader(f"Stacks in {selected_code}")
    if occupants:
        st.dataframe(occupants, hide_index=True, width="stretch")
    else:
        st.info("No stack records are assigned to this cell.")


def goods_in_page() -> None:
    st.title("Goods-in sessions")
    st.caption("Review arrivals, receiving milestones, containers, lines, and recorded issues.")
    statuses = fetch_all("SELECT DISTINCT status FROM production.goods_in_sessions ORDER BY status")
    status_options = [row["status"] for row in statuses]
    controls = st.columns([1.5, 2, 1])
    with controls[0]:
        selected_statuses = st.multiselect("Session status", status_options, default=status_options, key="goods_status")
    with controls[1]:
        search = st.text_input("Search SAD, PO, GRN, or container", key="goods_search")
    with controls[2]:
        row_limit = st.selectbox("Rows", [50, 100, 250], index=1, key="goods_limit")
    sessions = fetch_all("""SELECT g.id, g.w700_entry_id, g.status, g.po_number, g.ghala_grn, g.d365_grn,
        g.logistics_docs_confirmed, g.scan_in_complete, g.icms_status, g.seal_number,
        g.seal_broken_at, g.grn_request_sent_at, g.notified_at,
        e.sad_reference, e.expiry_date, e.status AS w700_status,
        c.container_number,
        (SELECT count(*) FROM production.w700_lines wl WHERE wl.w700_entry_id=e.id) AS line_count,
        (SELECT count(*) FROM production.goods_in_issues i WHERE i.goods_in_session_id=g.id) AS issue_count
        FROM production.goods_in_sessions g
        JOIN production.w700_entries e ON e.id=g.w700_entry_id
        LEFT JOIN production.w700_containers c ON c.id=g.w700_container_id
        WHERE g.status = ANY(%s)
          AND (%s='' OR concat_ws(' ',e.sad_reference,g.po_number,g.ghala_grn,g.d365_grn,c.container_number) ILIKE '%%' || %s || '%%')
        ORDER BY g.seal_broken_at DESC NULLS LAST, e.stamp_date_time DESC LIMIT %s""",
        (selected_statuses, search.strip(), search.strip(), row_limit))
    st.caption(f"Showing {len(sessions):,} sessions.")
    st.dataframe([{k: v for k, v in row.items() if k not in {"id", "w700_entry_id"}} for row in sessions], hide_index=True, width="stretch")
    export_button("Download goods-in sessions CSV", sessions, "goods_in_sessions.csv", "goods-in-download")
    if not sessions:
        return

    label_by_id = {str(row["id"]): f"{row['sad_reference']} · {row.get('po_number') or 'no PO'} · {row['status']}" for row in sessions}
    selected_id = st.selectbox("Open session details", list(label_by_id), format_func=lambda value: label_by_id[value], key="goods_detail")
    session = next(row for row in sessions if str(row["id"]) == selected_id)
    st.subheader(label_by_id[selected_id])
    metrics = st.columns(4)
    metrics[0].metric("W700 status", session["w700_status"] or "—")
    metrics[1].metric("Container", session["container_number"] or "—")
    metrics[2].metric("W700 lines", session["line_count"])
    metrics[3].metric("Recorded issues", session["issue_count"])
    st.write("**Receiving checks**")
    checks = st.columns(4)
    checks[0].write(f"Logistics documents: {'Complete' if session['logistics_docs_confirmed'] else 'Pending'}")
    checks[1].write(f"Scan-in: {'Complete' if session['scan_in_complete'] else 'Pending'}")
    checks[2].write(f"ICMS: {session['icms_status'] or 'Not recorded'}")
    checks[3].write(f"Seal: {session['seal_number'] or 'Not recorded'}")
    tabs = st.tabs(["W700 lines", "PO lines", "Issues", "Milestones"])
    with tabs[0]:
        lines = fetch_all("""SELECT item_number, hs_code, goods_description, qty_uom,
            authorised_qty, qty_received, qty_remaining, qty_assigned, cif_value_kes
            FROM production.w700_lines WHERE w700_entry_id=%s ORDER BY item_number""", (session["w700_entry_id"],))
        st.dataframe(lines, hide_index=True, width="stretch")
    with tabs[1]:
        po_lines = fetch_all("""SELECT item_number, description, ordered_quantity, created_at
            FROM production.goods_in_po_lines WHERE goods_in_session_id=%s ORDER BY created_at""", (session["id"],))
        st.dataframe(po_lines, hide_index=True, width="stretch")
    with tabs[2]:
        issues = fetch_all("""SELECT issue_type, description, actual_item_code,
            actual_qty_pcs, resolved_at, created_at
            FROM production.goods_in_issues WHERE goods_in_session_id=%s ORDER BY created_at DESC""", (session["id"],))
        if issues:
            st.dataframe(issues, hide_index=True, width="stretch")
        else:
            st.success("No receiving issues recorded for this session.")
    with tabs[3]:
        milestones = [{"Milestone": label, "Time": value} for label, value in [
            ("Seal broken", session["seal_broken_at"]), ("GRN request sent", session["grn_request_sent_at"]),
            ("Notified", session["notified_at"])] if value]
        st.dataframe(milestones, hide_index=True, width="stretch")


def w700_page() -> None:
    st.title("W700 entries")
    st.caption("Review bond entries, customs values, quantities, containers, and linked receiving sessions.")
    status_rows = fetch_all("SELECT DISTINCT status FROM production.w700_entries ORDER BY status")
    statuses = [row["status"] for row in status_rows]
    controls = st.columns([1.2, 2, 1])
    with controls[0]:
        selected_statuses = st.multiselect("W700 status", statuses, default=statuses, key="w700_status")
    with controls[1]:
        search = st.text_input("Search SAD reference, PO, or licence", key="w700_search")
    with controls[2]:
        row_limit = st.selectbox("Rows", [50, 100, 250], index=1, key="w700_limit")
    entries = fetch_all("""SELECT e.id, e.sad_reference, e.status, e.declaration_type,
        e.procedure_code, e.warehouse_id, e.bond_licence_ref, e.stamp_date_time,
        e.expiry_date, e.cif_value_kes, e.po_number, e.importer_pin,
        (SELECT count(*) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS line_count,
        (SELECT count(*) FROM production.w700_containers c WHERE c.w700_entry_id=e.id) AS container_count,
        (SELECT coalesce(sum(l.authorised_qty),0) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS authorised_qty,
        (SELECT coalesce(sum(l.qty_received),0) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS received_qty,
        (SELECT count(*) FROM production.goods_in_sessions g WHERE g.w700_entry_id=e.id) AS session_count
        FROM production.w700_entries e
        WHERE e.status=ANY(%s)
          AND (%s='' OR concat_ws(' ',e.sad_reference,e.po_number,e.bond_licence_ref) ILIKE '%%' || %s || '%%')
        ORDER BY e.stamp_date_time DESC LIMIT %s""", (selected_statuses, search.strip(), search.strip(), row_limit))
    st.caption(f"Showing {len(entries):,} W700 entries.")
    st.dataframe([{k: v for k, v in row.items() if k != "id"} for row in entries], hide_index=True, width="stretch")
    export_button("Download W700 entries CSV", entries, "w700_entries.csv", "w700-download")
    if not entries:
        return
    labels = {str(row["id"]): f"{row['sad_reference']} · {row.get('po_number') or 'no PO'} · {row['status']}" for row in entries}
    selected_id = st.selectbox("Open entry details", list(labels), format_func=lambda value: labels[value], key="w700_detail")
    entry = next(row for row in entries if str(row["id"]) == selected_id)
    st.subheader(labels[selected_id])
    metrics = st.columns(5)
    metrics[0].metric("Expiry", str(entry["expiry_date"]))
    metrics[1].metric("CIF value (KES)", f"{entry['cif_value_kes']:,.2f}")
    metrics[2].metric("Authorised quantity", f"{entry['authorised_qty']:,.2f}")
    metrics[3].metric("Received quantity", f"{entry['received_qty']:,.2f}")
    metrics[4].metric("Containers / sessions", f"{entry['container_count']} / {entry['session_count']}")
    if entry["expiry_date"] < date.today():
        st.error("This W700 entry is past its recorded expiry date.")
    elif entry["expiry_date"] <= date.today() + timedelta(days=30):
        st.warning("This W700 entry expires within 30 days.")
    tabs = st.tabs(["W700 lines", "Containers", "Goods-in sessions", "Entry details"])
    with tabs[0]:
        lines = fetch_all("""SELECT item_number, hs_code, goods_description, qty_uom,
            authorised_qty, qty_received, qty_remaining, qty_assigned, cif_value_kes
            FROM production.w700_lines WHERE w700_entry_id=%s ORDER BY item_number""", (entry["id"],))
        st.dataframe(lines, hide_index=True, width="stretch")
    with tabs[1]:
        containers = fetch_all("""SELECT container_number, created_at FROM production.w700_containers
            WHERE w700_entry_id=%s ORDER BY container_number""", (entry["id"],))
        st.dataframe(containers, hide_index=True, width="stretch")
    with tabs[2]:
        sessions = fetch_all("""SELECT g.status, g.po_number, g.ghala_grn, g.d365_grn,
            g.logistics_docs_confirmed, g.scan_in_complete, g.seal_number,
            g.seal_broken_at, g.grn_request_sent_at, c.container_number
            FROM production.goods_in_sessions g
            LEFT JOIN production.w700_containers c ON c.id=g.w700_container_id
            WHERE g.w700_entry_id=%s ORDER BY g.status""", (entry["id"],))
        st.dataframe(sessions, hide_index=True, width="stretch")
    with tabs[3]:
        details = {key: value for key, value in entry.items() if key not in {"id", "line_count", "container_count", "authorised_qty", "received_qty", "session_count"}}
        st.json(details)


def qr_png(payload: str) -> bytes:
    image = qrcode.make(payload, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=6, border=2)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def printable_stock_cards(rows: list[dict]) -> bytes:
    cards = []
    for row in rows:
        payload = f"STACK:{row['stack_code']}\nITEM:{row['item_code']}\nQTY:{row['qty_pcs']}\nSTATUS:{row['status']}\nCELL:{row.get('cell_code') or ''}"
        image = base64.b64encode(qr_png(payload)).decode("ascii")
        cards.append(f"""<article class="card"><img src="data:image/png;base64,{image}">
        <section><h2>{html.escape(str(row['stack_code']))}</h2>
        <p><b>Item:</b> {html.escape(str(row['item_code']))}</p>
        <p><b>Quantity:</b> {html.escape(str(row['qty_pcs']))} pcs</p>
        <p><b>Status:</b> {html.escape(str(row['status']))}</p>
        <p><b>Cell:</b> {html.escape(str(row.get('cell_code') or '—'))}</p>
        <p><b>Expiry:</b> {html.escape(str(row.get('expiry_date') or '—'))}</p></section></article>""")
    document = """<!doctype html><html><head><meta charset="utf-8"><title>Warehouse stock cards</title>
    <style>body{font:14px Arial,sans-serif;margin:16px;color:#17212b}.cards{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .card{border:1px solid #aab4be;border-radius:10px;padding:12px;display:flex;gap:12px;align-items:center;break-inside:avoid}
    .card img{width:125px;height:125px}.card h2{margin:.1rem 0 .5rem}.card p{margin:.25rem 0}
    @media print{body{margin:8mm}.cards{gap:7mm}.card{border-color:#555}}</style></head>
    <body><h1>Warehouse stock cards</h1><div class="cards">""" + "".join(cards) + "</div></body></html>"
    return document.encode("utf-8")


def stock_cards_page() -> None:
    st.title("Stock cards")
    st.caption("Print or download QR-labelled cards for the stacks in the selected view. QR codes contain stack and item identifiers, quantity, status, and cell.")
    status_rows = fetch_all("SELECT DISTINCT status FROM production.stacks ORDER BY status")
    statuses = [row["status"] for row in status_rows]
    zones = fetch_all("SELECT DISTINCT zone FROM production.cells WHERE zone IS NOT NULL ORDER BY zone")
    zone_options = ["All zones"] + [row["zone"] for row in zones]
    filters = st.columns([1.1, 1.2, 2, 1])
    with filters[0]:
        selected_statuses = st.multiselect("Status", statuses, default=["IN_BOND"] if "IN_BOND" in statuses else statuses, key="cards_status")
    with filters[1]:
        zone = st.selectbox("Zone", zone_options, key="cards_zone")
    with filters[2]:
        term = st.text_input("Item or stack code", key="cards_search")
    with filters[3]:
        limit = st.selectbox("Cards", [12, 24, 48], index=1, key="cards_limit")
    rows = fetch_all("""SELECT s.stack_code, s.item_code, s.qty_pcs, s.status,
        s.entry_date, s.expiry_date, c.cell_code, c.zone, e.sad_reference
        FROM production.stacks s LEFT JOIN production.cells c ON c.id=s.cell_id
        LEFT JOIN production.w700_lines l ON l.id=s.w700_line_id
        LEFT JOIN production.w700_entries e ON e.id=l.w700_entry_id
        WHERE s.status=ANY(%s) AND (%s='All zones' OR c.zone=%s)
          AND (%s='' OR s.item_code ILIKE '%%' || %s || '%%' OR s.stack_code ILIKE '%%' || %s || '%%')
        ORDER BY s.stack_code LIMIT %s""",
        (selected_statuses, zone, zone, term.strip(), term.strip(), term.strip(), limit))
    st.caption(f"Showing {len(rows):,} stock cards. Use the printable HTML download, then print from your browser.")
    if rows:
        st.download_button("Download printable stock cards", printable_stock_cards(rows),
                           file_name="warehouse_stock_cards.html", mime="text/html", key="cards-print")
    columns = st.columns(3)
    for index, row in enumerate(rows):
        payload = f"STACK:{row['stack_code']}\nITEM:{row['item_code']}\nQTY:{row['qty_pcs']}\nSTATUS:{row['status']}\nCELL:{row.get('cell_code') or ''}"
        with columns[index % 3]:
            with st.container(border=True):
                st.image(qr_png(payload), width=120)
                st.markdown(f"**{row['stack_code']}**")
                st.write(f"{row['item_code']} · {row['qty_pcs']} pcs · {row['status']}")
                st.caption(f"Cell {row.get('cell_code') or '—'} · expiry {row['expiry_date']} · {row.get('sad_reference') or 'No SAD'}")


def movement_page() -> None:
    st.title("Cell movement history")
    default_range = (date.today() - timedelta(days=90), date.today())
    filters = st.columns([1.4, 1.2, 1.2, 1])
    with filters[0]:
        period = st.date_input("Movement dates", value=default_range, key="movement_period")
    with filters[1]:
        item_search = st.text_input("Item code contains", key="movement_item")
    with filters[2]:
        stack_search = st.text_input("Stack code contains", key="movement_stack")
    with filters[3]:
        row_limit = st.selectbox("Rows", [100, 500, 1000, 5000], index=1, key="movement_limit")
    if not isinstance(period, tuple) or len(period) != 2:
        st.info("Select both a start and end date.")
        return
    start, end = period
    if start > end:
        st.warning("The start date must be before the end date.")
        return
    start_at = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_before = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    rows = fetch_all("""SELECT sm.moved_at, s.item_code, s.stack_code,
        s.qty_pcs, fc.cell_code AS from_cell, tc.cell_code AS to_cell,
        sm.reason
        FROM production.stack_movements sm
        JOIN production.stacks s ON s.id=sm.stack_id
        LEFT JOIN production.cells fc ON fc.id=sm.from_cell_id
        LEFT JOIN production.cells tc ON tc.id=sm.to_cell_id
        WHERE sm.moved_at >= %s AND sm.moved_at < %s
          AND (%s='' OR s.item_code ILIKE '%%' || %s || '%%')
          AND (%s='' OR s.stack_code ILIKE '%%' || %s || '%%')
        ORDER BY sm.moved_at DESC LIMIT %s""",
        (start_at, end_before, item_search.strip(), item_search.strip(), stack_search.strip(), stack_search.strip(), row_limit))
    st.caption(f"Showing {len(rows):,} physical moves from {start.isoformat()} through {end.isoformat()}.")
    st.dataframe(rows, hide_index=True, width="stretch")
    export_button("Download movement CSV", rows, "cell_movements.csv", "movement-download")


def calendar_page() -> None:
    st.title("Warehouse calendar")
    st.caption("Review expiry dates and operational events: receiving milestones, movements, and removals.")
    month_ref = st.date_input("Choose a month", value=date.today(), key="calendar_month")
    month_start = date(month_ref.year, month_ref.month, 1)
    month_end = date(month_ref.year + (month_ref.month == 12), month_ref.month % 12 + 1, 1)
    events = fetch_all("""SELECT event_date, event_type, reference, details FROM (
        SELECT s.expiry_date AS event_date, 'Stack expiry'::text AS event_type,
            s.stack_code::text AS reference,
            concat(s.item_code,' · ',s.qty_pcs,' pcs · ',s.status) AS details
        FROM production.stacks s WHERE s.status='IN_BOND'
        UNION ALL
        SELECT e.expiry_date, 'W700 expiry'::text, e.sad_reference::text,
            concat('W700 status ',e.status,' · PO ',coalesce(e.po_number,'—'))
        FROM production.w700_entries e WHERE e.status='ACTIVE'
        UNION ALL
        SELECT sm.moved_at::date, 'Cell move'::text, s.stack_code::text,
            concat(s.item_code,' · ',coalesce(fc.cell_code,'?'),' → ',coalesce(tc.cell_code,'?'),' · ',coalesce(sm.reason,'no reason recorded'))
        FROM production.stack_movements sm JOIN production.stacks s ON s.id=sm.stack_id
        LEFT JOIN production.cells fc ON fc.id=sm.from_cell_id
        LEFT JOIN production.cells tc ON tc.id=sm.to_cell_id
        UNION ALL
        SELECT g.seal_broken_at::date, 'Seal broken'::text, e.sad_reference::text,
            concat('Container ',coalesce(c.container_number,'not recorded'),' · PO ',coalesce(g.po_number,'—'))
        FROM production.goods_in_sessions g JOIN production.w700_entries e ON e.id=g.w700_entry_id
        LEFT JOIN production.w700_containers c ON c.id=g.w700_container_id WHERE g.seal_broken_at IS NOT NULL
        UNION ALL
        SELECT g.grn_request_sent_at::date, 'GRN requested'::text, e.sad_reference::text,
            concat('PO ',coalesce(g.po_number,e.po_number,'—'),' · ',g.status)
        FROM production.goods_in_sessions g JOIN production.w700_entries e ON e.id=g.w700_entry_id WHERE g.grn_request_sent_at IS NOT NULL
        UNION ALL
        SELECT rr.created_at::date, 'Removal requested'::text, coalesce(rr.removal_number,rr.id::text),
            concat(rr.removal_type,' · ',rr.status)
        FROM production.removal_requests rr
        UNION ALL
        SELECT rr.completed_at::date, 'Removal completed'::text, coalesce(rr.removal_number,rr.id::text),
            concat(rr.removal_type,' · ',rr.status)
        FROM production.removal_requests rr WHERE rr.completed_at IS NOT NULL
    ) calendar_events WHERE event_date >= %s AND event_date < %s
    ORDER BY event_date,event_type,reference""", (month_start, month_end))
    event_types = sorted({row["event_type"] for row in events})
    chosen_types = st.multiselect("Event types", event_types, default=event_types, key="calendar_types")
    visible_events = [row for row in events if row["event_type"] in chosen_types]
    by_date: dict[date, list[dict]] = {}
    for event in visible_events:
        by_date.setdefault(event["event_date"], []).append(event)

    st.caption(f"{len(visible_events):,} events this month")
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_ref.year, month_ref.month):
        day_columns = st.columns(7)
        for col, day in zip(day_columns, week):
            with col:
                if day.month != month_ref.month:
                    st.markdown("<div style='height:3.3rem'></div>", unsafe_allow_html=True)
                    continue
                day_events = by_date.get(day, [])
                with st.container(border=True):
                    st.markdown(f"**{day.day}**")
                    if day_events:
                        counts = Counter(row["event_type"] for row in day_events)
                        st.caption(f"{len(day_events)} event(s)")
                        st.write(" · ".join(f"{kind}: {count}" for kind, count in counts.items()))
                    else:
                        st.caption("No events")

    day_options = [date(month_ref.year, month_ref.month, day) for day in range(1, calendar.monthrange(month_ref.year, month_ref.month)[1] + 1)]
    chosen_day = st.selectbox("Event details for date", day_options, index=min(date.today().day - 1, len(day_options) - 1), format_func=lambda value: value.strftime("%A, %d %B %Y"), key="calendar_day")
    day_details = by_date.get(chosen_day, [])
    if day_details:
        if any("expiry" in row["event_type"].lower() for row in day_details):
            st.warning("This date has one or more recorded expiry events.")
        st.dataframe(day_details, hide_index=True, width="stretch")
    else:
        st.info("No events are recorded for this date.")
    export_button("Download calendar events CSV", visible_events, f"warehouse_events_{month_start:%Y_%m}.csv", "calendar-download")


def reports_page() -> None:
    st.title("Warehouse reports")
    st.caption("Build a current stock snapshot or a date-bounded operational report, then download it as CSV.")
    report_type = st.selectbox("Report", ["Stock snapshot", "Expiry watch", "Goods-in sessions", "W700 entries", "In / out flow", "Removal activity", "Cell movement ledger"])
    default_range = (date.today() - timedelta(days=90), date.today())
    period = st.date_input("Report date range", value=default_range, key="report_period")
    if not isinstance(period, tuple) or len(period) != 2:
        st.info("Select both a start and end date.")
        return
    start, end = period
    if start > end:
        st.warning("The report start date must be before the end date.")
        return
    start_at = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_before = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
    rows: list[dict]
    if report_type == "Stock snapshot":
        rows = fetch_all("""SELECT s.item_code, s.status, coalesce(c.zone,'Unassigned') AS zone,
            count(*) AS stacks, sum(s.qty_pcs) AS pieces
            FROM production.stacks s LEFT JOIN production.cells c ON c.id=s.cell_id
            GROUP BY s.item_code,s.status,coalesce(c.zone,'Unassigned')
            ORDER BY s.item_code,s.status,zone""")
    elif report_type == "Expiry watch":
        rows = fetch_all("""SELECT s.stack_code,s.item_code,s.qty_pcs,s.status,s.expiry_date,
            c.cell_code,c.zone,e.sad_reference,
            CASE WHEN s.expiry_date < current_date THEN 'EXPIRED'
                 WHEN s.expiry_date < current_date + 31 THEN 'DUE WITHIN 30 DAYS'
                 ELSE 'UPCOMING' END AS expiry_priority
            FROM production.stacks s LEFT JOIN production.cells c ON c.id=s.cell_id
            LEFT JOIN production.w700_lines l ON l.id=s.w700_line_id
            LEFT JOIN production.w700_entries e ON e.id=l.w700_entry_id
            WHERE s.expiry_date BETWEEN %s AND %s
            ORDER BY s.expiry_date,s.stack_code""", (start, end))
    elif report_type == "Goods-in sessions":
        rows = fetch_all("""SELECT g.status,e.sad_reference,coalesce(g.po_number,e.po_number) AS po_number,
            g.ghala_grn,g.d365_grn,c.container_number,g.seal_number,g.icms_status,
            g.logistics_docs_confirmed,g.scan_in_complete,g.seal_broken_at,
            g.grn_request_sent_at,g.notified_at,
            (SELECT count(*) FROM production.goods_in_issues i WHERE i.goods_in_session_id=g.id) AS issue_count
            FROM production.goods_in_sessions g JOIN production.w700_entries e ON e.id=g.w700_entry_id
            LEFT JOIN production.w700_containers c ON c.id=g.w700_container_id
            WHERE e.stamp_date_time >= %s AND e.stamp_date_time < %s
            ORDER BY e.stamp_date_time DESC""", (start_at, end_before))
    elif report_type == "W700 entries":
        rows = fetch_all("""SELECT e.sad_reference,e.status,e.declaration_type,e.procedure_code,
            e.warehouse_id,e.bond_licence_ref,e.stamp_date_time,e.expiry_date,e.po_number,
            e.cif_value_kes,e.total_customs_value_kes,
            (SELECT count(*) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS lines,
            (SELECT coalesce(sum(l.authorised_qty),0) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS authorised_qty,
            (SELECT coalesce(sum(l.qty_received),0) FROM production.w700_lines l WHERE l.w700_entry_id=e.id) AS received_qty
            FROM production.w700_entries e
            WHERE e.stamp_date_time >= %s AND e.stamp_date_time < %s
            ORDER BY e.stamp_date_time DESC""", (start_at, end_before))
    elif report_type == "In / out flow":
        rows = fetch_all("""WITH flow AS (
            SELECT s.entry_date AS event_date,'IN'::text AS flow,s.item_code,
                sum(s.qty_pcs)::numeric AS pieces
            FROM production.stacks s GROUP BY s.entry_date,s.item_code
            UNION ALL
            SELECT rl.scanned_out_at::date,'OUT'::text,s.item_code,
                sum(rl.qty_to_remove)::numeric
            FROM production.removal_lines rl JOIN production.stacks s ON s.id=rl.stack_id
            WHERE rl.scanned_out_at IS NOT NULL GROUP BY rl.scanned_out_at::date,s.item_code
        ) SELECT event_date,flow,item_code,sum(pieces) AS pieces
          FROM flow WHERE event_date BETWEEN %s AND %s
          GROUP BY event_date,flow,item_code ORDER BY event_date,flow,item_code""", (start, end))
    elif report_type == "Removal activity":
        rows = fetch_all("""SELECT rr.removal_number,rr.removal_type,rr.status,rr.created_at,
            rr.kra_email_sent_at,rr.pick_note_printed_at,rr.completed_at,rr.rejection_reason,
            count(rl.id) AS lines,coalesce(sum(rl.qty_to_remove),0) AS requested_pieces,
            count(rl.scanned_out_at) AS scanned_lines
            FROM production.removal_requests rr LEFT JOIN production.removal_lines rl ON rl.removal_request_id=rr.id
            WHERE rr.created_at >= %s AND rr.created_at < %s
            GROUP BY rr.id ORDER BY rr.created_at DESC""", (start_at, end_before))
    else:
        rows = fetch_all("""SELECT sm.moved_at,s.item_code,s.stack_code,s.qty_pcs,
            fc.cell_code AS from_cell,tc.cell_code AS to_cell,sm.reason
            FROM production.stack_movements sm JOIN production.stacks s ON s.id=sm.stack_id
            LEFT JOIN production.cells fc ON fc.id=sm.from_cell_id
            LEFT JOIN production.cells tc ON tc.id=sm.to_cell_id
            WHERE sm.moved_at >= %s AND sm.moved_at < %s
            ORDER BY sm.moved_at DESC""", (start_at, end_before))
    st.subheader(report_type)
    st.caption(f"{len(rows):,} rows · {start.isoformat()} to {end.isoformat()}")
    if report_type == "In / out flow" and rows:
        st.bar_chart(rows, x="event_date", y="pieces", color="flow", x_label="Date", y_label="Pieces", height=320)
    st.dataframe(rows, hide_index=True, width="stretch")
    export_button("Download report CSV", rows, f"{report_type.lower().replace(' ','_').replace('/','and')}.csv", "report-download")


def removals_page() -> None:
    st.title("Removal requests")
    options = fetch_all("SELECT DISTINCT status FROM production.removal_requests ORDER BY status")
    statuses = [row["status"] for row in options]
    filters = st.columns([1.2, 1, 1.5, 1])
    with filters[0]:
        selected = st.multiselect("Request status", statuses, default=statuses, key="removal_status")
    with filters[1]:
        request_type = st.selectbox("Removal type", ["All", "C410", "C400"])
    with filters[2]:
        search = st.text_input("Removal number contains", key="removal_search")
    with filters[3]:
        row_limit = st.selectbox("Rows", [100, 500, 1000], index=0, key="removal_limit")
    rows = fetch_all("""SELECT rr.removal_number, rr.removal_type, rr.status,
        rr.created_at, rr.kra_email_sent_at, rr.pick_note_printed_at,
        rr.completed_at, rr.rejection_reason, count(rl.id) AS line_count,
        coalesce(sum(rl.qty_to_remove),0) AS requested_pieces
        FROM production.removal_requests rr
        LEFT JOIN production.removal_lines rl ON rl.removal_request_id=rr.id
        WHERE rr.status = ANY(%s)
          AND (%s='All' OR rr.removal_type=%s)
          AND (%s='' OR rr.removal_number ILIKE '%%' || %s || '%%')
        GROUP BY rr.id
        ORDER BY rr.created_at DESC LIMIT %s""",
        (selected, request_type, request_type, search.strip(), search.strip(), row_limit))
    st.caption(f"Showing {len(rows):,} matching removal requests.")
    st.dataframe(rows, hide_index=True, width="stretch")
    export_button("Download removal requests CSV", rows, "removal_requests.csv", "removals-download")


def to_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def item_tracker(search: str) -> None:
    st.title("Item and stack movement")
    st.caption("Search by item code (for example Z1000746) or by stack code (for example STK-2026-1476).")
    stacks = fetch_all("""SELECT s.id, s.stack_code, s.item_code, s.qty_pcs, s.status,
        s.entry_date, s.expiry_date, s.scanned_at,
        c.cell_code, c.location_code, c.zone,
        we.sad_reference, we.po_number
        FROM production.stacks s
        LEFT JOIN production.cells c ON c.id=s.cell_id
        LEFT JOIN production.w700_lines wl ON wl.id=s.w700_line_id
        LEFT JOIN production.w700_entries we ON we.id=wl.w700_entry_id
        WHERE upper(s.item_code)=upper(%s) OR upper(s.stack_code)=upper(%s)
        ORDER BY s.stack_code""", (search, search))
    if not stacks:
        st.info(f"No stack records found for `{search}`.")
        return

    item_codes = sorted({row["item_code"] for row in stacks})
    item_rows = fetch_all("""SELECT item_code, item_type_rule, pieces_per_stack,
        supplier_description, d365_description FROM production.items
        WHERE item_code = ANY(%s) ORDER BY item_code""", (item_codes,))
    stack_ids = [str(row["id"]) for row in stacks]
    moves = fetch_all("""SELECT sm.moved_at, s.stack_code, fc.cell_code AS from_cell,
        tc.cell_code AS to_cell, sm.reason
        FROM production.stack_movements sm
        JOIN production.stacks s ON s.id=sm.stack_id
        LEFT JOIN production.cells fc ON fc.id=sm.from_cell_id
        LEFT JOIN production.cells tc ON tc.id=sm.to_cell_id
        WHERE sm.stack_id = ANY(%s::uuid[]) ORDER BY sm.moved_at""", (stack_ids,))
    removals = fetch_all("""SELECT coalesce(rl.scanned_out_at, rr.completed_at, rr.created_at) AS event_at,
        rl.scanned_out_at, s.stack_code, rl.qty_to_remove, rr.removal_number,
        rr.removal_type, rr.status
        FROM production.removal_lines rl
        JOIN production.stacks s ON s.id=rl.stack_id
        JOIN production.removal_requests rr ON rr.id=rl.removal_request_id
        WHERE rl.stack_id = ANY(%s::uuid[])
        ORDER BY coalesce(rl.scanned_out_at, rr.completed_at, rr.created_at)""", (stack_ids,))

    counts = {status: sum(1 for row in stacks if row["status"] == status)
              for status in ("IN_BOND", "IN_TRANSIT", "RELEASED", "EXPORTED")}
    in_bond_pieces = sum(int(row["qty_pcs"] or 0) for row in stacks if row["status"] == "IN_BOND")
    pieces = sum(int(row["qty_pcs"] or 0) for row in stacks)
    moves_by_stack = {}
    for move in moves:
        moves_by_stack[move["stack_code"]] = moves_by_stack.get(move["stack_code"], 0) + 1

    if item_rows:
        item = item_rows[0]
        st.subheader(f"{item['item_code']} — {item.get('supplier_description') or 'Item'}")
        st.caption(f"Type: {item.get('item_type_rule') or '—'} | Pieces per stack: {item.get('pieces_per_stack') or '—'} | Description: {item.get('d365_description') or '—'}")
    else:
        st.subheader(f"Item {item_codes[0]}")
    cards = st.columns(5)
    cards[0].metric("Stacks", f"{len(stacks):,}")
    cards[1].metric("In-bond pieces", f"{in_bond_pieces:,}")
    cards[2].metric("Recorded pieces", f"{pieces:,}")
    cards[3].metric("Cell moves", f"{len(moves):,}")
    cards[4].metric("Removal lines", f"{len(removals):,}")
    st.caption("Recorded pieces include released stacks and are not the current on-hand total.")
    st.write("Status counts: " + " · ".join(f"{status}: {count}" for status, count in counts.items()))

    st.subheader("Stacks and current recorded locations")
    stack_view = [{
        "Stack": row["stack_code"], "Status": row["status"], "Pieces": row["qty_pcs"],
        "Cell": row["cell_code"], "Zone": row["zone"], "Entry date": row["entry_date"],
        "Expiry": row["expiry_date"], "W700 reference": row["sad_reference"], "PO": row["po_number"],
        "Cell moves": moves_by_stack.get(row["stack_code"], 0),
    } for row in stacks]
    st.dataframe(stack_view, hide_index=True, width="stretch")

    events = []
    for row in moves:
        events.append({"_time": to_datetime(row["moved_at"]), "Time": row["moved_at"],
            "Stack": row["stack_code"], "Event": "Cell move",
            "Details": f"{row['from_cell'] or 'unknown'} → {row['to_cell'] or 'unknown'}; {row['reason'] or 'no reason recorded'}"})
    for row in removals:
        label = "Stock removed" if row["scanned_out_at"] else "Removal request"
        details = f"{row['qty_to_remove']} pcs; {row['removal_number'] or 'un-numbered'}; {row['status']}"
        events.append({"_time": to_datetime(row["event_at"]), "Time": row["event_at"],
            "Stack": row["stack_code"], "Event": label, "Details": details})
    events.sort(key=lambda row: row["_time"])
    st.subheader("Movement and removal timeline")
    if events:
        limit = st.slider("Timeline events", min_value=10, max_value=min(500, max(10, len(events))), value=min(100, max(10, len(events))))
        visible = events[-limit:]
        st.dataframe([{k: v for k, v in row.items() if k != "_time"} for row in reversed(visible)], hide_index=True, width="stretch")
    else:
        st.info("No cell movements or removal lines are recorded for these stacks.")


def main() -> None:
    st.markdown("""<style>
    [data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,.16); }
    </style>""", unsafe_allow_html=True)
    st.session_state.setdefault("navigation", "Overview")
    st.session_state.setdefault("sidebar_search", "Z1000746")
    with st.sidebar:
        st.title("Warehouse")
        page = st.radio("View", ["Overview", "Warehouse map", "Inventory", "Goods in", "W700 entries", "Stock cards", "Item tracker", "Cell movements", "Removal requests", "Calendar", "Reports"], key="navigation")
        search = st.text_input("Item or stack code", disabled=page != "Item tracker", key="sidebar_search")
        if st.button("Refresh data", width="stretch"):
            st.rerun()
        st.caption("Connected to the production database with read-only transactions.")

    try:
        if page == "Overview":
            item_overview()
        elif page == "Warehouse map":
            warehouse_map_page()
        elif page == "Inventory":
            inventory_page()
        elif page == "Goods in":
            goods_in_page()
        elif page == "W700 entries":
            w700_page()
        elif page == "Stock cards":
            stock_cards_page()
        elif page == "Calendar":
            calendar_page()
        elif page == "Reports":
            reports_page()
        else:
            if page == "Item tracker":
                term = search.strip()
                if term:
                    item_tracker(term)
                else:
                    st.info("Enter an item code or stack code to search.")
            elif page == "Cell movements":
                movement_page()
            else:
                removals_page()
    except Exception as exc:
        show_connection_error(exc)


if __name__ == "__main__":
    main()

