"""Safe, streaming CSV migration to PostgreSQL."""
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import load_dotenv
try:
    from rich.console import Console
    from rich.table import Table
    _console = Console()
except ImportError:  # Keep diagnostics usable before dependencies are installed.
    _console = None

SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast", "auth", "storage", "extensions", "realtime", "vault", "graphql", "graphql_public", "supabase_migrations"}


def load_config():
    load_dotenv()
    url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not url:
        raise SystemExit("ERROR: SUPABASE_DB_URL is missing. Copy .env.example to .env and configure it.")
    if not url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("ERROR: SUPABASE_DB_URL must be a postgresql:// or postgres:// connection URL.")
    try:
        timeout = int(os.getenv("CONNECT_TIMEOUT", "15"))
        if timeout < 1:
            raise ValueError
    except ValueError:
        raise SystemExit("ERROR: CONNECT_TIMEOUT must be a positive integer.")
    return {"url": url, "null_marker": os.getenv("NULL_MARKER", r"\N"), "connect_timeout": timeout}


def qname(schema, table):
    return f"{schema}.{table}"


def safe_error(exc, url):
    """Redact URL userinfo from libpq errors before writing logs/reports."""
    message = str(exc)
    # libpq may report a malformed password suffix as part of the "host" token.
    message = re.sub(r"failed to resolve host '[^']+'", "failed to resolve the configured database host (check hostname and URL-encode special password characters)", message, flags=re.I)
    message = re.sub(r"(could not translate host name|could not translate host|connection to host)\s+'[^']+'", r"\1 '<configured-host>'", message, flags=re.I)
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if parts.netloc and "@" in parts.netloc:
            message = message.replace(parts.netloc, "<redacted-host-info>")
        if parts.password:
            message = message.replace(parts.password, "<redacted>")
    except Exception:
        pass
    message = re.sub(r"(postgres(?:ql)?://)[^\s/@]+:[^\s/@]+@", r"\1<redacted>@", message, flags=re.I)
    return message


class Migrator:
    def __init__(self, config, data_dir, schema_file, report_dir):
        self.config, self.data_dir, self.schema_file, self.report_dir = config, Path(data_dir), Path(schema_file), Path(report_dir)
        self.report = {"started_at": datetime.now(timezone.utc).isoformat(), "database": None,
                       "server_version": None, "tables": [], "errors": [], "warnings": []}

    def connect(self):
        return psycopg.connect(self.config["url"], connect_timeout=self.config["connect_timeout"],
                               application_name="supabase-csv-migrator", sslmode="require")

    def apply_schema(self, conn):
        if not self.schema_file.is_file():
            raise RuntimeError(f"Schema file not found: {self.schema_file}")
        text = self.schema_file.read_text(encoding="utf-8-sig")
        if re.search(r"\b(DROP\s+(SCHEMA|TABLE|DATABASE)|TRUNCATE\s+|DELETE\s+FROM)\b", text, re.I):
            raise RuntimeError("Schema file contains destructive statements. Remove them and review the schema before applying.")
        if re.search(r"\b(CREATE|ALTER)\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:auth|storage|extensions|realtime|vault|graphql|graphql_public)\b", text, re.I):
            raise RuntimeError("Schema file targets a Supabase-managed schema; refusing to apply it.")
        try:
            # No external client process means the credential never appears in a process
            # command line. Psycopg uses PostgreSQL's simple query protocol for this script.
            with conn.cursor() as cur:
                cur.execute(text, prepare=False)
        except psycopg.Error as exc:
            raise RuntimeError("Schema application failed and was rolled back: " + safe_error(exc, self.config["url"])) from None

    def discover_tables(self, conn):
        with conn.cursor() as cur:
            cur.execute("""SELECT table_schema,table_name FROM information_schema.tables
                WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema')""")
            tables = {(a, b) for a, b in cur.fetchall()}
            cur.execute("""SELECT n.nspname,c.relname,a.attname,k.ord
                FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN LATERAL unnest(con.conkey) WITH ORDINALITY k(attnum,ord) ON true
                JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
                WHERE con.contype='p' ORDER BY n.nspname,c.relname,k.ord""")
            pks = defaultdict(list)
            for s, t, c, _ in cur.fetchall(): pks[(s, t)].append(c)
            cur.execute("""SELECT ns.nspname,ct.relname,a.attname,nr.nspname,cr.relname,ar.attname,
                       con.conname,k.ord,con.condeferrable
                FROM pg_constraint con JOIN pg_class ct ON ct.oid=con.conrelid
                JOIN pg_namespace ns ON ns.oid=ct.relnamespace
                JOIN pg_class cr ON cr.oid=con.confrelid JOIN pg_namespace nr ON nr.oid=cr.relnamespace
                JOIN LATERAL unnest(con.conkey,con.confkey) WITH ORDINALITY k(ca,pa,ord) ON true
                JOIN pg_attribute a ON a.attrelid=ct.oid AND a.attnum=k.ca
                JOIN pg_attribute ar ON ar.attrelid=cr.oid AND ar.attnum=k.pa
                WHERE con.contype='f' ORDER BY ns.nspname,ct.relname,con.conname,k.ord""")
            parts = defaultdict(list); deferred = {}
            for s,t,c,rs,rt,rc,cn,ord_,deferrable in cur.fetchall():
                key=(s,t,cn,rs,rt); parts[key].append((ord_,c,rc)); deferred[key]=deferrable
            fks=defaultdict(list)
            for (s,t,cn,rs,rt), values in parts.items():
                values.sort(); fks[(s,t)].append({"columns":[x[1] for x in values],"ref_schema":rs,
                    "ref_table":rt,"ref_columns":[x[2] for x in values],"constraint":cn,"deferrable":deferred[(s,t,cn,rs,rt)]})
            cur.execute("""SELECT table_schema,table_name,column_name,is_nullable,column_default,
                is_identity,is_generated,ordinal_position FROM information_schema.columns
                ORDER BY table_schema,table_name,ordinal_position""")
            cols=defaultdict(list)
            for s,t,c,nullable,default,identity,generated,pos in cur.fetchall():
                cols[(s,t)].append({"name":c,"nullable":nullable=="YES","default":default,
                                    "identity":identity=="YES","generated":generated!="NEVER"})
        return tables,pks,fks,cols

    def map_csvs(self, tables):
        if not self.data_dir.is_dir(): raise RuntimeError(f"Data folder not found: {self.data_dir}")
        mapping={}; unmatched=[]
        for path in sorted(self.data_dir.rglob("*.csv")):
            stem=path.stem
            if "." in stem:
                schema,table=stem.split(".",1); candidate=(schema,table)
                if candidate in tables: mapping[candidate]=path
                else: unmatched.append(path.name)
            else:
                matches=[x for x in tables if x[1]==stem]
                if len(matches)==1: mapping[matches[0]]=path
                elif len(matches)>1: raise RuntimeError(f"Ambiguous CSV {path.name}; use schema.{stem}.csv.")
                else: unmatched.append(path.name)
        if unmatched: self.report["warnings"].append({"unmatched_csv_files":unmatched})
        if not mapping: raise RuntimeError(f"No matching CSV files found in {self.data_dir}")
        return mapping

    def dependency_order(self, mapping, fks):
        nodes=set(mapping); indegree={n:0 for n in nodes}; children=defaultdict(set)
        for child in nodes:
            for fk in fks.get(child,[]):
                parent=(fk["ref_schema"],fk["ref_table"])
                if parent in nodes and child not in children[parent]: children[parent].add(child); indegree[child]+=1
        queue=deque(sorted(n for n,d in indegree.items() if d==0)); order=[]
        while queue:
            n=queue.popleft(); order.append(n)
            for child in sorted(children[n]):
                indegree[child]-=1
                if indegree[child]==0: queue.append(child)
        if len(order)!=len(nodes):
            cycle=[qname(*n) for n,d in indegree.items() if d]
            cycle_fks=[fk for child in nodes for fk in fks.get(child,[]) if (fk["ref_schema"],fk["ref_table"]) in nodes and indegree[child]]
            if cycle_fks and all(fk["deferrable"] for fk in cycle_fks):
                self.report["warnings"].append({"deferred_cycle":cycle})
                return sorted(nodes)
            raise RuntimeError("Non-deferrable foreign-key dependency cycle: " + ", ".join(cycle) + ". Make cycle constraints DEFERRABLE or import through a reviewed staging process.")
        return order

    def analyze_csv(self, path, target_cols):
        f = None
        try:
            f=path.open("r",encoding="utf-8-sig",newline="")
            reader=csv.reader(f,strict=True)
            header=next(reader)
            if not header or any(not x for x in header) or len(header)!=len(set(header)): raise RuntimeError("header has blank or duplicate column names")
            names={c["name"]:c for c in target_cols}; unknown=sorted(set(header)-set(names))
            if unknown: raise RuntimeError(f"unexpected CSV columns: {unknown}")
            missing=[c["name"] for c in target_cols if not c["generated"] and c["name"] not in header and not c["nullable"] and c["default"] is None and not c["identity"]]
            if missing: raise RuntimeError(f"missing required destination columns: {missing}")
            generated=[c for c in header if names[c]["generated"]]
            if generated: self.report["warnings"].append({"file":path.name,"generated_columns_ignored":generated})
            import_cols=[c for c in header if not names[c]["generated"]]
            seen=set(); count=0; dupes=0; pks=[c["name"] for c in target_cols if c.get("primary_key")]
            # Empty files containing only a valid header are a valid zero-row import.
            for rownum,row in enumerate(reader,2):
                if len(row)!=len(header): raise RuntimeError(f"row {rownum} has {len(row)} fields; header has {len(header)}")
                count+=1
            f.seek(0); reader=csv.reader(f,strict=True); next(reader)
            # Duplicate key checking is streamed and bounded by key-set size.
            if pks and all(pk in header for pk in pks):
                offsets=[header.index(pk) for pk in pks]
                for row in reader:
                    key=tuple(row[i] for i in offsets)
                    if key in seen: dupes+=1
                    seen.add(key)
            f.close()
            if dupes: raise RuntimeError(f"CSV contains {dupes} duplicate primary-key row(s)")
            return {"path":path,"header":header,"columns":import_cols,"source_row_count":count}
        except UnicodeDecodeError as exc: raise RuntimeError(f"{path.name} is not valid UTF-8 (BOM is supported): {exc}") from exc
        except csv.Error as exc: raise RuntimeError(f"{path.name} has malformed CSV: {exc}") from exc
        except StopIteration: raise RuntimeError(f"{path.name} is empty; expected a header row.")
        finally:
            if f is not None and not f.closed: f.close()

    def validate_csv_mapping(self,mapping,cols,pks):
        plans={}
        for table,path in mapping.items():
            for c in cols[table]: c["primary_key"]=c["name"] in pks.get(table,[])
            plans[table]=self.analyze_csv(path,cols[table])
        return plans

    def source_rows(self, plan):
        """Parse/re-encode rows so generated source columns can be removed safely."""
        with plan["path"].open("r",encoding="utf-8-sig",newline="") as f:
            reader=csv.reader(f,strict=True); header=next(reader)
            indexes=[header.index(c) for c in plan["columns"]]
            for row in reader:
                values=[row[i] for i in indexes]
                yield [None if value==self.config["null_marker"] else value for value in values]

    def import_table(self,conn,table,plan,replace):
        schema,name=table; columns=plan["columns"]
        with conn.cursor() as cur:
            if replace:
                cur.execute(sql.SQL("DELETE FROM {} ").format(sql.Identifier(schema,name)))
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(schema,name),sql.SQL(",").join(map(sql.Identifier,columns)))) as copy:
                for row in self.source_rows(plan): copy.write_row(row)
            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema,name)))
            return cur.fetchone()[0]

    def matches_existing(self,conn,table,plan):
        """Compare the complete supplied column set through a typed temporary table."""
        schema,name=table; columns=plan["columns"]
        temp=f"migrator_resume_{abs(hash(table))}"
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP").format(sql.Identifier(temp),sql.Identifier(schema,name)))
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(sql.Identifier(temp),sql.SQL(",").join(map(sql.Identifier,columns)))) as copy:
                for row in self.source_rows(plan): copy.write_row(row)
            cols=sql.SQL(",").join(map(sql.Identifier,columns))
            cur.execute(sql.SQL("""SELECT
                NOT EXISTS (SELECT {} FROM {} EXCEPT SELECT {} FROM {}) AND
                NOT EXISTS (SELECT {} FROM {} EXCEPT SELECT {} FROM {})""").format(
                cols,sql.Identifier(temp),cols,sql.Identifier(schema,name),
                cols,sql.Identifier(schema,name),cols,sql.Identifier(temp)))
            return cur.fetchone()[0]

    def validate_relationships(self,conn,order,fks):
        results=[]
        with conn.cursor() as cur:
            for child in order:
                for fk in fks.get(child,[]):
                    parent=(fk["ref_schema"],fk["ref_table"])
                    equals=sql.SQL(" AND ").join(sql.SQL("p.{}=c.{}").format(sql.Identifier(pc),sql.Identifier(cc)) for cc,pc in zip(fk["columns"],fk["ref_columns"]))
                    present=sql.SQL(" AND ").join(sql.SQL("c.{} IS NOT NULL").format(sql.Identifier(cc)) for cc in fk["columns"])
                    cur.execute(sql.SQL("SELECT count(*) FROM {} c WHERE ({}) AND NOT EXISTS (SELECT 1 FROM {} p WHERE {})").format(sql.Identifier(*child),present,sql.Identifier(*parent),equals))
                    count=cur.fetchone()[0]; results.append({"table":qname(*child),"constraint":fk["constraint"],"orphan_count":count})
                    if count: raise RuntimeError(f"FK {fk['constraint']} on {qname(*child)} has {count} orphan row(s).")
        return results

    def write_report(self):
        self.report["finished_at"]=datetime.now(timezone.utc).isoformat()
        self.report_dir.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path=self.report_dir/"migration_summary.json"
        json_path.write_text(json.dumps(self.report,indent=2,default=str),encoding="utf-8")
        rows=[]
        for item in self.report["tables"]:
            rows.append("<tr>"+"".join(f"<td>{html.escape(str(item.get(k,'')))}</td>" for k in ("file","table","source_row_count","imported_row_count","status","duration_seconds"))+"</tr>")
        page="<!doctype html><meta charset='utf-8'><title>Migration report</title><h1>Migration report</h1><p>Status: "+html.escape(str(self.report.get("status","unknown")))+"</p><table border='1'><tr><th>File</th><th>Table</th><th>Source rows</th><th>Imported rows</th><th>Status</th><th>Seconds</th></tr>"+"".join(rows)+"</table><h2>Errors</h2><pre>"+html.escape("\n".join(self.report["errors"]))+"</pre><h2>Warnings</h2><pre>"+html.escape(json.dumps(self.report["warnings"],indent=2))+"</pre>"
        (self.report_dir/"migration_summary.html").write_text(page,encoding="utf-8")
        (self.report_dir/"migration_errors.log").write_text("\n".join(self.report["errors"]),encoding="utf-8")
        print(f"Report saved: {json_path}")

    def run(self,apply_schema=False,dry_run=False,truncate=False,validate_only=False,resume=False):
        try:
            print("Connecting to destination Supabase...")
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_database(),version()")
                    self.report["database"],self.report["server_version"]=cur.fetchone()
                print(f"Connected to {self.report['database']} ({self.report['server_version'].splitlines()[0]}).")
                if apply_schema: self.apply_schema(conn)
                tables,pks,fks,cols=self.discover_tables(conn); mapping=self.map_csvs(tables)
                plans=self.validate_csv_mapping(mapping,cols,pks); order=self.dependency_order(mapping,fks)
                plan_table = Table(title="Migration plan") if _console else None
                if plan_table:
                    plan_table.add_column("Destination table")
                    plan_table.add_column("CSV file")
                    plan_table.add_column("Source rows", justify="right")
                for table in order:
                    plan=plans[table]
                    item={"table":qname(*table),"file":plan["path"].name,"columns":plan["columns"],
                          "source_row_count":plan["source_row_count"],"primary_key":pks.get(table,[]),
                          "foreign_keys":fks.get(table,[]),"status":"planned","imported_row_count":0,"failed_row_count":0}
                    self.report["tables"].append(item)
                    if plan_table: plan_table.add_row(qname(*table), plan["path"].name, str(plan["source_row_count"]))
                    else: print(f" {qname(*table)} <- {plan['path'].name} ({plan['source_row_count']} rows)")
                if plan_table: _console.print(plan_table)
                if dry_run:
                    self.report["status"]="validated"; print("\nPreview complete. No rows were changed."); return
                if validate_only:
                    with conn.cursor() as cur:
                        for item,table in zip(self.report["tables"],order):
                            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table)))
                            item["destination_row_count"]=cur.fetchone()[0]
                            item["status"]="valid" if item["destination_row_count"]==item["source_row_count"] else "row_count_mismatch"
                    self.report["relationship_checks"]=self.validate_relationships(conn,order,fks)
                    mismatched=[x["table"] for x in self.report["tables"] if x["status"]!="valid"]
                    self.report["status"]="failed" if mismatched else "success"
                    if mismatched: raise RuntimeError("Validation row-count mismatch for: "+", ".join(mismatched))
                    print("\nValidation complete. No rows were changed."); return
                if truncate:
                    answer=input("Type TRUNCATE to replace data in the matched tables (transactional, no CASCADE): ")
                    if answer!="TRUNCATE": raise RuntimeError("Truncate cancelled.")
                    with conn.cursor() as cur:
                        cur.execute(sql.SQL("TRUNCATE TABLE {} RESTRICT").format(sql.SQL(",").join(sql.Identifier(*table) for table in order)))
                elif not resume:
                    with conn.cursor() as cur:
                        for table in order:
                            cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table)))
                            existing=cur.fetchone()[0]
                            if existing: raise RuntimeError(f"{qname(*table)} already has {existing} rows. Use --truncate to replace matched tables, or --resume to verify/skip existing rows.")
                else:
                    for table in order:
                        if not pks.get(table): raise RuntimeError(f"--resume requires a primary key on {qname(*table)}")
                if any(self.report["warnings"] and isinstance(w,dict) and "deferred_cycle" in w for w in self.report["warnings"]):
                    with conn.cursor() as cur: cur.execute("SET CONSTRAINTS ALL DEFERRED")
                for index,table in enumerate(order):
                    item=self.report["tables"][index]; started=time.monotonic()
                    try:
                        with conn.transaction():
                            with conn.cursor() as cur:
                                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(*table))); before=cur.fetchone()[0]
                            if resume and before==plans[table]["source_row_count"] and self.matches_existing(conn,table,plans[table]):
                                item["status"]="skipped_verified_match"; item["imported_row_count"]=before
                            else:
                                if resume and before: raise RuntimeError(f"--resume found different contents in {qname(*table)} despite a matching row count; refusing to overwrite or merge.")
                                count=self.import_table(conn,table,plans[table],False)
                                inserted=count-before; item["imported_row_count"]=inserted; item["destination_row_count"]=count; item["status"]="imported"
                                if inserted!=plans[table]["source_row_count"]: raise RuntimeError(f"Row count mismatch for {qname(*table)}: source {plans[table]['source_row_count']}, inserted {inserted}.")
                    except Exception:
                        item["status"]="failed"; item["failed_row_count"]=plans[table]["source_row_count"]
                        raise
                    item["duration_seconds"]=round(time.monotonic()-started,3)
                self.report["relationship_checks"]=self.validate_relationships(conn,order,fks)
                with conn.cursor() as cur:
                    cur.execute("""SELECT n.nspname,c.relname,a.attname,
                        pg_get_serial_sequence(format('%I.%I',n.nspname,c.relname),a.attname),
                        ps.seqstart
                        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        JOIN pg_attribute a ON a.attrelid=c.oid
                        JOIN pg_depend d ON d.refobjid=c.oid AND d.refobjsubid=a.attnum AND d.deptype IN ('a','i')
                        JOIN pg_class sc ON sc.oid=d.objid JOIN pg_sequence ps ON ps.seqrelid=sc.oid
                        WHERE c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped""")
                    for sch,tab,col,seq,start in cur.fetchall():
                        if (sch,tab) not in set(order): continue
                        cur.execute(sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(col),sql.Identifier(sch,tab)))
                        maximum=cur.fetchone()[0]
                        if maximum is None: cur.execute("SELECT setval(%s,%s,false)",(seq,start))
                        else: cur.execute("SELECT setval(%s,%s,true)",(seq,maximum))
                self.report["status"]="success_with_warnings" if self.report["warnings"] else "success"
                print(f"\nMigration {self.report['status']}.")
        except Exception as exc:
            message=safe_error(exc,self.config["url"]); self.report["errors"].append(message); self.report["status"]="failed"
            for item in self.report["tables"]:
                if item.get("status") in {"imported","skipped_verified_match"}:
                    item["status"]="rolled_back"; item["imported_row_count"]=0
            print(f"\nERROR: {message}",file=sys.stderr)
            raise RuntimeError(message) from None
        finally:
            self.write_report()
