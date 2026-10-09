"""Deterministic analysis engine. Every number the auditor shows is computed here and logged
as an Evidence item with its SQL and sample record IDs, so it can be traced and re-run."""
from __future__ import annotations

import math
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import pandas as pd

from . import AS_OF_DATE
from .catalog import METRICS, Metric
from .db import query

MIN_GROUP_N = 30


# ---------------------------------------------------------------- evidence log
@dataclass
class Evidence:
    id: str
    kind: str
    title: str
    metric: str | None
    summary: str
    data: dict
    sql: str
    sample_ids: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class EvidenceLog:
    def __init__(self) -> None:
        self.items: list[Evidence] = []

    def add(self, kind: str, title: str, metric: str | None, summary: str, data: dict,
            sql: str = "", sample_ids: dict | None = None) -> Evidence:
        ev = Evidence(f"E{len(self.items) + 1}", kind, title, metric, summary, data, sql, sample_ids or {})
        self.items.append(ev)
        return ev

    def get(self, eid: str) -> Evidence | None:
        return next((e for e in self.items if e.id == eid), None)


# ---------------------------------------------------------------- helpers
def _fmt(v: float | None, unit: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "score":
        return f"{v:.3f}" if abs(v) < 2 else f"{v:.2f}"
    return f"{v:.1f} {unit}"


def _diff_fmt(v: float, unit: str) -> str:
    sign = "+" if v >= 0 else "-"
    if unit == "%":
        return f"{sign}{abs(v):.1f} pp"
    if unit == "score":
        return f"{sign}{abs(v):.3f}"
    return f"{sign}{abs(v):.1f} {unit}"


def _render_sql(sql: str, params: list) -> str:
    """Inline parameters for display in evidence receipts (never executed)."""
    out, it = [], iter(params)
    for ch in sql:
        if ch == "?":
            v = next(it)
            out.append(f"'{v}'" if isinstance(v, str) else str(v))
        else:
            out.append(ch)
    return "".join(out)


AI_MODEL = "_ai_model"  # attribution filter: manual records plus this project's own AI records only


def _base(metric: Metric, where: dict | None) -> tuple[str, list]:
    clauses, params = ["value is not null"], []
    for dim, val in (where or {}).items():
        if dim == AI_MODEL:  # other AI projects' records must not count for (or against) this one
            clauses.append("(not coalesce(treated, false) or model_id = ?)")
            params.append(val)
            continue
        if dim not in metric.dims:
            raise ValueError(f"'{dim}' is not a dimension of {metric.key}")
        if isinstance(val, (list, tuple)):
            clauses.append(f'"{dim}" in ({", ".join("?" * len(val))})')
            params.extend(val)
        else:
            clauses.append(f'"{dim}" = ?')
            params.append(val)
    return f"select * from ({metric.sql}) b where {' and '.join(clauses)}", params


@lru_cache(maxsize=64)
def ai_models(metric_key: str) -> tuple[str, ...]:
    """AI models that appear on AI-assisted records of a metric, when the records say which model did the work."""
    m = METRICS[metric_key]
    if not (m.has_treated and "model_id" in m.dims):
        return ()
    df = query(f"select distinct model_id from ({m.sql}) b where treated and model_id is not null order by 1")
    return tuple(df["model_id"])


def _welch(a: dict, b: dict) -> dict:
    """a = AI/after group, b = comparison group. Normal-approximation test on the difference in means."""
    diff = a["mean"] - b["mean"]
    se = math.sqrt(a["var"] / a["n"] + b["var"] / b["n"]) if a["n"] > 1 and b["n"] > 1 else float("nan")
    z = diff / se if se and se > 0 else float("inf")
    p = math.erfc(abs(z) / math.sqrt(2)) if math.isfinite(z) else 0.0
    rel = diff / abs(b["mean"]) if b["mean"] else None
    return {"diff": diff, "se": se, "ci_low": diff - 1.96 * se, "ci_high": diff + 1.96 * se,
            "p_value": p, "rel_change": rel}


def _group_stats(base_sql: str, params: list, group_expr: str, extra_where: str = "",
                 extra_params: list | None = None) -> tuple[dict, str]:
    sql = (f"with b as ({base_sql}) select ({group_expr}) as grp, count(*) as n, avg(value) as mean, "
           f"coalesce(var_samp(value), 0) as var from b {extra_where} group by 1")
    df = query(sql, params + (extra_params or []))
    return {row.grp: {"n": int(row.n), "mean": float(row.mean), "var": float(row.var)}
            for row in df.itertuples() if row.grp is not None}, _render_sql(sql, params + (extra_params or []))


def _sample_ids(base_sql: str, params: list, cond: str, cond_params: list, k: int = 5) -> list[str]:
    df = query(f"with b as ({base_sql}) select id from b where {cond} order by hash(id) limit {k}",
               params + cond_params)
    return [str(x) for x in df["id"].tolist()]


def as_of() -> date:
    return date.fromisoformat(AS_OF_DATE)


def censor_cutoff(metric: Metric) -> str | None:
    """Records newer than this have not had time for their outcome to materialise."""
    if metric.outcome_lag_days <= 0:
        return None
    return (as_of() - timedelta(days=metric.outcome_lag_days)).isoformat()


# ---------------------------------------------------------------- analyses
def compare_treated(log: EvidenceLog, metric_key: str, where: dict | None = None,
                    since: str | None = None, until: str | None = None) -> Evidence | None:
    """AI-assisted records vs non-assisted records in the same period."""
    m = METRICS[metric_key]
    if not m.has_treated:
        return None
    base, params = _base(m, where)
    if since is None:
        first = query(f"with b as ({base}) select min(ts) as t from b where treated", params)["t"].iloc[0]
        if pd.isna(first):
            return None
        since = str(pd.Timestamp(first).date())
    conds, cp = ["ts >= ?"], [since]
    if until:
        conds.append("ts < ?")
        cp.append(until)
    stats, sql = _group_stats(base, params, "treated", "where " + " and ".join(conds), cp)
    if True not in stats or False not in stats:
        return None
    a, b = stats[True], stats[False]
    t = _welch(a, b)
    ids = {"ai": _sample_ids(base, params, "treated and " + " and ".join(conds), cp),
           "comparison": _sample_ids(base, params, "not treated and " + " and ".join(conds), cp)}
    summary = (f"{m.label}: {_fmt(a['mean'], m.unit)} with AI ({m.treated_label}, n={a['n']:,}) vs "
               f"{_fmt(b['mean'], m.unit)} without (n={b['n']:,}) since {since}; "
               f"difference {_diff_fmt(t['diff'], m.unit)}"
               + (f" ({t['rel_change']:+.1%})" if t["rel_change"] is not None else "")
               + f", p={t['p_value']:.3g}.")
    data = {"design": "treated", "where": where or {}, "since": since, "until": until,
            "ai": a, "comparison": b, **t, "unit": m.unit}
    return log.add("compare", f"{m.label}: AI vs non-AI records", metric_key, summary, data, sql, ids)


def before_after(log: EvidenceLog, metric_key: str, cutoff: str, where: dict | None = None,
                 window_days: int | None = 365, until: str | None = None) -> Evidence | None:
    """Same population before vs after the rollout date."""
    m = METRICS[metric_key]
    base, params = _base(m, where)
    start = (date.fromisoformat(cutoff) - timedelta(days=window_days)).isoformat() if window_days else "1900-01-01"
    conds, cp = ["ts >= ?"], [start]
    if window_days:
        conds.append("ts < ?")
        cp.append((date.fromisoformat(cutoff) + timedelta(days=window_days)).isoformat())
    if until:
        conds.append("ts < ?")
        cp.append(until)
    stats, sql = _before_after_stats(base, params, cutoff, conds, cp)
    pre, post = stats.get(False), stats.get(True)
    data = {"design": "before_after", "where": where or {}, "cutoff": cutoff, "window_days": window_days,
            "until": until, "pre": pre, "post": post, "unit": m.unit}
    if not pre or not post:
        summary = (f"{m.label}: not enough data on both sides of {cutoff} "
                   f"(pre n={pre['n'] if pre else 0}, post n={post['n'] if post else 0}).")
        return log.add("before_after", f"{m.label}: before vs after {cutoff}", metric_key, summary, data, sql)
    t = _welch(post, pre)
    data.update(t)
    all_cond = " and ".join(conds)
    ids = {"before": _sample_ids(base, params, f"ts < ?::timestamp and {all_cond}", [cutoff] + cp),
           "after": _sample_ids(base, params, f"ts >= ?::timestamp and {all_cond}", [cutoff] + cp)}
    summary = (f"{m.label}: {_fmt(pre['mean'], m.unit)} before {cutoff} (n={pre['n']:,}) vs "
               f"{_fmt(post['mean'], m.unit)} after (n={post['n']:,}); change {_diff_fmt(t['diff'], m.unit)}"
               + (f" ({t['rel_change']:+.1%})" if t["rel_change"] is not None else "")
               + f", p={t['p_value']:.3g}" + (f"; records after {until} excluded (outcome not yet known)" if until else "")
               + ".")
    return log.add("before_after", f"{m.label}: before vs after {cutoff}", metric_key, summary, data, sql, ids)


def _before_after_stats(base: str, params: list, cutoff: str, conds: list, cp: list) -> tuple[dict, str]:
    sql = (f"with b as ({base}) select (ts >= ?::timestamp) as grp, count(*) as n, avg(value) as mean, "
           f"coalesce(var_samp(value), 0) as var from b where {' and '.join(conds)} group by 1")
    allp = params + [cutoff] + cp
    df = query(sql, allp)
    return ({bool(r.grp): {"n": int(r.n), "mean": float(r.mean), "var": float(r.var)} for r in df.itertuples()},
            _render_sql(sql, allp))


def diff_in_diff(log: EvidenceLog, metric_key: str, cutoff: str | None = None,
                 where: dict | None = None) -> Evidence | None:
    """Adopter groups vs non-adopters, before vs after each group's own go-live (staggered rollout)."""
    m = METRICS[metric_key]
    if not m.has_adopter:
        return None
    base, params = _base(m, where)
    if cutoff is None:
        med = query(f"with b as ({base}) select median(go_live) as g from b where adopter", params)["g"].iloc[0]
        if pd.isna(med):  # no adopter records left after the filters
            return None
        cutoff = str(pd.Timestamp(med).date())
    sql = (f"with b as ({base}) select adopter, (ts >= coalesce(go_live, ?::timestamp)) as post, count(*) as n, "
           f"avg(value) as mean, coalesce(var_samp(value), 0) as var from b group by 1, 2")
    allp = params + [cutoff]
    df = query(sql, allp)
    cells = {(bool(r.adopter), bool(r.post)): {"n": int(r.n), "mean": float(r.mean), "var": float(r.var)}
             for r in df.itertuples()}
    if len(cells) < 4:
        return None
    tp, tq, cp_, cq = cells[(True, False)], cells[(True, True)], cells[(False, False)], cells[(False, True)]
    did = (tq["mean"] - tp["mean"]) - (cq["mean"] - cp_["mean"])
    se = math.sqrt(sum(c["var"] / c["n"] for c in (tp, tq, cp_, cq)))
    z = did / se if se > 0 else float("inf")
    p = math.erfc(abs(z) / math.sqrt(2))
    rel = did / abs(tp["mean"]) if tp["mean"] else None
    ids = {"adopters_after": _sample_ids(base, params, "adopter and ts >= go_live", []),
           "non_adopters_after": _sample_ids(base, params, "not adopter and ts >= ?::timestamp", [cutoff])}
    summary = (f"{m.label} diff-in-diff: {m.adopter_label} went {_fmt(tp['mean'], m.unit)} -> {_fmt(tq['mean'], m.unit)}, "
               f"others went {_fmt(cp_['mean'], m.unit)} -> {_fmt(cq['mean'], m.unit)} "
               f"(others' cutoff {cutoff}); net AI effect {_diff_fmt(did, m.unit)}"
               + (f" ({rel:+.1%})" if rel is not None else "") + f", p={p:.3g}.")
    data = {"design": "did", "where": where or {}, "cutoff": cutoff,
            "adopters_pre": tp, "adopters_post": tq, "others_pre": cp_, "others_post": cq,
            "diff": did, "se": se, "ci_low": did - 1.96 * se, "ci_high": did + 1.96 * se,
            "p_value": p, "rel_change": rel, "unit": m.unit}
    return log.add("did", f"{m.label}: difference-in-differences", metric_key, summary, data, _render_sql(sql, allp), ids)


def level(log: EvidenceLog, metric_key: str, where: dict | None = None, start: str | None = None,
          end: str | None = None, treated: bool | None = None, label: str = "") -> Evidence | None:
    m = METRICS[metric_key]
    base, params = _base(m, where)
    conds, cp = [], []
    if start:
        conds.append("ts >= ?")
        cp.append(start)
    if end:
        conds.append("ts < ?")
        cp.append(end)
    if treated is not None and m.has_treated:
        conds.append("treated" if treated else "not treated")
    w = ("where " + " and ".join(conds)) if conds else ""
    sql = f"with b as ({base}) select count(*) as n, avg(value) as mean from b {w}"
    r = query(sql, params + cp).iloc[0]
    if not r["n"]:
        return None
    window = f"{start or 'start'} to {end or AS_OF_DATE}"
    summary = f"{m.label} {label or 'level'}: {_fmt(float(r['mean']), m.unit)} (n={int(r['n']):,}, {window})."
    return log.add("level", f"{m.label}: {label or 'level'}", metric_key, summary,
                   {"n": int(r["n"]), "mean": float(r["mean"]), "start": start, "end": end, "unit": m.unit,
                    "where": where or {}}, _render_sql(sql, params + cp))


def monthly(metric_key: str, where: dict | None = None, group: str | None = None) -> pd.DataFrame:
    """Monthly series for charts and change-point detection. group: None | 'treated' | 'adopter' | a dim."""
    m = METRICS[metric_key]
    base, params = _base(m, where)
    g = "'All'" if group is None else (f'"{group}"' if group in m.dims else group)
    if group in ("treated", "adopter"):
        lbl = m.treated_label if group == "treated" else m.adopter_label
        g = f"case when {group} then '{lbl}' else 'Other' end"
    sql = (f"with b as ({base}) select date_trunc('month', ts) as month, cast({g} as varchar) as grp, "
           f"count(*) as n, avg(value) as mean from b group by 1, 2 order by 1")
    return query(sql, params)


def changepoint(log: EvidenceLog, metric_key: str, where: dict | None = None) -> Evidence | None:
    """Find the single month where the metric's level shifted most (binary segmentation on monthly means)."""
    m = METRICS[metric_key]
    s = monthly(metric_key, where)
    s = s[s["n"] >= 10].reset_index(drop=True)
    cut = censor_cutoff(m)
    if cut:
        s = s[s["month"] < pd.Timestamp(cut)].reset_index(drop=True)
    if len(s) < 8:
        return None
    best = None
    for k in range(3, len(s) - 2):
        a, b = s.iloc[:k], s.iloc[k:]
        ma = (a["mean"] * a["n"]).sum() / a["n"].sum()
        mb = (b["mean"] * b["n"]).sum() / b["n"].sum()
        sd = math.sqrt(a["mean"].var(ddof=1) / len(a) + b["mean"].var(ddof=1) / len(b)) or 1e-9
        score = abs(mb - ma) / sd
        if best is None or score > best[0]:
            best = (score, k, ma, mb)
    score, k, ma, mb = best
    month = str(s.iloc[k]["month"].date())
    rel = (mb - ma) / abs(ma) if ma else None
    summary = (f"{m.label}: largest level shift starts {month}: {_fmt(ma, m.unit)} before vs "
               f"{_fmt(mb, m.unit)} after ({_diff_fmt(mb - ma, m.unit)}"
               + (f", {rel:+.1%}" if rel is not None else "") + f"; shift score {score:.1f}).")
    return log.add("changepoint", f"{m.label}: change-point", metric_key, summary,
                   {"month": month, "before": ma, "after": mb, "rel_change": rel, "score": score,
                    "unit": m.unit, "where": where or {}}, f"monthly avg(value) of {metric_key}")


def what_changed(log: EvidenceLog, metric_key: str, month: str, where: dict | None = None,
                 window_days: int = 90) -> Evidence | None:
    """Confounder hunter: which dimension's mix shifted around the change-point, and does it explain the metric?"""
    m = METRICS[metric_key]
    base, params = _base(m, where)
    lo = (date.fromisoformat(month) - timedelta(days=window_days)).isoformat()
    hi = (date.fromisoformat(month) + timedelta(days=window_days)).isoformat()
    shifts = []
    for dim in (d for d in m.dims if not d.endswith("_id")):  # IDs (events, teams) churn by design
        sql = (f"with b as ({base}) select cast(\"{dim}\" as varchar) as cat, "
               f"sum(case when ts < ?::timestamp then 1 else 0 end) as n_before, "
               f"sum(case when ts >= ?::timestamp then 1 else 0 end) as n_after "
               f"from b where ts >= ? and ts < ? group by 1")
        df = query(sql, params + [month, month, lo, hi])
        if df.empty or len(df) > 40:
            continue
        nb, na = df["n_before"].sum(), df["n_after"].sum()
        if nb == 0 or na == 0:
            continue
        df["share_before"] = df["n_before"] / nb * 100
        df["share_after"] = df["n_after"] / na * 100
        df["delta"] = df["share_after"] - df["share_before"]
        top = df.loc[df["delta"].abs().idxmax()]
        if abs(top["delta"]) < 15:
            continue
        # metric for this category vs the rest, over the whole history
        cmp = query(f"with b as ({base}) select coalesce(cast(\"{dim}\" as varchar) = ?, false) as is_cat, count(*) as n, "
                    f"avg(value) as mean from b group by 1", params + [str(top["cat"])])
        cm = {bool(r.is_cat): (int(r.n), float(r.mean)) for r in cmp.itertuples()}
        shifts.append({"dim": dim, "category": top["cat"], "share_before": float(top["share_before"]),
                       "share_after": float(top["share_after"]), "delta_pp": float(top["delta"]),
                       "metric_in_category": cm.get(True, (0, None))[1],
                       "metric_elsewhere": cm.get(False, (0, None))[1],
                       "n_category": cm.get(True, (0, None))[0]})
    shifts.sort(key=lambda s: -abs(s["delta_pp"]))
    if shifts:
        s0 = shifts[0]
        summary = (f"Around {month}, {s0['dim']} = '{s0['category']}' went from {s0['share_before']:.0f}% to "
                   f"{s0['share_after']:.0f}% of records; {m.label} is {_fmt(s0['metric_in_category'], m.unit)} "
                   f"for '{s0['category']}' vs {_fmt(s0['metric_elsewhere'], m.unit)} for the rest.")
    else:
        summary = f"No dimension of {m.label} changed its mix by 15+ pp around {month}."
    return log.add("what_changed", f"{m.label}: what changed around {month}", metric_key, summary,
                   {"month": month, "shifts": shifts[:3], "unit": m.unit, "where": where or {}},
                   f"dimension mix of {metric_key} in [{lo}, {month}) vs [{month}, {hi})")


def segment(log: EvidenceLog, metric_key: str, dim: str, where: dict | None = None,
            since: str | None = None, until: str | None = None) -> Evidence | None:
    m = METRICS[metric_key]
    if dim not in m.dims and dim not in ("treated",):
        return None
    base, params = _base(m, where)
    conds, cp = [], []
    if since:
        conds.append("ts >= ?")
        cp.append(since)
    if until:
        conds.append("ts < ?")
        cp.append(until)
    w = ("where " + " and ".join(conds)) if conds else ""
    sql = (f"with b as ({base}) select cast(\"{dim}\" as varchar) as cat, count(*) as n, avg(value) as mean "
           f"from b {w} group by 1 order by n desc limit 15")
    df = query(sql, params + cp)
    if df.empty:
        return None
    rows = [{"category": r.cat, "n": int(r.n), "mean": float(r.mean)} for r in df.itertuples()]
    ranked = sorted(rows, key=lambda r: r["mean"], reverse=m.higher_is_better)
    best, worst = ranked[0], ranked[-1]
    summary = (f"{m.label} by {dim}: best '{best['category']}' {_fmt(best['mean'], m.unit)} (n={best['n']:,}), "
               f"worst '{worst['category']}' {_fmt(worst['mean'], m.unit)} (n={worst['n']:,}) across {len(rows)} groups.")
    return log.add("segment", f"{m.label} by {dim}", metric_key, summary,
                   {"dim": dim, "rows": rows, "unit": m.unit, "where": where or {}, "since": since},
                   _render_sql(sql, params + cp))
