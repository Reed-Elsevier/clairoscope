"""Business translation: how much of the reported value the evidence supports, and a pre-registered
pilot charter (sample size + duration) for the next controlled validation."""
from __future__ import annotations

import math
from datetime import timedelta

from .catalog import METRICS
from .db import query
from .engine import _base, _fmt, as_of
from .robustness import Z95, Z80POWER


def _annual_ai_volume(metric_key: str, where: dict) -> int:
    m = METRICS[metric_key]
    base, params = _base(m, where)
    start = (as_of() - timedelta(days=365)).isoformat()
    cond = " and treated" if m.has_treated else ""
    return int(query(f"with b as ({base}) select count(*) as n from b where ts >= ?{cond}",
                     params + [start])["n"].iloc[0])


def evidence_value(uc: dict, plan: dict | None, eff: dict | None, rub: dict) -> dict:
    """Reported realised value vs the share of it the operational evidence supports."""
    reported = uc["realized_annual_value_usd"]
    out = {"reported_usd": reported, "expected_usd": uc["expected_annual_value_usd"],
           "claimed_hours": uc["hours_saved_annual"], "verified_hours": None, "support_ratio": 0.0,
           "supported_usd": 0.0, "basis": ""}
    if not rub.get("effect_supported") or not plan or not eff:
        out["basis"] = f"Verdict {rub['verdict']}: no robust improvement measured, so none of the reported value is evidenced."
        return out
    m = METRICS[plan["primary"]["metric"]]
    if m.labor and m.unit == "min":
        volume = _annual_ai_volume(m.key, plan["primary"]["where"])
        hours = abs(eff["diff"]) * volume / 60.0
        out["verified_hours"] = hours
        if uc["hours_saved_annual"]:
            out["support_ratio"] = min(1.0, hours / uc["hours_saved_annual"])
            out["basis"] = (f"{abs(eff['diff']):.1f} min saved x {volume:,} AI-assisted records in the last 12 months "
                            f"= {hours:,.0f} staff hours/yr vs {uc['hours_saved_annual']:,.0f} hours claimed.")
        else:
            out["support_ratio"] = min(1.0, rub.get("ratio") or 0.0)
            out["basis"] = f"{hours:,.0f} staff hours/yr verified; no hours claim on record."
    else:
        out["support_ratio"] = min(1.0, rub.get("ratio") or 1.0)
        out["basis"] = (f"{m.label} is not a staff-effort metric; value is scaled by the share of the claimed "
                        f"effect that was observed ({out['support_ratio']:.0%}).")
    if rub["verdict"] == "TRADE-OFF":
        out["basis"] += " Net value is lower still: the hidden cost is not priced in."
    if rub["verdict"] == "CONTRADICTED":
        out["basis"] += " The reported KPI numbers are wrong, but the measured improvement itself is real."
    out["supported_usd"] = reported * out["support_ratio"]
    return out


def pilot_charter(uc: dict, plan: dict | None, rub: dict) -> dict | None:
    """Pre-registration for a controlled pilot: metric, guardrails, margin, sample size, duration, decision rule."""
    if not plan:
        return None
    m = METRICS[plan["primary"]["metric"]]
    base_sql, params = _base(m, plan["primary"]["where"])
    since = (as_of() - timedelta(days=183)).isoformat()
    stats = query(f"with b as ({base_sql}) select count(*) / 6.0 as per_month, avg(value) as mean, "
                  f"stddev_samp(value) as sd from b where ts >= ?", params + [since]).iloc[0]
    per_month, mean, sd = float(stats["per_month"] or 0), float(stats["mean"] or 0), float(stats["sd"] or 0)
    claimed = rub.get("claimed_rel")
    target_rel = abs(claimed) * 0.5 if claimed and math.isfinite(claimed) else 0.05  # no usable claim: 5%
    sesoi = target_rel * abs(mean) if mean else None
    if not sesoi or not sd or not math.isfinite(sesoi) or not math.isfinite(sd):
        return None
    n_arm = math.ceil(2 * (Z95 + Z80POWER) ** 2 * sd ** 2 / sesoi ** 2)
    # at least 2 months so a novelty effect can wear off before the decision
    months = max(2, math.ceil(2 * n_arm / per_month)) if per_month else None
    direction = "increase" if m.higher_is_better else "reduce"
    guards = [f"{METRICS[g['metric']].label} must not worsen by more than "
              f"{'2 pp' if METRICS[g['metric']].unit == '%' else '5%'} (non-inferiority)" for g in plan["guardrails"]]
    return {
        "use_case": f"{uc['use_case_id']} {uc['name']}",
        "primary_metric": f"{m.label} ({m.source})",
        "hypothesis": f"The AI will {direction} {m.label} by at least {target_rel:.0%} "
                      f"(half of the claimed effect) vs the control group.",
        "design": ("Randomise or stagger rollout by team, keep >=20% of teams as a holdout; "
                   "analyse with difference-in-differences and the placebo / pre-trend checks used here."),
        "baseline": _fmt(mean, m.unit), "sd": round(sd, 3), "margin": _fmt(sesoi, m.unit),
        "n_per_arm": n_arm, "volume_per_month": round(per_month),
        "duration_months": months,
        "guardrails": guards,
        "decision_rule": (f"Scale only if the primary effect's 95% CI excludes zero AND reaches the margin "
                          f"({_fmt(sesoi, m.unit)}), AND no guardrail breaches its non-inferiority bound. "
                          f"Analysis date fixed in advance; no early peeking."),
        "note": ("Sample size assumes record-level independence; team-level randomisation needs more data "
                 "(design effect). Margins are a starting point for the business owner to confirm."),
    }
