"""Use-case facts (claims, adoption, governance, model evals) and the portfolio-wide screen."""
from __future__ import annotations

import pandas as pd

from .db import query

GENERIC_KPIS = {"Cost per transaction", "Backlog", "Throughput", "CSAT", "Error rate", "Handling time"}
LOWER_IS_BETTER_WORDS = ("time", "minutes", "hours", "rework", "false positive", "error", "cost", "backlog", "latency")


def kpi_higher_is_better(kpi_name: str) -> bool:
    name = kpi_name.lower()
    return not any(w in name for w in LOWER_IS_BETTER_WORDS)


def _d(v) -> str | None:
    return None if v is None or pd.isna(v) else str(pd.Timestamp(v).date())


def list_use_cases() -> pd.DataFrame:
    return query("""
        select u.use_case_id, u.use_case_name, u.stage, u.archetype, d.division_name
        from ai_use_cases u left join divisions d using (division_id)
        order by (u.use_case_id <= 'UC0008') desc, u.use_case_id""")


def get_use_case(use_case_id: str) -> dict | None:
    df = query("""
        select u.*, d.division_name, p.process_name, p.has_event_log
        from ai_use_cases u left join divisions d using (division_id)
        left join process_definitions p using (process_id) where u.use_case_id = ?""", [use_case_id])
    if df.empty:
        return None
    r = df.iloc[0]
    uc = {
        "use_case_id": r["use_case_id"], "name": r["use_case_name"], "division": r["division_name"],
        "process_id": r["process_id"], "process_name": r["process_name"],
        "process_has_event_log": bool(r["has_event_log"]) if not pd.isna(r["has_event_log"]) else False,
        "archetype": r["archetype"], "stage": r["stage"], "primary_kpi": r["primary_kpi"],
        "model_id": r["model_id"], "idea_date": _d(r["idea_date"]), "poc_date": _d(r["poc_date"]),
        "pilot_date": _d(r["pilot_date"]), "scaled_date": _d(r["scaled_date"]), "retired_date": _d(r["retired_date"]),
        "expected_annual_value_usd": float(r["expected_annual_value_usd"] or 0),
        "realized_annual_value_usd": float(r["realized_annual_value_usd"] or 0),
        "hours_saved_annual": float(r["hours_saved_annual"] or 0),
    }
    kpi = query("select * from ai_use_case_kpis where use_case_id = ? order by measured_date desc limit 1", [use_case_id])
    if not kpi.empty and pd.notna(kpi.iloc[0]["current_value"]) and pd.notna(kpi.iloc[0]["baseline_value"]):
        k = kpi.iloc[0]  # a KPI without a result (ideas only have a baseline) is not a claim
        uc["claim"] = {"kpi_name": k["kpi_name"], "baseline": float(k["baseline_value"]),
                       "current": float(k["current_value"]), "measured_date": _d(k["measured_date"]),
                       "source": f"ai_use_case_kpis.{k['kpi_id']}",
                       "generic_kpi": k["kpi_name"] in GENERIC_KPIS,
                       "higher_is_better": kpi_higher_is_better(k["kpi_name"])}
    usage = query("""select count(*) as n, min(event_ts) as first_ts, max(event_ts) as last_ts,
                            avg((outcome = 'Accepted')::int) as accepted, avg((outcome = 'Rejected')::int) as rejected
                     from ai_usage_events where use_case_id = ?""", [use_case_id]).iloc[0]
    uc["usage"] = {"events": int(usage["n"]), "first": _d(usage["first_ts"]), "last": _d(usage["last_ts"]),
                   "accepted_share": None if pd.isna(usage["accepted"]) else round(float(usage["accepted"]), 3),
                   "rejected_share": None if pd.isna(usage["rejected"]) else round(float(usage["rejected"]), 3)}
    gov = query("select review_type, status, high_findings, completed_at from ai_governance_reviews where use_case_id = ?",
                [use_case_id])
    uc["governance"] = [{"review_type": g.review_type, "status": g.status, "high_findings": int(g.high_findings or 0),
                         "completed": _d(g.completed_at)} for g in gov.itertuples()]
    if r["model_id"]:
        mv = query("select version, released_date, eval_accuracy, eval_hallucination_rate, status "
                   "from model_versions where model_id = ? order by released_date", [r["model_id"]])
        uc["model_versions"] = [{"version": m.version, "released": _d(m.released_date),
                                 "eval_accuracy": None if pd.isna(m.eval_accuracy) else float(m.eval_accuracy),
                                 "status": m.status} for m in mv.itertuples()]
    else:
        uc["model_versions"] = []
    return uc


def rollout_date(uc: dict) -> tuple[str | None, str]:
    """Best available date the AI started affecting work, and where it came from."""
    for key, label in (("pilot_date", "pilot date"), ("scaled_date", "scaled date")):
        if uc.get(key):
            return uc[key], label
    if uc["usage"]["first"]:
        return uc["usage"]["first"], "first recorded usage event"
    return None, "none"


def portfolio() -> pd.DataFrame:
    """Screen all use cases for evidence red flags in one query (no LLM)."""
    return query("""
        with usage as (select use_case_id, count(*) as usage_events from ai_usage_events group by 1),
        gov as (select use_case_id, count(*) filter (where status like 'Approved%') as approved_reviews,
                       count(*) as reviews from ai_governance_reviews group by 1),
        kpi as (select use_case_id, any_value(kpi_name) as kpi_name, any_value(baseline_value) as baseline,
                       any_value(current_value) as current, max(measured_date) as measured_date
                from ai_use_case_kpis group by 1)
        select u.use_case_id, u.use_case_name, u.stage, u.archetype, d.division_name as division,
               u.expected_annual_value_usd as expected_usd, u.realized_annual_value_usd as realized_usd,
               coalesce(usage.usage_events, 0) as usage_events,
               coalesce(gov.approved_reviews, 0) as approved_reviews,
               k.kpi_name, k.baseline, k.current, k.measured_date, u.pilot_date,
               p.has_event_log as measurable_process
        from ai_use_cases u
        left join divisions d using (division_id)
        left join usage using (use_case_id) left join gov using (use_case_id)
        left join kpi k using (use_case_id)
        left join process_definitions p using (process_id)
        order by u.use_case_id""")


def portfolio_flags(df: pd.DataFrame) -> pd.DataFrame:
    live = df["stage"].isin(["Pilot", "Scaled"])
    out = df.copy()
    out["flag_ghost_adoption"] = live & (out["usage_events"] == 0)
    out["flag_governance_gap"] = live & (out["approved_reviews"] == 0)
    out["flag_kpi_before_pilot"] = out["pilot_date"].notna() & out["measured_date"].notna() & \
        (pd.to_datetime(out["measured_date"]) < pd.to_datetime(out["pilot_date"]))
    hib = out["kpi_name"].fillna("").map(kpi_higher_is_better)
    worse = ((out["current"] < out["baseline"]) & hib) | ((out["current"] > out["baseline"]) & ~hib)
    out["flag_claim_shows_decline"] = live & out["kpi_name"].notna() & worse
    out["flag_value_gap"] = (out["stage"] == "Scaled") & (out["realized_usd"] < 0.5 * out["expected_usd"])
    flag_cols = [c for c in out.columns if c.startswith("flag_")]
    out["red_flags"] = out[flag_cols].sum(axis=1)
    return out
