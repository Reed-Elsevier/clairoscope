"""Decision ledger: every audit and every human decision is stored (SQLite, .state/ledger.sqlite)."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .db import ROOT

# AUDITOR_STATE_DIR lets AWS deployments put the ledger on a persistent volume (EBS/EFS)
DB = Path(os.environ.get("AUDITOR_STATE_DIR", "").strip() or ROOT / ".state") / "ledger.sqlite"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""create table if not exists audits (audit_id text primary key, created_at text, use_case_id text,
                 use_case_name text, claim text, verdict text, rubric_verdict text, mode text, report_json text)""")
    c.execute("""create table if not exists decisions (id integer primary key autoincrement, audit_id text,
                 decided_at text, reviewer text, agrees_with_verdict integer, decision text, note text)""")
    return c


def save_audit(r: dict) -> None:
    claim = r["claim_text"] or (f"{r['claim']['kpi_name']}: {r['claim']['baseline']} -> {r['claim']['current']}"
                                if r.get("claim") else "")
    with _conn() as c:
        c.execute("insert or replace into audits values (?,?,?,?,?,?,?,?,?)",
                  (r["audit_id"], r["created_at"], r["use_case"]["use_case_id"], r["use_case"]["name"], claim,
                   r["verdict"]["verdict"], r["rubric"]["verdict"], r["mode"], json.dumps(r, default=str)))


def save_decision(audit_id: str, reviewer: str, agrees: bool, decision: str, note: str) -> None:
    with _conn() as c:
        c.execute("insert into decisions (audit_id, decided_at, reviewer, agrees_with_verdict, decision, note) "
                  "values (?,?,?,?,?,?)",
                  (audit_id, datetime.now().isoformat(timespec="seconds"), reviewer, int(agrees), decision, note))


def history() -> pd.DataFrame:
    with _conn() as c:
        return pd.read_sql_query("""
            select a.created_at, a.use_case_id, a.use_case_name, a.verdict, a.rubric_verdict, a.mode,
                   d.reviewer, d.decision, d.agrees_with_verdict, d.note, d.decided_at, a.audit_id
            from audits a left join decisions d using (audit_id) order by a.created_at desc""", c)


def latest_decision(audit_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("select reviewer, agrees_with_verdict, decision, note from decisions where audit_id = ? "
                        "order by id desc limit 1", (audit_id,)).fetchone()
    return {"reviewer": row[0], "agrees": bool(row[1]), "decision": row[2], "note": row[3]} if row else None


def load_report(audit_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("select report_json from audits where audit_id = ?", (audit_id,)).fetchone()
    return json.loads(row[0]) if row else None


def reset() -> None:
    Path(DB).unlink(missing_ok=True)
