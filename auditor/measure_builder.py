"""Measure builder: Claude designs a measure for projects the 27-measure catalog cannot test.

Without it, a project is only auditable if an analyst hand-wrote a measure for its kind of work. Here Claude
reads the relevant part of the data dictionary (retrieved by `schema.search`), picks the table that records
the project's work and writes the measure as small SQL expressions. Code then decides whether to trust it:

  1. Safety: expressions only (no SELECT / FROM / JOIN / file or network access), columns must exist.
  2. Attribution: an AI flag is accepted only when it records this project's own tool or model, never a
     tool shared across hundreds of projects. A team/division/process scope must name this project's own ids.
  3. Sufficiency: enough records overall, in each comparison group and on both sides of the rollout.
  4. Pre-registration: the design is fixed and fingerprinted (SHA-256) before any effect is computed.
     Claude only ever sees row counts and errors while repairing a design, never the outcome.

Claude never produces a number shown to a user: every figure comes from running the validated SQL.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone

from . import schema
from .catalog import METRICS, Metric
from .db import query
from .llm import LLM

UNITS = ["min", "hours", "days", "%", "score", "count", "usd"]
MIN_ROWS, MIN_CELL = 300, 30

# Per-record columns known to record AI use, and whose AI they record.
FLAG_TOOLS = {("support_cases", "ai_copilot_used"): "REPH Copilot",
              ("case_interactions", "ai_drafted"): "REPH Copilot",
              ("analyst_actions", "tool_used"): "Investigation Assistant"}
MODEL_FLAGS = {("editorial_tasks", "ai_assisted"): "model_id"}  # attributable through the model that did the work

_TEXT = {"type": "string"}
_GUARD = {"type": "object", "additionalProperties": False,
          "required": ["label", "unit", "higher_is_better", "value_sql", "why"],
          "properties": {"label": _TEXT, "unit": {"type": "string", "enum": UNITS},
                         "higher_is_better": {"type": "boolean"}, "value_sql": _TEXT, "why": _TEXT}}
DESIGN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["feasible", "table", "label", "unit", "higher_is_better", "value_sql", "time_column", "filter_sql",
                 "ai_flag", "scope_sql", "dims", "why", "assumptions", "guardrails"],
    "properties": {
        "feasible": {"type": "boolean"}, "table": _TEXT, "label": _TEXT, "unit": {"type": "string", "enum": UNITS},
        "higher_is_better": {"type": "boolean"}, "value_sql": _TEXT, "time_column": _TEXT, "filter_sql": _TEXT,
        "ai_flag": {"type": "object", "additionalProperties": False, "required": ["column", "true_when"],
                    "properties": {"column": _TEXT, "true_when": _TEXT}},
        "scope_sql": _TEXT, "dims": {"type": "array", "items": _TEXT}, "why": _TEXT,
        "assumptions": {"type": "array", "items": _TEXT}, "guardrails": {"type": "array", "items": _GUARD},
    },
}

BUILDER_SYSTEM = """You are the measure designer of Clairoscope, an AI value auditor. A project claims its AI improved a KPI,
but no standard measure covers its kind of work. Design ONE fair measurement from the tables provided (they
were retrieved from the data dictionary for this project). Code will validate and run your design; you will
not see any result before the design is fixed.

Fields:
- feasible: false if none of the tables records the work this project changes. Say why in "why". Never force a fit.
- table: exactly one table from the list. Every expression may use only that table's columns.
- value_sql: one DuckDB SQL expression giving one number per row: the outcome the claim is about.
  Rates: CASE WHEN <condition> THEN 100 ELSE 0 END with unit "%". Durations: date_diff('minute'|'hour'|'day',
  start_col, end_col). Return NULL for rows where the outcome is not known yet.
- time_column: the column that dates the work (when it was created or started).
- filter_sql: optional boolean expression restricting rows to the kind of work this project changes; "" for all.
- ai_flag: only if the table has a per-record column that records THIS project's AI being used (see the
  project's tools; a tool shared by hundreds of projects cannot identify this one). Else column "" and true_when "".
- scope_sql: boolean expression selecting the records of the project's own team, division or process, using the
  literal ids given (e.g. team_id = 'TM0010'). It lets us compare the project's area with everyone else before
  and after rollout. "" if the table has no such column.
- dims: up to 4 categorical columns of the table (e.g. category, priority) for mix checks.
- guardrails: 0-2 hidden costs from the same table that the AI could plausibly worsen (quality, rework,
  reopening, escalation, errors). Same expression rules.
- why: 1-2 plain sentences on why this measures the claim. assumptions: what must be true for it to be valid.
Rules: single SQL expressions only: no SELECT, FROM, JOIN, WITH, subqueries, semicolons or comments; use
date_part/date_diff, not EXTRACT. Use only column names and category values that exist. Prefer the measure the
claimed KPI literally describes; otherwise the closest direct record of the same work.
Return JSON only."""


class BuildError(Exception):
    """No trustworthy measure could be built (the reason is user-facing)."""


class NotFeasible(BuildError):
    """Claude judged that no retrieved table records this project's work."""


_BANNED = re.compile(r";|--|/\*|\b(select|from|join|union|with|copy|attach|detach|install|load|pragma|export|"
                     r"import|call|set|create|insert|update|delete|drop|alter|truncate|vacuum|checkpoint|glob|"
                     r"getenv|system|read_\w+|write_\w+|https?|s3)\b", re.I)


def _safe(expr: str, what: str) -> str:
    expr = (expr or "").strip()
    if _BANNED.search(re.sub(r"'(?:[^']|'')*'", "''", expr)):  # string literals may say anything
        raise BuildError(f"{what} is not a plain SQL expression: {expr!r}")
    return expr


def _tools(use_case_id: str) -> dict[str, dict]:
    df = query("""select tool, count(*) filter (where use_case_id = ?) as n,
                         count(distinct use_case_id) as projects
                  from ai_usage_events group by tool""", [use_case_id])
    return {r.tool: {"events": int(r.n), "projects_using_tool": int(r.projects)} for r in df.itertuples() if r.n}


def _ids(use_case_id: str) -> dict:
    r = query("select owner_team_id, division_id, process_id, model_id from ai_use_cases where use_case_id = ?",
              [use_case_id]).iloc[0]
    return {k: r[k] for k in ("owner_team_id", "division_id", "process_id", "model_id") if isinstance(r[k], str)}


def _flag(spec: dict, uc: dict, tools: dict, ids: dict) -> tuple[str | None, str]:
    """(treated expression or None, note). Rejects flags that would credit this project with another AI's work."""
    col, when = spec["ai_flag"].get("column", "").strip(), spec["ai_flag"].get("true_when", "").strip()
    if not col:
        return None, ""
    table = spec["table"]
    if col not in schema.columns(table):
        return None, f"AI flag {col} is not a column of {table}; ignored."
    expr = _safe(when or col, "AI flag")
    if (table, col) in MODEL_FLAGS:
        mcol, own = MODEL_FLAGS[(table, col)], ids.get("model_id")
        models = query(f'select distinct {mcol} as m from "{table}" where {col} and {mcol} is not null')["m"].tolist()
        if own not in models:
            return None, (f"AI flag {table}.{col} records models {', '.join(models)}, not this project's "
                          f"model {own or '(none)'}; not used.")
        return f"(({expr}) and {mcol} = '{own}')", f"AI flag {table}.{col}, limited to this project's model {own}."
    tool = FLAG_TOOLS.get((table, col))
    if tool is None:
        return None, f"{table}.{col} is not a known record of AI use; not used."
    if tool not in tools:
        return None, f"{table}.{col} records {tool}, which this project does not use; not used."
    if tools[tool]["projects_using_tool"] > 1:
        return None, (f"{table}.{col} records {tool}, shared by {tools[tool]['projects_using_tool']} projects, so it "
                      f"cannot isolate this one; not used.")
    return expr, f"AI flag {table}.{col} records {tool}, used only by this project."


def _scope(spec: dict, ids: dict) -> tuple[str | None, str]:
    expr = _safe(spec.get("scope_sql", ""), "Scope")
    if not expr:
        return None, ""
    own = [v for v in ids.values() if f"'{v}'" in expr]
    if not own:
        return None, "Scope does not name this project's own team, division or process; not used."
    return expr, f"Project's own area: {expr}."


def _assemble(spec: dict, value_sql: str, treated: str | None, scope: str | None, rollout: str) -> str:
    t = spec["table"]
    pk = schema.primary_key(t)
    parts = [f"{pk} as id" if pk else "row_number() over () as id",
             f"try_cast({spec['time_column']} as timestamp) as ts",
             f"try_cast(({value_sql}) as double) as value"]
    if treated:
        parts.append(f"coalesce(({treated}), false) as treated")
    if scope:
        parts += [f"coalesce(({scope}), false) as adopter",
                  f"case when coalesce(({scope}), false) then timestamp '{rollout}' end as go_live"]
    parts += spec["dims"]
    where = f" where ({spec['filter_sql']})" if spec.get("filter_sql") else ""
    return f'select {", ".join(parts)} from "{t}"{where}'


def _counts(sql: str, rollout: str, treated: bool, scope: bool) -> dict:
    r = f"timestamp '{rollout}'"
    extra = ""
    if treated:
        extra += ", count(*) filter (where treated) as ai, count(*) filter (where not treated) as manual"
    if scope:
        extra += (f", count(*) filter (where adopter and ts < {r}) as own_pre"
                  f", count(*) filter (where adopter and ts >= {r}) as own_post"
                  f", count(*) filter (where not adopter and ts < {r}) as others_pre"
                  f", count(*) filter (where not adopter and ts >= {r}) as others_post")
    row = query(f"""with b as ({sql}) select count(*) as n, count(distinct value) as distinct_values,
                    min(value) as lo, max(value) as hi,
                    count(*) filter (where ts < {r} and ts >= {r} - interval 365 day) as pre,
                    count(*) filter (where ts >= {r} and ts < {r} + interval 365 day) as post{extra}
                    from b where value is not null and ts is not null""").iloc[0]
    return {k: (None if v != v else (float(v) if k in ("lo", "hi") else int(v))) for k, v in row.items()}


def _check(spec: dict, unit: str, c: dict, treated: bool, scope: bool) -> tuple[list[str], list[str]]:
    """(problems, feasible designs) from row counts only."""
    problems = []
    if c["n"] < MIN_ROWS:
        problems.append(f"only {c['n']} records with a value (need {MIN_ROWS})")
    if c["distinct_values"] < 2:
        problems.append("the value never varies")
    if unit == "%" and c["lo"] is not None and (c["lo"] < 0 or c["hi"] > 100):
        problems.append(f"a % measure ranges {c['lo']}..{c['hi']}; rates must be 0 or 100 per record")
    designs = []
    if scope and min(c["own_pre"], c["own_post"], c["others_pre"], c["others_post"]) >= MIN_CELL:
        designs.append("did")
    if treated and min(c["ai"], c["manual"]) >= MIN_CELL:
        designs.append("treated")
    if min(c["pre"], c["post"]) >= MIN_CELL:
        designs.append("before_after")
    if not designs:
        problems.append(f"not enough records on both sides of the rollout to compare (counts: {c})")
    return problems, designs


def _lag_days(sql: str, unit: str) -> int:
    """How long a record's outcome takes to become final. Newer records are cut off, otherwise only the quick
    ones would be counted and any recent rollout would look like an improvement (right-censoring)."""
    per_day = {"min": 1440, "hours": 24, "days": 1}.get(unit)
    if per_day is None:
        return 30  # rates and scores can still change after the record is created (reopened, escalated, ...)
    q = query(f"with b as ({sql}) select quantile_cont(value, 0.95) as q from b where value is not null")["q"].iloc[0]
    return max(1, math.ceil((q or 0) / per_day) + 1)


def _design(llm: LLM, payload: dict, feedback: list[str] | None) -> dict:
    if feedback:
        payload = dict(payload, problems_with_previous_design=feedback)
    spec = llm.json(BUILDER_SYSTEM, json.dumps(payload, default=str), DESIGN_SCHEMA)
    if not spec.get("feasible"):
        raise NotFeasible("Claude found no table that records this project's work: " + spec.get("why", ""))
    if spec.get("table") not in schema.available():
        raise ValueError(f"table {spec.get('table')!r} is not loaded; use one of the tables provided")
    cols = schema.columns(spec["table"])
    if spec.get("time_column") not in cols:
        raise ValueError(f"time column {spec.get('time_column')!r} is not a column of {spec['table']}")
    spec["dims"] = [d for d in spec.get("dims", []) if d in cols][:4]
    spec["filter_sql"] = _safe(spec.get("filter_sql", ""), "Filter")
    _safe(spec["value_sql"], "Value")
    return spec


def build(uc: dict, claim: dict | None, rollout: str, llm: LLM, retrieved: int = 6) -> dict:
    """Design, validate and register a measure. Returns {plan, design}; raises BuildError when not trustworthy."""
    ids, tools = _ids(uc["use_case_id"]), _tools(uc["use_case_id"])
    # the work, not the org chart: a division name would pull in reference tables like "divisions"
    query_text = " ".join(str(x) for x in (uc.get("name"), uc.get("primary_kpi"), uc.get("process_name"),
                                           (claim or {}).get("kpi_name")) if x)
    cards = schema.search(query_text, retrieved)
    if (uc.get("process_has_event_log") and "process_event_log" in schema.available()
            and "process_event_log" not in {c["table"] for c in cards}):
        cards.append(schema.dictionary()["process_event_log"])
    payload = {
        "project": {"name": uc.get("name"), "kpi_claimed": uc.get("primary_kpi"), "process": uc.get("process_name"),
                    "division": uc.get("division"), "stage": uc.get("stage"), "rollout_date": rollout, **ids},
        "claim": claim, "project_ai_tools": tools, "tables": [schema.brief(c) for c in cards],
    }
    feedback, last_error = None, ""
    for attempt in (1, 2):  # one repair round, driven by validation errors and row counts only
        try:
            spec = _design(llm, payload, feedback)
            treated, flag_note = _flag(spec, uc, tools, ids)
            scope, scope_note = _scope(spec, ids)
            sql = _assemble(spec, spec["value_sql"], treated, scope, rollout)
            query(f"select * from ({sql}) limit 0")  # binds every column name and function
            counts = _counts(sql, rollout, bool(treated), bool(scope))
            problems, designs = _check(spec, spec["unit"], counts, bool(treated), bool(scope))
        except NotFeasible:
            raise
        except Exception as e:  # unsafe or unbound SQL, or a malformed design: one chance to repair
            problems, designs, last_error = [str(e).split("\n")[0][:300]], [], str(e)
        if not problems:
            break
        feedback, last_error = problems, "; ".join(problems)
    else:
        raise BuildError("Claude's measure did not pass the checks: " + last_error)

    guards, notes = [], [n for n in (flag_note, scope_note) if n]
    for g in spec.get("guardrails", [])[:2]:
        try:
            gsql = _assemble(spec, _safe(g["value_sql"], "Guardrail"), treated, scope, rollout)
            gc = _counts(gsql, rollout, bool(treated), bool(scope))
            gp, _ = _check(spec, g["unit"], gc, bool(treated), bool(scope))
            if gp:
                raise BuildError("; ".join(gp))
            guards.append((g, gsql))
        except Exception as e:
            notes.append(f"Hidden-cost measure '{g.get('label')}' dropped: {str(e).splitlines()[0][:160]}")

    registered = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fingerprint = hashlib.sha256(json.dumps({"spec": spec, "sql": sql, "guards": [s for _, s in guards]},
                                            sort_keys=True, default=str).encode()).hexdigest()
    key = f"cd_{fingerprint[:10]}"
    lag = _lag_days(sql, spec["unit"])
    _register(key, spec["label"], spec["unit"], spec["higher_is_better"], spec, sql, treated, scope, lag)
    plan_guards = []
    for i, (g, gsql) in enumerate(guards):
        gkey = f"{key}_g{i + 1}"
        _register(gkey, g["label"], g["unit"], g["higher_is_better"], spec, gsql, treated, scope,
                  _lag_days(gsql, g["unit"]))
        plan_guards.append({"metric": gkey, "where": {}, "why": g["why"]})
    best = designs[0]  # did > treated > before_after, the same ranking the planner uses
    plan = {"hypotheses": spec.get("assumptions", []), "primary": {"metric": key, "where": {}}, "design": best,
            "guardrails": plan_guards, "investigations": [], "rationale": spec["why"],
            "planner": f"Claude measure builder ({llm.name})", "catalog_fit": "designed"}
    design = {"spec": spec, "sql": sql, "guardrails": [dict(g, sql=s) for g, s in guards], "designs": designs,
              "counts": counts, "notes": notes, "outcome_lag_days": lag, "retrieved_tables": [(c["table"], c.get("score")) for c in cards],
              "registered_at": registered, "sha256": fingerprint, "attempts": attempt,
              "as_of": date.today().isoformat()}
    return {"plan": plan, "design": design}


def _register(key: str, label: str, unit: str, hib: bool, spec: dict, sql: str, treated, scope,
              lag: int = 30) -> None:
    METRICS[key] = Metric(
        key, label, unit, hib, spec["table"], f"Designed by Claude: {spec['why']}", sql, tuple(spec["dims"]),
        treated_label="AI-assisted records" if treated else None,
        adopter_label="Project's own area" if scope else None, outcome_lag_days=lag, tags=("claude-designed",))


def restore(design: dict) -> None:
    """Re-register a stored design (e.g. after a restart) so its charts and memo still work."""
    spec, key = design["spec"], f"cd_{design['sha256'][:10]}"
    treated, scope = " as treated" in design["sql"], " as adopter" in design["sql"]
    if key not in METRICS:
        _register(key, spec["label"], spec["unit"], spec["higher_is_better"], spec, design["sql"], treated, scope,
                  design.get("outcome_lag_days", 30))
    for i, g in enumerate(design.get("guardrails", [])):
        if f"{key}_g{i + 1}" not in METRICS:
            _register(f"{key}_g{i + 1}", g["label"], g["unit"], g["higher_is_better"], spec, g["sql"], treated, scope,
                      _lag_days(g["sql"], g["unit"]))
