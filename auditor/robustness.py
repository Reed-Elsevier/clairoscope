"""Robustness checks borrowed from causal-inference and online-experimentation practice.

  placebo       fake rollout date in the pre-period: a real AI effect should NOT appear there
  pre_trend     difference-in-differences needs parallel pre-trends between adopters and others
  mix_adjusted  Simpson's paradox: re-estimate the effect within strata (category, channel, ...)
  durability    novelty decay: is the effect still there in the latest quarters?
  power         minimum detectable effect + equivalence (TOST): 'no effect' vs 'cannot tell'
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from .catalog import METRICS
from .db import query
from .engine import Evidence, EvidenceLog, MIN_GROUP_N, _base, _diff_fmt, _fmt, _render_sql, before_after

Z90, Z95, Z80POWER = 1.645, 1.96, 0.84


def _ols_slope(y: list[float], x: list[float]) -> tuple[float, float]:
    """Slope and its two-sided p-value (normal approximation)."""
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if n < 4 or sxx == 0:
        return 0.0, 1.0
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    resid = [yi - (my + b * (xi - mx)) for xi, yi in zip(x, y)]
    se = math.sqrt(sum(r * r for r in resid) / (n - 2) / sxx) if n > 2 else float("inf")
    p = math.erfc(abs(b / se) / math.sqrt(2)) if se > 0 else 0.0
    return b, p


def placebo(log: EvidenceLog, metric_key: str, design: str, cutoff: str | None, where: dict) -> Evidence | None:
    """Pretend the AI launched 6 months before it did and use only pre-launch data."""
    m = METRICS[metric_key]
    if design == "did" and m.has_adopter:
        base, params = _base(m, where)
        first = query(f"with b as ({base}) select min(go_live) as g from b where adopter", params)["g"].iloc[0]
        if pd.isna(first):
            return None
        real = pd.Timestamp(first).date()
        fake = (real - timedelta(days=180)).isoformat()
        start = (real - timedelta(days=540)).isoformat()
        sql = (f"with b as ({base}) select adopter, (ts >= ?::timestamp) as post, count(*) as n, avg(value) as mean, "
               f"coalesce(var_samp(value), 0) as var from b where ts >= ? and ts < ? group by 1, 2")
        allp = params + [fake, start, real.isoformat()]
        cells = {(bool(r.adopter), bool(r.post)): r for r in query(sql, allp).itertuples()}
        if len(cells) < 4:
            return None
        tp, tq, cp, cq = cells[(True, False)], cells[(True, True)], cells[(False, False)], cells[(False, True)]
        did = (tq.mean - tp.mean) - (cq.mean - cp.mean)
        se = math.sqrt(sum(c.var / c.n for c in (tp, tq, cp, cq)))
        p = math.erfc(abs(did / se) / math.sqrt(2)) if se > 0 else 1.0
        rel = did / abs(tp.mean) if tp.mean else None
        summary = (f"Placebo: pretending {m.adopter_label} adopted on {fake} (6 months early, pre-launch data only) "
                   f"gives a fake effect of {_diff_fmt(did, m.unit)}" + (f" ({rel:+.1%})" if rel is not None else "")
                   + f", p={p:.2g}. A real AI effect should not appear here.")
        return log.add("placebo", f"{m.label}: placebo rollout {fake}", metric_key, summary,
                       {"design": "did_placebo", "fake_cutoff": fake, "diff": did, "rel_change": rel, "p_value": p,
                        "se": se, "unit": m.unit}, _render_sql(sql, allp))
    if design == "before_after" and cutoff:
        fake = (date.fromisoformat(cutoff) - timedelta(days=180)).isoformat()
        tmp = EvidenceLog()
        ev = before_after(tmp, metric_key, fake, where, window_days=180, until=cutoff)
        if ev is None or "p_value" not in ev.data:
            return None
        d = ev.data
        summary = (f"Placebo: a fake rollout on {fake} (6 months early, pre-launch data only) shows "
                   f"{_diff_fmt(d['diff'], m.unit)}" + (f" ({d['rel_change']:+.1%})" if d["rel_change"] is not None else "")
                   + f", p={d['p_value']:.2g}. A real AI effect should not appear here.")
        return log.add("placebo", f"{m.label}: placebo rollout {fake}", metric_key, summary,
                       {"design": "before_after_placebo", "fake_cutoff": fake, "diff": d["diff"],
                        "rel_change": d["rel_change"], "p_value": d["p_value"], "se": d["se"], "unit": m.unit},
                       ev.sql)
    return None


def pre_trend(log: EvidenceLog, metric_key: str, where: dict) -> Evidence | None:
    """Monthly adopter-minus-others gap before the first go-live should be flat (parallel trends)."""
    m = METRICS[metric_key]
    if not m.has_adopter:
        return None
    base, params = _base(m, where)
    sql = (f"with b as ({base}), g as (select min(go_live) as g0 from b where adopter) "
           f"select date_trunc('month', ts) as month, adopter, avg(value) as mean, count(*) as n from b, g "
           f"where ts < g.g0 and ts >= g.g0 - interval 18 month group by 1, 2 order by 1")
    df = query(sql, params)
    piv = df.pivot_table(index="month", columns="adopter", values="mean").dropna()
    if len(piv) < 6 or True not in piv.columns or False not in piv.columns:
        return None
    gap = (piv[True] - piv[False]).tolist()
    slope, p = _ols_slope(gap, list(range(len(gap))))
    drift12 = slope * 12
    summary = (f"Pre-trend check: before any team adopted, the {m.adopter_label}-minus-others gap in {m.label} "
               f"drifted {_diff_fmt(drift12, m.unit)} per year (p={p:.2g}) over {len(gap)} months. "
               + ("Parallel trends look plausible." if p >= 0.05 or abs(drift12) < 1e-9 else
                  "Groups were already diverging: diff-in-diff may be biased."))
    return log.add("pre_trend", f"{m.label}: parallel pre-trends", metric_key, summary,
                   {"slope_per_month": slope, "drift_per_year": drift12, "p_value": p, "months": len(gap),
                    "unit": m.unit}, _render_sql(sql, params))


def mix_adjusted(log: EvidenceLog, metric_key: str, design: str, where: dict, cutoff: str | None,
                 since: str | None, until: str | None) -> Evidence | None:
    """Simpson's-paradox check: effect within strata of the first usable dimension, weighted by AI volume."""
    m = METRICS[metric_key]
    if design == "treated" and m.has_treated:
        group = "treated"
        conds = ["ts >= ?"] if since else []
        cp = [since] if since else []
    elif design == "before_after" and cutoff:
        group = "(ts >= ?::timestamp)"
        start = (date.fromisoformat(cutoff) - timedelta(days=365)).isoformat()
        end = (date.fromisoformat(cutoff) + timedelta(days=365)).isoformat()
        conds, cp = ["ts >= ?", "ts < ?"], [start, end]
    else:
        return None
    if until:
        conds.append("ts < ?")
        cp.append(until)
    base, params = _base(m, where)
    w = ("where " + " and ".join(conds)) if conds else ""
    for dim in (d for d in m.dims if not d.endswith("_id") and d not in where):
        gp = [cutoff] if group.startswith("(ts") else []
        sql = (f"with b as ({base}) select cast(\"{dim}\" as varchar) as cat, {group} as grp, count(*) as n, "
               f"avg(value) as mean from b {w} group by 1, 2")
        allp = params + gp + cp
        df = query(sql, allp)
        if df.empty or df["cat"].nunique() > 15:
            continue
        piv = df.pivot_table(index="cat", columns="grp", values=["n", "mean"])
        if (True not in piv["n"].columns) or (False not in piv["n"].columns):
            continue
        both = piv.dropna()
        both = both[(both[("n", True)] >= 10) & (both[("n", False)] >= 10)]
        if len(both) < 2:
            continue
        wts = both[("n", True)] / both[("n", True)].sum()
        adj = float((wts * (both[("mean", True)] - both[("mean", False)])).sum())
        cmp_mean = float((wts * both[("mean", False)]).sum())
        tot = df.groupby("grp").apply(lambda g: (g["n"] * g["mean"]).sum() / g["n"].sum(), include_groups=False)
        raw = float(tot[True] - tot[False])
        coverage = float(both[("n", True)].sum() / piv[("n", True)].sum())
        summary = (f"Mix-adjusted effect within {dim} strata: {_diff_fmt(adj, m.unit)} vs raw {_diff_fmt(raw, m.unit)} "
                   f"({len(both)} strata, {coverage:.0%} of AI records). "
                   + ("Consistent: the effect is not a mix artefact." if (adj * raw > 0 and abs(adj) >= 0.5 * abs(raw))
                      else "Inconsistent: the headline effect is driven by WHERE the AI was used (Simpson's paradox)."))
        return log.add("mix_adjusted", f"{m.label}: mix-adjusted by {dim}", metric_key, summary,
                       {"dim": dim, "adjusted_diff": adj, "raw_diff": raw, "strata": len(both),
                        "rel_change": adj / abs(cmp_mean) if cmp_mean else None, "coverage": coverage,
                        "unit": m.unit}, _render_sql(sql, allp))
    return None


def durability(log: EvidenceLog, metric_key: str, where: dict, until: str | None) -> Evidence | None:
    """Novelty check: AI vs non-AI difference per quarter since the AI started."""
    m = METRICS[metric_key]
    if not m.has_treated:
        return None
    base, params = _base(m, where)
    cond, cp = ("where ts < ?", [until]) if until else ("", [])
    sql = (f"with b as ({base}), s as (select min(ts) as t0 from b where treated) "
           f"select date_trunc('quarter', ts) as q, treated, count(*) as n, avg(value) as mean "
           f"from b, s {cond + (' and' if cond else 'where')} ts >= s.t0 group by 1, 2 order by 1")
    df = query(sql, params + cp)
    piv = df.pivot_table(index="q", columns="treated", values=["mean", "n"]).dropna()
    piv = piv[(piv[("n", True)] >= MIN_GROUP_N) & (piv[("n", False)] >= MIN_GROUP_N)]
    if len(piv) < 4:
        return None
    eff = (piv[("mean", True)] - piv[("mean", False)]).tolist()
    quarters = [str(pd.Timestamp(q).date()) for q in piv.index]
    early, late = sum(eff[:2]) / 2, sum(eff[-2:]) / 2
    retained = late / early if early else None
    summary = (f"Durability: AI-vs-non-AI gap in {m.label} was {_diff_fmt(early, m.unit)} in the first two quarters "
               f"and {_diff_fmt(late, m.unit)} in the latest two"
               + (f" ({retained:.0%} retained)." if retained is not None else "."))
    return log.add("durability", f"{m.label}: effect by quarter", metric_key, summary,
                   {"quarters": quarters, "effects": eff, "early": early, "late": late, "retained": retained,
                    "unit": m.unit}, _render_sql(sql, params + cp))


def power(primary: dict | None, claimed_rel: float | None, claim_improves: bool, unit: str) -> dict | None:
    """Minimum detectable effect (80% power, 5% two-sided) and an equivalence test against half the claim."""
    if not primary or not primary.get("se") or not math.isfinite(primary["se"]):
        return None
    se, diff, base = primary["se"], primary["diff"], primary["baseline"]
    mde = (Z95 + Z80POWER) * se
    claimed_abs = abs(claimed_rel * base) if (claimed_rel is not None and claim_improves and base) else None
    sesoi = 0.5 * claimed_abs if claimed_abs else 0.05 * abs(base) if base else None
    lo, hi = diff - Z90 * se, diff + Z90 * se
    equivalent = sesoi is not None and -sesoi < lo and hi < sesoi
    detectable = claimed_abs is not None and mde < claimed_abs
    return {"se": se, "mde": mde, "claimed_abs": claimed_abs, "sesoi": sesoi, "ci90": [lo, hi],
            "equivalent": equivalent, "claim_detectable": detectable,
            "text": (f"Minimum detectable effect {_fmt(mde, unit)} (80% power); "
                     f"claimed effect {_fmt(claimed_abs, unit) if claimed_abs else 'n/a'}; "
                     f"90% CI of observed effect [{lo:.2f}, {hi:.2f}]"
                     + (f"; equivalence margin ±{sesoi:.2f}" if sesoi else ""))}
