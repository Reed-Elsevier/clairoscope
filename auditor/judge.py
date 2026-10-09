"""Evidence traps and the deterministic verdict rubric.

The rubric is the auditor's referee: the LLM may explain or contest its verdict, but cannot
silently replace it (see agent.py). Thresholds are deliberately simple so a judge can read them.
"""
from __future__ import annotations

from datetime import date

from .catalog import METRICS
from .engine import Evidence, MIN_GROUP_N, _diff_fmt, _fmt

VERDICTS = ("PROVEN", "TRADE-OFF", "UNPROVEN", "CONTRADICTED")
SUPPORT_RATIO = 0.5  # observed effect must reach half the claimed effect
LEVEL_GAP_HIGH = 0.5  # observed level >50% worse than claimed = claim contradicted
BASELINE_GAP = 0.25


def effect(ev: Evidence | None) -> dict | None:
    """Normalise any comparison evidence to: diff, rel, p, ai/comparison levels and sizes."""
    if ev is None or "p_value" not in ev.data:
        return None
    d = ev.data
    if d["design"] == "did":
        ai, cmp_, base, cur = d["adopters_post"], d["others_post"], d["adopters_pre"]["mean"], d["adopters_post"]["mean"]
    elif d["design"] == "treated":
        ai, cmp_, base, cur = d["ai"], d["comparison"], d["comparison"]["mean"], d["ai"]["mean"]
    else:
        ai, cmp_, base, cur = d["post"], d["pre"], d["pre"]["mean"], d["post"]["mean"]
    return {"design": d["design"], "diff": d["diff"], "rel": d["rel_change"], "p": d["p_value"], "se": d.get("se"),
            "baseline": base, "current": cur, "n_ai": ai["n"], "n_cmp": cmp_["n"], "evidence": ev.id}


def _better_sign(metric_key: str) -> int:
    return 1 if METRICS[metric_key].higher_is_better else -1


def guardrail_harm(metric_key: str, eff: dict | None) -> dict | None:
    """Classify a guardrail movement as 'major' / 'minor' harm, or None."""
    if not eff or eff["p"] >= 0.05 or eff["rel"] is None:
        return None
    m = METRICS[metric_key]
    worse_rel = -eff["rel"] * _better_sign(metric_key)
    worse_abs = -eff["diff"] * _better_sign(metric_key)
    if worse_rel <= 0:
        return None
    if worse_rel >= 0.15 or (m.unit == "%" and worse_abs >= 5):
        sev = "major"
    elif worse_rel >= 0.05 or (m.unit == "%" and worse_abs >= 1):
        sev = "minor"
    else:
        return None
    return {"metric": metric_key, "label": m.label, "severity": sev, "evidence": eff["evidence"],
            "unit": m.unit, "before": eff["baseline"], "after": eff["current"],
            "text": f"{m.label} worsened {_diff_fmt(eff['diff'], m.unit)} ({eff['rel']:+.1%}), p={eff['p']:.2g}"}


REFERENCES = {
    "KPI_BEFORE_PILOT": "NIST AI RMF MEASURE 4.3 (improvements or declines identified and documented)",
    "BASELINE_NOT_REPRODUCIBLE": "NIST AI RMF MEASURE 4.3",
    "CURRENT_NOT_REPRODUCIBLE": "NIST AI RMF MEASURE 4.3",
    "CLAIM_SHOWS_DECLINE": "NIST AI RMF MEASURE 4.3",
    "EVAL_PROD_GAP": "NIST AI RMF MEASURE 2.3 (performance demonstrated in deployment-like conditions)",
    "GOVERNANCE_GAP": "Center policy: governance reviews mandatory since 2025 (challenge brief)",
    "PLACEBO_FAILED": "Falsification test (difference-in-differences practice)",
    "PRE_TREND": "Parallel-trends assumption (Callaway & Sant'Anna 2021; event-study practice)",
    "SIMPSON": "Simpson's paradox / mix shift",
    "NOVELTY_DECAY": "Novelty effect (Kohavi, Tang & Xu, Trustworthy Online Controlled Experiments)",
    "RIGHT_CENSORING": "Outcome lag / right-censoring",
    "CONFOUNDER": "Confounding change (metric-drift checklist)",
}

ROBUSTNESS_DOWNGRADE = {"PLACEBO_FAILED", "PRE_TREND", "SIMPSON"}


def _trap(code, severity, title, detail, evidence=None, blocks=False) -> dict:
    return {"code": code, "severity": severity, "title": title, "detail": detail,
            "evidence_ids": [e for e in (evidence or []) if e], "blocks_verdict": blocks,
            "reference": REFERENCES.get(code, "")}


def robustness_traps(primary: dict | None, metric_key: str | None, placebo_ev: Evidence | None,
                     pretrend_ev: Evidence | None, mix_ev: Evidence | None, dur_ev: Evidence | None) -> list[dict]:
    traps: list[dict] = []
    if not primary or not metric_key:
        return traps
    m = METRICS[metric_key]
    sign = _better_sign(metric_key)
    if placebo_ev and primary["rel"] is not None:
        d = placebo_ev.data
        if d["p_value"] < 0.05 and d["rel_change"] is not None and d["rel_change"] * primary["rel"] > 0 \
                and abs(d["rel_change"]) >= 0.5 * abs(primary["rel"]):
            traps.append(_trap("PLACEBO_FAILED", "high", "Placebo test failed: the 'effect' predates the AI",
                               f"A fake rollout 6 months early already shows {d['rel_change']:+.1%} "
                               f"(real: {primary['rel']:+.1%}). The change is likely a pre-existing trend.",
                               [placebo_ev.id]))
    if pretrend_ev:
        d = pretrend_ev.data
        if d["p_value"] < 0.05 and abs(d["drift_per_year"]) >= 0.5 * abs(primary["diff"]):
            traps.append(_trap("PRE_TREND", "high", "Adopters and others were already diverging",
                               f"Before any go-live the gap drifted {_diff_fmt(d['drift_per_year'], m.unit)} per "
                               f"year; diff-in-diff assumes parallel trends.", [pretrend_ev.id]))
    if mix_ev:
        d = mix_ev.data
        if not (d["adjusted_diff"] * d["raw_diff"] > 0 and abs(d["adjusted_diff"]) >= 0.5 * abs(d["raw_diff"])):
            traps.append(_trap("SIMPSON", "high", "Effect disappears within comparable work (Simpson's paradox)",
                               f"Raw {_diff_fmt(d['raw_diff'], m.unit)} vs {_diff_fmt(d['adjusted_diff'], m.unit)} "
                               f"within {d['dim']} strata: the AI was used on easier/different work.", [mix_ev.id]))
    if dur_ev and dur_ev.data["retained"] is not None:
        d = dur_ev.data
        improving_early = d["early"] * sign > 0
        if improving_early and d["retained"] < 0.5:
            traps.append(_trap("NOVELTY_DECAY", "medium", "Benefit is fading",
                               f"The AI-vs-non-AI gap shrank to {d['retained']:.0%} of its early size.", [dur_ev.id]))
    return traps


def claimed_level_ref(primary: dict | None, level_ev: Evidence | None,
                      claim_level_ev: Evidence | None) -> tuple[float | None, str | None, str]:
    """Observed value to hold the claimed 'current' number against, best source first."""
    if claim_level_ev:
        return claim_level_ev.data["mean"], claim_level_ev.id, "around the date the KPI was reported"
    if level_ev:
        return level_ev.data["mean"], level_ev.id, "for AI-exposed work"
    if primary:
        return primary["current"], primary["evidence"], "for AI-exposed work"
    return None, None, ""


def find_traps(uc: dict, claim: dict | None, plan: dict | None, rollout: tuple, primary: dict | None,
               cp: Evidence | None, wc: Evidence | None, claim_level_ev: Evidence | None,
               level_ev: Evidence | None, censored_until: str | None,
               accuracy_level: Evidence | None) -> list[dict]:
    traps: list[dict] = []
    cutoff, cutoff_src = rollout
    design = plan["design"] if plan else None
    live = uc["stage"] in ("Pilot", "Scaled")

    if plan is None:
        traps.append(_trap("NO_MEASURE", "high", "No operational data can test this claim",
                           "None of the catalog's operational metrics measures this use case's work, so the claim "
                           "is self-reported only.", blocks=True))
    if claim and uc.get("pilot_date") and claim.get("measured_date") and claim["measured_date"] < uc["pilot_date"]:
        traps.append(_trap("KPI_BEFORE_PILOT", "high", "KPI was measured before the pilot started",
                           f"The claimed result is dated {claim['measured_date']}, but the pilot began "
                           f"{uc['pilot_date']}. The reported number cannot reflect the AI in production.",
                           blocks=(design == "before_after")))
    if claim:
        hib = claim["higher_is_better"]
        if (claim["current"] < claim["baseline"]) == hib and claim["current"] != claim["baseline"]:
            traps.append(_trap("CLAIM_SHOWS_DECLINE", "medium", "The claim's own numbers show deterioration",
                               f"'{claim['kpi_name']}' moved {claim['baseline']} -> {claim['current']}, which is "
                               f"worse for a {'higher' if hib else 'lower'}-is-better KPI, yet the use case is "
                               f"reported at stage '{uc['stage']}'."))
    if claim and plan and not claim.get("generic_kpi"):
        unit = METRICS[plan["primary"]["metric"]].unit
        if primary and claim["baseline"]:
            gap = (primary["baseline"] - claim["baseline"]) / abs(claim["baseline"])
            if abs(gap) > BASELINE_GAP:
                traps.append(_trap("BASELINE_NOT_REPRODUCIBLE", "high" if abs(gap) > LEVEL_GAP_HIGH else "medium",
                                   "Claimed baseline does not match the operational data",
                                   f"Claimed baseline {claim['baseline']} vs observed {_fmt(primary['baseline'], unit)} "
                                   f"in the comparison period ({gap:+.0%}).", [primary["evidence"]]))
        ref, ref_id, where_txt = claimed_level_ref(primary, level_ev, claim_level_ev)
        if ref is not None and claim["current"]:
            gap = (ref - claim["current"]) / abs(claim["current"])
            if abs(gap) > BASELINE_GAP:
                traps.append(_trap("CURRENT_NOT_REPRODUCIBLE", "high" if abs(gap) > LEVEL_GAP_HIGH else "medium",
                                   "Claimed current value does not match the operational data",
                                   f"Claimed current {claim['current']} vs observed {_fmt(ref, unit)} "
                                   f"{where_txt} ({gap:+.0%}).", [ref_id]))
    if censored_until and plan:
        m = METRICS[plan["primary"]["metric"]]
        blocks = design == "before_after" and cutoff and censored_until <= cutoff
        traps.append(_trap("RIGHT_CENSORING", "high" if blocks else "low",
                           "Recent outcomes are not known yet",
                           f"{m.label} takes up to ~{m.outcome_lag_days} days to materialise, so records after "
                           f"{censored_until} were excluded."
                           + (f" That removes the whole post-rollout period (rollout {cutoff}): too early to judge."
                              if blocks else ""), blocks=bool(blocks)))
    if live and uc["usage"]["events"] == 0 and not (primary and primary["design"] in ("treated", "did")):
        traps.append(_trap("GHOST_ADOPTION", "high", "No recorded usage",
                           f"Stage is '{uc['stage']}' but ai_usage_events has no events for this use case."))
    if live and not any(g["status"].startswith("Approved") for g in uc["governance"]):
        traps.append(_trap("GOVERNANCE_GAP", "medium", "Live without an approved governance review",
                           f"Stage '{uc['stage']}' but no approved PIA / threat model / model-risk review on record."))
    prod = [v for v in uc.get("model_versions", []) if v["status"] == "Production" and v["eval_accuracy"]]
    if prod and accuracy_level:
        gap = prod[-1]["eval_accuracy"] - accuracy_level.data["mean"]
        if gap > 0.05:
            traps.append(_trap("EVAL_PROD_GAP", "medium", "Offline evaluation overstates production quality",
                               f"Model {uc['model_id']} {prod[-1]['version']} reports eval accuracy "
                               f"{prod[-1]['eval_accuracy']:.3f}; on real AI-assisted work it scores "
                               f"{accuracy_level.data['mean']:.3f}.", [accuracy_level.id]))
    if cp and cp.data["score"] >= 3:
        rel = cp.data["rel_change"]
        far = (cutoff is None) or abs((date.fromisoformat(cp.data["month"]) - date.fromisoformat(cutoff)).days) > 60
        shift = (wc.data["shifts"][0] if wc and wc.data["shifts"] else None)
        if far and rel is not None and abs(rel) >= 0.15:
            unit = cp.data["unit"]
            detail = (f"The biggest shift in the metric starts {cp.data['month']} "
                      f"({_fmt(cp.data['before'], unit)} -> {_fmt(cp.data['after'], unit)}), "
                      f"{'with no rollout date' if cutoff is None else f'not at the AI rollout ({cutoff}, {cutoff_src})'}.")
            if shift:
                detail += (f" At the same time {shift['dim']} '{shift['category']}' went from "
                           f"{shift['share_before']:.0f}% to {shift['share_after']:.0f}% of records.")
            traps.append(_trap("CONFOUNDER", "high", "Something other than the AI moved this metric", detail,
                               [cp.id, wc.id if wc else None]))
    if primary and primary["n_ai"] < 100:
        traps.append(_trap("SMALL_SAMPLE", "medium", "Small AI sample",
                           f"Only {primary['n_ai']} AI-exposed records; treat the effect size as indicative.",
                           [primary["evidence"]]))
    if primary is None and plan and plan.get("design") == "before_after":
        traps.append(_trap("NO_PRE_PERIOD", "high", "No usable comparison period",
                           f"Too little data on one side of {cutoff} (minimum {MIN_GROUP_N} records each).",
                           blocks=True))
    if uc["stage"] == "Scaled" and uc["expected_annual_value_usd"] and \
            uc["realized_annual_value_usd"] < 0.5 * uc["expected_annual_value_usd"]:
        traps.append(_trap("VALUE_GAP", "low", "Realised value under half of the business case",
                           f"${uc['realized_annual_value_usd']:,.0f} realised vs ${uc['expected_annual_value_usd']:,.0f} expected."))
    return traps


def _level_contradiction(plan: dict | None, claim: dict | None, primary: dict | None,
                         level_ev: Evidence | None, claim_level_ev: Evidence | None) -> str | None:
    """The claimed 'current' value is >50% worse-than-claimed in the operational data."""
    if not (plan and claim and not claim.get("generic_kpi") and claim["current"]):
        return None
    mkey = plan["primary"]["metric"]
    ref, _, where_txt = claimed_level_ref(primary, level_ev, claim_level_ev)
    if ref is None:
        return None
    gap_worse = -(ref - claim["current"]) / abs(claim["current"]) * _better_sign(mkey)
    if gap_worse <= LEVEL_GAP_HIGH:
        return None
    return (f"The reported {claim['kpi_name']} of {claim['current']} cannot be reproduced: operations show "
            f"{_fmt(ref, METRICS[mkey].unit)} {where_txt}.")


def rubric(plan: dict | None, claim: dict | None, primary: dict | None, harms: list[dict],
           traps: list[dict], level_ev: Evidence | None, claim_level_ev: Evidence | None = None,
           triangulation: list[dict] | None = None, pw: dict | None = None) -> dict:
    reasons: list[str] = []
    contradiction = _level_contradiction(plan, claim, primary, level_ev, claim_level_ev)
    blockers = [t["title"] for t in traps if t["blocks_verdict"]]
    if primary is None and not blockers:
        blockers = ["no comparison could be computed"]
    if blockers:
        if contradiction:
            return {"verdict": "CONTRADICTED", "claimed_rel": None, "observed_rel": None, "ratio": None,
                    "reasons": [contradiction, "The AI's effect itself cannot be evaluated yet: " + "; ".join(blockers)]}
        return {"verdict": "UNPROVEN", "reasons": ["Cannot be evaluated yet: " + "; ".join(blockers)],
                "claimed_rel": None, "observed_rel": None, "ratio": None}
    mkey = plan["primary"]["metric"]
    m = METRICS[mkey]
    sign = _better_sign(mkey)
    obs_rel = primary["rel"] or 0.0
    improved = obs_rel * sign > 0
    significant = primary["p"] < 0.05
    disagree = [t for t in (triangulation or []) if t["n_ai"] >= MIN_GROUP_N and t["n_cmp"] >= MIN_GROUP_N
                and ((t["p"] >= 0.05) or ((t["rel"] or 0) * sign > 0) != improved)]
    claimed_rel = None
    claim_is_improvement = False
    if claim and claim["baseline"]:
        claimed_rel = (claim["current"] - claim["baseline"]) / abs(claim["baseline"])
        kpi_sign = 1 if claim["higher_is_better"] else -1
        claim_is_improvement = claimed_rel * kpi_sign > 0
    ratio = abs(obs_rel) / abs(claimed_rel) if claimed_rel and claim_is_improvement else None
    if ratio is not None:
        supported = improved and significant and ratio >= SUPPORT_RATIO
    else:
        supported = improved and significant and abs(obs_rel) >= 0.05
    worse = (not improved) and significant and abs(obs_rel) >= 0.05 and not disagree
    if contradiction:
        reasons.append(contradiction)
    reasons.append(f"Observed effect on {m.label}: {_diff_fmt(primary['diff'], m.unit)} ({obs_rel:+.1%}, "
                   f"p={primary['p']:.2g}, design={primary['design']}).")
    if claimed_rel is not None:
        reasons.append(f"Claimed effect: {claimed_rel:+.1%}" +
                       (f"; observed reaches {ratio:.0%} of it." if ratio is not None else
                        " (claim itself is not an improvement)."))
    major = [h for h in harms if h["severity"] == "major"]
    for h in harms:
        reasons.append(f"Hidden cost ({h['severity']}): {h['text']}.")

    if disagree:
        reasons.append("Cross-check designs disagree: " + "; ".join(
            f"{t['design']} shows {t['rel']:+.1%} (p={t['p']:.2g})" for t in disagree if t["rel"] is not None) + ".")
    fragile = [t for t in traps if t["code"] in ROBUSTNESS_DOWNGRADE]
    if supported and fragile:
        supported = False
        reasons.append("The improvement fails a robustness check: " + "; ".join(t["title"] for t in fragile) + ".")
    confounded = (primary is not None and primary.get("design") == "before_after"
                  and any(t["code"] == "CONFOUNDER" for t in traps))
    if supported and confounded:  # with no comparison group, a coinciding change cannot be told apart from the AI
        supported = False
        reasons.append("Before vs after has no comparison group, and something other than the AI moved this metric.")
    ruled_out = bool(pw and pw["equivalent"] and claim_is_improvement and not supported)
    strength = None
    if pw:
        if supported or worse:
            strength = "decisive"
        elif ruled_out:
            strength = "claim ruled out"
        elif pw["claim_detectable"]:
            strength = "adequately powered"
        else:
            strength = "underpowered"
    if worse or contradiction:
        verdict = "CONTRADICTED"
    elif supported and major:
        verdict = "TRADE-OFF"
    elif supported:
        verdict = "PROVEN"
    elif ruled_out:
        verdict = "CONTRADICTED"
        reasons.append(f"Equivalence test: the 90% CI of the effect [{pw['ci90'][0]:.2f}, {pw['ci90'][1]:.2f}] lies "
                       f"inside ±{pw['sesoi']:.2f}, ruling out even half of the claimed effect.")
    else:
        verdict = "UNPROVEN"
        if not significant:
            reasons.append("The observed change is not statistically distinguishable from zero.")
        elif ratio is not None and ratio < SUPPORT_RATIO:
            reasons.append("The observed improvement is less than half of what was claimed.")
        if strength == "adequately powered":
            reasons.append("The data had 80% power to detect the claimed effect, and did not.")
        elif strength == "underpowered":
            reasons.append("Inconclusive: the data cannot detect an effect as small as the one claimed.")
    return {"verdict": verdict, "reasons": reasons, "claimed_rel": claimed_rel, "observed_rel": obs_rel,
            "ratio": ratio, "evidence_strength": strength, "effect_supported": supported}
