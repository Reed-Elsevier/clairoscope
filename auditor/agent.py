"""The audit agent: plan (LLM) -> execute (code) -> investigate (LLM picks follow-ups) -> verdict (LLM),
with deterministic guards around every LLM step:

  * plans are validated against the metric catalog (the LLM never writes SQL),
  * numbers in the LLM's text are checked against the evidence log (numeric grounding),
  * the LLM cannot silently change the rubric verdict; an override must carry a stated reason.

With no LLM configured the same pipeline runs with registered plans and templated text.
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Callable

from . import engine, robustness
from .catalog import METRICS, catalog_for_prompt
from .engine import EvidenceLog
from .facts import get_use_case, kpi_higher_is_better, list_use_cases, rollout_date
from .judge import VERDICTS, effect, find_traps, guardrail_harm, robustness_traps, rubric
from .llm import LLM, LLMError
from .plans import REGISTERED, heuristic_plan, registered_plan, validate_plan
from .value import evidence_value, pilot_charter

Step = Callable[[str, str], None]

# ---------------------------------------------------------------- schemas (strict JSON)
_FILTERS = {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                       "required": ["dim", "value"],
                                       "properties": {"dim": {"type": "string"}, "value": {"type": "string"}}}}
_INVESTIGATION = {"type": "object", "additionalProperties": False, "required": ["type", "metric", "dim", "where"],
                  "properties": {"type": {"type": "string", "enum": ["segment", "compare", "what_changed"]},
                                 "metric": {"type": "string"}, "dim": {"type": ["string", "null"]},
                                 "where": _FILTERS}}
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["hypotheses", "primary", "design", "guardrails", "investigations", "rationale"],
    "properties": {
        "hypotheses": {"type": "array", "items": {"type": "string"}},
        "primary": {"type": "object", "additionalProperties": False, "required": ["metric", "where"],
                    "properties": {"metric": {"type": "string"}, "where": _FILTERS}},
        "design": {"type": "string", "enum": ["did", "treated", "before_after"]},
        "guardrails": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["metric", "where", "why"],
            "properties": {"metric": {"type": "string"}, "where": _FILTERS, "why": {"type": "string"}}}},
        "investigations": {"type": "array", "items": _INVESTIGATION},
        "rationale": {"type": "string"},
    },
}
FOLLOWUP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reasoning", "checks"],
                   "properties": {"reasoning": {"type": "string"},
                                  "checks": {"type": "array", "items": _INVESTIGATION}}}
_CITED = {"type": "object", "additionalProperties": False, "required": ["text", "evidence_ids"],
          "properties": {"text": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}
VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "agrees_with_rubric", "override_reason", "headline", "summary", "findings",
                 "hidden_costs", "recommendation", "next_validation_step", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "agrees_with_rubric": {"type": "boolean"},
        "override_reason": {"type": "string"},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": _CITED},
        "hidden_costs": {"type": "array", "items": _CITED},
        "recommendation": {"type": "object", "additionalProperties": False, "required": ["action", "text"],
                           "properties": {"action": {"type": "string", "enum": [
                               "scale", "fix_then_scale", "keep_monitoring", "measure_properly", "pause", "stop"]},
                               "text": {"type": "string"}}},
        "next_validation_step": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}
CLAIM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["use_case_id", "kpi_name", "baseline", "current", "claimed_change_pct", "higher_is_better",
                 "restated_claim"],
    "properties": {
        "use_case_id": {"type": ["string", "null"]}, "kpi_name": {"type": "string"},
        "baseline": {"type": ["number", "null"]}, "current": {"type": ["number", "null"]},
        "claimed_change_pct": {"type": ["number", "null"]}, "higher_is_better": {"type": "boolean"},
        "restated_claim": {"type": "string"},
    },
}

# ---------------------------------------------------------------- prompts
PLANNER_SYSTEM = """You are the planning stage of an AI Value Auditor for a shared-services center.
A team claims its AI use case improved a KPI. Your job is to design a FAIR test of that claim using the
operational data, not to judge it yet.

Rules:
- Choose metrics ONLY from the provided catalog (use the exact "key"). Filters ("where") may only use a
  metric's listed dims, with values that exist in the data (e.g. task_type "Classify").
- Never filter on a value that identifies the AI itself (e.g. the AI's own model_id or tool name): that
  removes the non-AI comparison group. Filter on the kind of work instead (task_type, category, stage).
- Pick the strongest feasible design for the primary metric:
  "did" (difference-in-differences; needs adopters_for_did) > "treated" (needs per_record_ai_flag)
  > "before_after" (needs a rollout date). The engine will also run the other feasible designs as a cross-check.
- Guardrails are the hidden costs: outcomes the claim does NOT mention that the AI could plausibly hurt
  (quality, accuracy, rework, satisfaction, escalation, SLA, downstream backlog). Pick 1-4. Never repeat the primary.
- Investigations: up to 3 follow-ups that could explain the result (segment by a dim, compare a related
  population, or look for what changed). Prefer ones that could reveal a confounder or a pocket of harm.
- Write 2-3 short hypotheses about how the claim could be wrong.
Return JSON only."""

FOLLOWUP_SYSTEM = """You are the SKEPTIC stage of an AI Value Auditor. Your job is to try to break the emerging
conclusion before a leader acts on it. Read the evidence and ask: what else could explain this result?
(a pre-existing trend, a different mix of work, one segment driving everything, a tool or vendor change,
harm hidden in a sub-population, an effect that fades). Request at most 3 checks that would most likely
OVERTURN the current reading. Use only catalog metrics and dims. In "reasoning", state the strongest
alternative explanation in one or two sentences. Return an empty list only if no credible alternative
remains. Return JSON only."""

VERDICT_SYSTEM = """You are the verdict stage of an AI Value Auditor. Write the audit conclusion for a leader who
must decide whether to scale, fix, pause or stop this AI use case.

Hard rules:
- Every number you write must come from the evidence items or the claim. Do not compute new statistics.
- Cite evidence ids (E1, E2, ...) for every finding and hidden cost.
- A deterministic rubric has already produced a verdict. Agree with it unless the evidence clearly shows the
  rubric is misled (e.g. it ignores a confounder); if you disagree, set agrees_with_rubric=false and explain
  in override_reason. Otherwise override_reason is "".
- Name the hidden costs plainly. Mention evidence traps (e.g. KPI measured before pilot) when relevant.
- Be concise and concrete: headline <= 15 words, summary <= 80 words, 2-5 findings.
- next_validation_step: the single most useful next measurement to make the verdict more certain.
Return JSON only."""


# ---------------------------------------------------------------- helpers
def _filters_to_dict(f) -> dict:
    if isinstance(f, dict):
        return f
    return {x["dim"]: x["value"] for x in (f or []) if isinstance(x, dict) and "dim" in x}


def _dict_to_filters(d: dict) -> list:
    return [{"dim": k, "value": str(v)} for k, v in (d or {}).items()]


def _example_plan() -> dict:
    p = REGISTERED["UC0001"]
    return {
        "hypotheses": ["Handling time fell Center-wide, not only where the copilot is used.",
                       "Faster handling may come with more escalations or reopened cases."],
        "primary": {"metric": p["primary"]["metric"], "where": []}, "design": p["design"],
        "guardrails": [{"metric": g["metric"], "where": [], "why": g["why"]} for g in p["guardrails"][:2]],
        "investigations": [{"type": "segment", "metric": "support_escalation", "dim": "category", "where": []}],
        "rationale": p["rationale"],
    }


def _uc_brief(uc: dict) -> dict:
    keep = ("use_case_id", "name", "division", "process_name", "process_has_event_log", "archetype", "stage",
            "primary_kpi", "model_id", "pilot_date", "scaled_date", "expected_annual_value_usd",
            "realized_annual_value_usd", "usage", "model_versions")
    return {k: uc.get(k) for k in keep}


# ---------------------------------------------------------------- claim parsing (typed claims)
_ALIASES = {
    "UC0001": ["copilot", "support", "handling", "cx", "customer", "aht", "case"],
    "UC0002": ["legal", "classif", "classifier", "auto-classify"],
    "UC0003": ["xml", "validation", "validator"],
    "UC0004": ["invoice", "exception", "ap ", "payable"],
    "UC0005": ["fraud", "alert", "false positive", "aml"],
    "UC0006": ["rag", "knowledge", "search"],
    "UC0007": ["integrity", "screening", "paper mill"],
    "UC0008": ["lead", "scoring", "conversion", "exhibitor"],
}


def _heuristic_claim(text: str) -> dict:
    t = text.lower()
    scores = {uc: sum(a in t for a in al) for uc, al in _ALIASES.items()}
    uc_id = max(scores, key=scores.get) if max(scores.values()) > 0 else None
    m = re.search(r"from\s+([\d.]+)\s*%?\s*(?:min\w*|hours?|h)?\s*to\s+([\d.]+)", t)
    pct = re.search(r"([\d.]+)\s*%", t)
    baseline = current = change = None
    if m:
        baseline, current = float(m.group(1)), float(m.group(2))
    elif pct:
        mag = float(pct.group(1))
        down = any(w in t for w in ("cut", "reduc", "lower", "decreas", "drop", "fewer", "faster", "less"))
        change = -mag if down else mag
    kpi = text.strip()
    return {"use_case_id": uc_id, "kpi_name": kpi, "baseline": baseline, "current": current,
            "claimed_change_pct": change, "higher_is_better": kpi_higher_is_better(kpi),
            "restated_claim": text.strip()}


def parse_claim(text: str, llm: LLM | None) -> tuple[dict, str]:
    if llm:
        try:
            ucs = list_use_cases()
            menu = [{"id": r.use_case_id, "name": r.use_case_name, "stage": r.stage}
                    for r in ucs.head(120).itertuples()]
            out = llm.json(
                "Map a free-text claim about an AI project to the closest use case and extract the claimed KPI "
                "change. Use null when a number is not stated. Return JSON only.",
                json.dumps({"claim": text, "use_cases": menu}), CLAIM_SCHEMA)
            return out, "llm"
        except (LLMError, ValueError, KeyError) as e:
            return _heuristic_claim(text) | {"_error": str(e)}, "heuristic"
    return _heuristic_claim(text), "heuristic"


def claim_from_parsed(p: dict) -> dict:
    base, cur = p.get("baseline"), p.get("current")
    generic = base is None or cur is None
    if generic:
        change = p.get("claimed_change_pct") or 0.0
        base, cur = 100.0, 100.0 * (1 + change / 100.0)
    return {"kpi_name": p.get("kpi_name") or "typed claim", "baseline": base, "current": cur,
            "measured_date": None, "source": "typed by user", "generic_kpi": generic,
            "higher_is_better": bool(p.get("higher_is_better", True))}


# ---------------------------------------------------------------- grounding
_NUM = re.compile(r"(?<![A-Za-z0-9_])[-+]?\$?\d[\d,]*\.?\d*")


def _collect_numbers(obj, out: list) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
        if abs(obj) <= 5:  # relative changes / ratios are also written as percentages
            out.append(float(obj) * 100)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, str):
        for tok in _NUM.findall(obj):
            try:
                out.append(float(tok.replace(",", "").replace("$", "").lstrip("+")))
            except ValueError:
                pass


def grounding_check(texts: list[str], known: list) -> dict:
    pool: list[float] = []
    _collect_numbers(known, pool)
    pool_abs = [abs(x) for x in pool if math.isfinite(x)]  # evidence can hold NaN/inf (e.g. se of n=1)
    checked, unverified = 0, []
    for text in texts:
        for tok in _NUM.findall(text or ""):
            raw = tok.replace(",", "").replace("$", "").lstrip("+")
            try:
                x = abs(float(raw))
            except ValueError:
                continue
            if 1990 <= x <= 2100 and "." not in raw:  # years
                continue
            if x <= 10 and "." not in raw:  # small counts ("3 checks")
                continue
            checked += 1
            ok = any(abs(x - y) <= max(0.051, 0.006 * y) or (x == round(x) and abs(round(y) - x) < 1e-9)
                     or abs(round(y, 1) - x) < 1e-9 for y in pool_abs)
            if not ok:
                unverified.append(tok)
    return {"numbers_checked": checked, "unverified": sorted(set(unverified)),
            "verified": checked - len(unverified)}


# ---------------------------------------------------------------- execution
def _run_design(log: EvidenceLog, design: str, metric: str, where: dict, cutoff: str | None,
                until: str | None):
    if design == "did":
        return engine.diff_in_diff(log, metric, None, where)
    if design == "treated":
        return engine.compare_treated(log, metric, where, until=until)
    if design == "before_after" and cutoff:
        return engine.before_after(log, metric, cutoff, where, until=until)
    return None


def _measure(log: EvidenceLog, design: str, metric: str, where: dict, cutoff: str | None,
             until: str | None) -> tuple[dict | None, list]:
    """Run the planned design first, then every other feasible design as a cross-check."""
    eff, tri = None, []
    for d in [design] + [x for x in ("did", "treated", "before_after") if x != design]:
        e = effect(_run_design(log, d, metric, where, cutoff, until))
        if e is None:
            continue
        if eff is None and d == design:
            eff = e
        else:
            tri.append(e)
    if eff is None and tri:  # planned design had no data; fall back to the next one
        eff = tri.pop(0)
    return eff, tri


def _claim_date_level(log: EvidenceLog, claim: dict | None, metric_key: str, where: dict,
                      censored_until: str | None):
    """What operations showed in the 90 days either side of the date the KPI was reported.

    For metrics with a per-record AI flag only AI-assisted records count; if none exist in that
    window (KPI reported before any AI use) the check is skipped rather than comparing against manual work.
    """
    if not claim or claim.get("generic_kpi") or not claim.get("measured_date"):
        return None
    md = date.fromisoformat(claim["measured_date"])
    start, end = (md - timedelta(days=90)).isoformat(), (md + timedelta(days=90)).isoformat()
    if censored_until and end > censored_until:
        end = censored_until
    if start >= end:
        return None
    treated = True if METRICS[metric_key].has_treated else None
    return engine.level(log, metric_key, where, start=start, end=end, treated=treated,
                        label=f"around the KPI report date {claim['measured_date']}")


def _run_investigation(log: EvidenceLog, inv: dict, cutoff: str | None, cp_month: str | None):
    m = METRICS[inv["metric"]]
    until = engine.censor_cutoff(m)
    if inv["type"] == "segment":
        return engine.segment(log, inv["metric"], inv["dim"], inv["where"], until=until)
    if inv["type"] == "compare":
        if m.has_treated:
            return engine.compare_treated(log, inv["metric"], inv["where"], until=until)
        if cutoff:
            return engine.before_after(log, inv["metric"], cutoff, inv["where"], until=until)
        return None
    month = cp_month or cutoff
    return engine.what_changed(log, inv["metric"], month, inv["where"]) if month else None


def _template_verdict(uc: dict, rub: dict, eff: dict | None, harms: list, traps: list, log: EvidenceLog,
                      inv_evs: list) -> dict:
    v = rub["verdict"]
    findings = []
    if eff:
        findings.append({"text": log.get(eff["evidence"]).summary, "evidence_ids": [eff["evidence"]]})
    for t in traps:
        if t["severity"] == "high":
            findings.append({"text": f"{t['title']}: {t['detail']}", "evidence_ids": t["evidence_ids"]})
    for ev in inv_evs[:2]:
        findings.append({"text": ev.summary, "evidence_ids": [ev.id]})
    action = {"PROVEN": "keep_monitoring" if harms else "scale", "TRADE-OFF": "fix_then_scale",
              "UNPROVEN": "measure_properly", "CONTRADICTED": "pause"}[v]
    headline = {"PROVEN": "The claimed benefit holds up in the operational data",
                "TRADE-OFF": "The benefit is real, but it costs something the claim leaves out",
                "UNPROVEN": "The operational data does not support the claim yet",
                "CONTRADICTED": "The operational data contradicts the claim"}[v]
    return {"verdict": v, "agrees_with_rubric": True, "override_reason": "", "headline": headline,
            "summary": " ".join(rub["reasons"][:3]), "findings": findings[:5],
            "hidden_costs": [{"text": h["text"], "evidence_ids": [h["evidence"]]} for h in harms],
            "recommendation": {"action": action, "text": "Recommendation generated from the rubric (LLM off)."},
            "next_validation_step": "Run a controlled A/B pilot with a pre-registered primary metric and guardrails.",
            "confidence": "medium"}


def run_audit(use_case_id: str | None = None, claim_text: str | None = None, llm: LLM | None = None,
              on_step: Step | None = None) -> dict:
    t0 = time.time()
    trace: list[dict] = []

    def step(name: str, detail: str) -> None:
        trace.append({"step": name, "detail": detail, "t": round(time.time() - t0, 1)})
        if on_step:
            on_step(name, detail)

    mode = llm.name if llm else "deterministic (no LLM)"
    parsed = None
    if claim_text:
        parsed, how = parse_claim(claim_text, llm)
        use_case_id = parsed.get("use_case_id") or use_case_id
        step("Parse claim", f"Mapped to {use_case_id} via {how}: {parsed.get('restated_claim')}")
    if not use_case_id:
        raise ValueError("Could not map the claim to a use case. Pick one from the list.")
    uc = get_use_case(use_case_id)
    if uc is None:
        raise ValueError(f"Unknown use case {use_case_id}")
    claim = claim_from_parsed(parsed) if parsed else uc.get("claim")
    rollout = rollout_date(uc)
    step("Load facts", f"{uc['name']} ({uc['stage']}); rollout {rollout[0]} from {rollout[1]}; "
                       f"{uc['usage']['events']:,} usage events")

    # ---- plan
    plan, planner_note = None, ""
    if llm:
        try:
            payload = {"use_case": _uc_brief(uc), "claim": claim, "rollout_date": rollout[0],
                       "metric_catalog": catalog_for_prompt(),
                       "example_plan_for_a_different_use_case": _example_plan()}
            raw = llm.json(PLANNER_SYSTEM, json.dumps(payload, default=str), PLAN_SCHEMA)
            raw["primary"]["where"] = _filters_to_dict(raw["primary"].get("where"))
            plan = raw | {"planner": f"LLM planner ({llm.name})"}
        except (LLMError, KeyError, TypeError, ValueError) as e:
            planner_note = f"LLM planner failed ({e}); "
    if plan is None:
        plan = registered_plan(uc["use_case_id"]) or heuristic_plan(uc)
    warnings: list[str] = []
    if plan:
        try:
            plan, warnings = validate_plan(plan, rollout[0])
        except ValueError as e:
            warnings.append(str(e))
            plan = registered_plan(uc["use_case_id"]) or heuristic_plan(uc)
            if plan:
                plan, w2 = validate_plan(plan, rollout[0])
                warnings += w2
        if plan and plan["design"] is None:
            plan = None
    step("Plan", planner_note + (f"{plan['planner']}: primary {plan['primary']['metric']} "
                                 f"{plan['primary']['where'] or ''} via {plan['design']}; guardrails "
                                 f"{[g['metric'] for g in plan['guardrails']]}" if plan else "no measurable metric"))

    # ---- execute
    log = EvidenceLog()
    eff, tri, harms, inv_evs = None, [], [], []
    cp = wc = level_ev = acc_ev = claim_level_ev = None
    censored_until = None
    if plan:
        pm, pw = plan["primary"]["metric"], plan["primary"]["where"]
        metric = METRICS[pm]
        censored_until = engine.censor_cutoff(metric)
        eff, tri = _measure(log, plan["design"], pm, pw, rollout[0], censored_until)
        if eff is None and pw:  # plan repair: a filter may have removed the whole comparison group
            for dim in sorted(pw, key=lambda d: not d.endswith("_id")):  # ID-like filters are the usual cause
                trial = {k: v for k, v in pw.items() if k != dim}
                eff, tri = _measure(log, plan["design"], pm, trial, rollout[0], censored_until)
                if eff:
                    warnings.append(f"Plan repair: dropped filter {dim}={pw[dim]!r}; it left no comparison group "
                                    f"(it only matches AI-exposed records).")
                    pw = plan["primary"]["where"] = trial
                    for g in plan["guardrails"]:
                        g["where"].pop(dim, None)
                    step("Plan repair", warnings[-1])
                    break
        if metric.has_treated and eff and eff["design"] != "treated":
            level_ev = engine.level(log, pm, pw, start=rollout[0], treated=True, end=censored_until,
                                    label="AI-assisted records after rollout")
        claim_level_ev = _claim_date_level(log, claim, pm, pw, censored_until)
        step("Measure", log.items[0].summary if log.items else "no comparable data")
        for g in plan["guardrails"]:
            gm = METRICS[g["metric"]]
            gd = eff["design"] if eff else plan["design"]
            ev = _run_design(log, gd, g["metric"], g["where"], rollout[0], engine.censor_cutoff(gm))
            if ev is None:
                for d in ("treated", "before_after"):
                    ev = _run_design(log, d, g["metric"], g["where"], rollout[0], engine.censor_cutoff(gm))
                    if ev is not None:
                        break
            h = guardrail_harm(g["metric"], effect(ev))
            if h:
                harms.append(h)
            if "accuracy" in g["metric"] and gm.has_treated:
                acc_ev = engine.level(log, g["metric"], g["where"], start=rollout[0], treated=True,
                                      label="AI-assisted accuracy")
        step("Hidden costs", f"{len(plan['guardrails'])} guardrails checked; "
                             f"{sum(h['severity'] == 'major' for h in harms)} major / "
                             f"{sum(h['severity'] == 'minor' for h in harms)} minor harms")
        cp = engine.changepoint(log, pm, pw)
        if cp:
            wc = engine.what_changed(log, pm, cp.data["month"], pw)
        step("Confounder hunt", wc.summary if wc else (cp.summary if cp else "no clear change-point"))
        for inv in plan["investigations"]:
            ev = _run_investigation(log, inv, rollout[0], cp.data["month"] if cp else None)
            if ev:
                inv_evs.append(ev)
        # ---- LLM follow-up round
        if llm:
            try:
                fu = llm.json(FOLLOWUP_SYSTEM, json.dumps({
                    "claim": claim, "plan": plan, "evidence": [{"id": e.id, "summary": e.summary} for e in log.items],
                    "metric_catalog": catalog_for_prompt()}, default=str), FOLLOWUP_SCHEMA)
                extra = {"primary": plan["primary"], "design": plan["design"],
                         "investigations": [c | {"where": _filters_to_dict(c.get("where"))} for c in fu["checks"][:3]]}
                extra, w3 = validate_plan(extra, rollout[0])
                warnings += w3
                for inv in extra["investigations"]:
                    ev = _run_investigation(log, inv, rollout[0], cp.data["month"] if cp else None)
                    if ev:
                        inv_evs.append(ev)
                step("Investigate", f"{fu['reasoning'][:200]} -> {len(extra['investigations'])} extra checks")
            except (LLMError, KeyError, TypeError, ValueError) as e:
                step("Investigate", f"skipped ({e})")

    # ---- robustness checks (research-backed falsification tests)
    rob_evs: dict = {}
    pwr = None
    claimed_rel, claim_improves = None, False
    if claim and claim.get("baseline"):
        claimed_rel = (claim["current"] - claim["baseline"]) / abs(claim["baseline"])
        claim_improves = claimed_rel * (1 if claim["higher_is_better"] else -1) > 0
    if plan and eff:
        pm, pw = plan["primary"]["metric"], plan["primary"]["where"]
        since = (log.get(eff["evidence"]).data or {}).get("since")
        rob_evs = {
            "placebo": robustness.placebo(log, pm, eff["design"], rollout[0], pw),
            "pre_trend": robustness.pre_trend(log, pm, pw) if eff["design"] == "did" else None,
            "mix_adjusted": robustness.mix_adjusted(log, pm, eff["design"], pw, rollout[0], since, censored_until),
            "durability": robustness.durability(log, pm, pw, censored_until)
            if eff["design"] in ("treated", "did") else None,
        }
        pwr = robustness.power(eff, claimed_rel, claim_improves, METRICS[pm].unit)
        ran = [k for k, v in rob_evs.items() if v]
        step("Robustness", f"ran {', '.join(ran) or 'none'}" + (f"; {pwr['text']}" if pwr else ""))

    traps = find_traps(uc, claim, plan, rollout, eff, cp, wc, claim_level_ev, level_ev, censored_until, acc_ev)
    traps += robustness_traps(eff, plan["primary"]["metric"] if plan else None, rob_evs.get("placebo"),
                              rob_evs.get("pre_trend"), rob_evs.get("mix_adjusted"), rob_evs.get("durability"))
    rub = rubric(plan, claim, eff, harms, traps, level_ev, claim_level_ev, tri, pwr)
    value = evidence_value(uc, plan, eff, rub)
    charter = pilot_charter(uc, plan, rub)
    step("Rubric", f"{rub['verdict']}: {rub['reasons'][0] if rub['reasons'] else ''}")

    # ---- verdict
    known = [claim, _uc_brief(uc), [e.to_dict() for e in log.items], rub, traps, harms, pwr, value, charter]
    verdict, contested = None, False
    if llm:
        try:
            verdict = llm.json(VERDICT_SYSTEM, json.dumps({
                "use_case": _uc_brief(uc), "claim": claim, "plan": plan,
                "evidence": [{"id": e.id, "title": e.title, "summary": e.summary} for e in log.items],
                "traps": traps, "hidden_cost_flags": harms, "rubric": rub, "power": pwr,
                "evidence_supported_value": value}, default=str), VERDICT_SCHEMA)
            if verdict["verdict"] != rub["verdict"]:
                if verdict.get("override_reason", "").strip():
                    contested = True
                else:
                    verdict["verdict"] = rub["verdict"]
                    verdict["agrees_with_rubric"] = True
        except (LLMError, KeyError, TypeError, ValueError) as e:
            step("Verdict", f"LLM verdict failed ({e}); using rubric template")
    if verdict is None:
        verdict = _template_verdict(uc, rub, eff, harms, traps, log, inv_evs)
    valid_ids = {e.id for e in log.items}
    for item in verdict.get("findings", []) + verdict.get("hidden_costs", []):
        item["evidence_ids"] = [i for i in item.get("evidence_ids", []) if i in valid_ids]
    texts = [verdict["headline"], verdict["summary"], verdict["recommendation"]["text"]] + \
            [f["text"] for f in verdict["findings"]] + [h["text"] for h in verdict["hidden_costs"]]
    grounding = grounding_check(texts, known) if llm else {"numbers_checked": 0, "verified": 0,
                                                           "unverified": [], "note": "templated text"}
    step("Verdict", f"{verdict['verdict']}" + (" (contested by LLM)" if contested else "") +
         f"; numeric grounding {grounding['verified']}/{grounding['numbers_checked']}")

    return {
        "audit_id": uuid.uuid4().hex[:8], "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode, "use_case": uc, "claim": claim, "claim_text": claim_text, "parsed_claim": parsed,
        "rollout": {"date": rollout[0], "source": rollout[1]}, "plan": plan, "plan_warnings": warnings,
        "evidence": [e.to_dict() for e in log.items], "primary_effect": eff, "triangulation": tri,
        "harms": harms, "traps": traps, "rubric": rub, "verdict": verdict, "contested": contested,
        "grounding": grounding, "trace": trace, "censored_until": censored_until,
        "investigation_ids": [e.id for e in inv_evs],
        "changepoint": cp.data if cp else None,
        "robustness": {k: (v.id if v else None) for k, v in rob_evs.items()},
        "power": pwr, "value": value, "pilot_charter": charter,
    }
