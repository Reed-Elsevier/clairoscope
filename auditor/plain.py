"""Plain-language view of an audit for non-analysts.

Everything here is deterministic text built from the computed report, so the first screen a leader sees
cannot contain an invented number. The LLM-written headline and summary are shown alongside, and are
themselves grounding-checked in agent.py.
"""
from __future__ import annotations

import re

from .catalog import METRICS

VERDICT_TEXT = {
    "PROVEN": ("Works", "green", "Yes. The benefit is real."),
    "TRADE-OFF": ("Works, with a hidden cost", "amber", "Partly. The benefit is real, but something important got worse."),
    "UNPROVEN": ("Not proven", "grey", "Not proven. The data does not show the claimed benefit."),
    "CONTRADICTED": ("Doesn't hold up", "red", "No. The data contradicts what was reported."),
}
ACTION_TEXT = {
    "scale": "Scale it",
    "fix_then_scale": "Fix first, then scale",
    "keep_monitoring": "Keep going, keep watching",
    "measure_properly": "Measure properly before deciding",
    "pause": "Pause and investigate",
    "stop": "Stop",
}
DESIGN_TEXT = {
    "did": "teams using the AI vs teams not using it, before and after go-live",
    "treated": "work done with the AI vs similar work done without it, same period",
    "before_after": "the year before the AI vs the year after",
}
# Acronyms a non-specialist may not know: spelled out the first time they appear on a page.
# Everyday ones (AI, IT, PDF, ID) are left alone.
GLOSSARY = {
    "CSAT": "customer satisfaction score",
    "SLA": "service-level agreement, the agreed on-time target",
    "QA": "quality assurance",
    "KPI": "key performance indicator",
    "PIA": "privacy impact assessment",
    "AP": "accounts payable",
    "PO": "purchase order",
    "AHT": "average handling time",
    "XML": "the structured file format articles are published in",
    "CX": "customer experience",
    "RAG": "AI that answers from company documents",
    "KYC": "know your customer",
    "FTE": "full-time equivalent",
    "ROI": "return on investment",
    "LLM": "large language model",
    "SOP": "standard operating procedure",
    "UAT": "user acceptance testing",
    "DTD": "document type definition",
}


def explain(text: str, seen: set[str]) -> str:
    """Spell out each glossary acronym at its first appearance; an acronym already given as 'words (ACR)' counts."""
    if not text:
        return text
    for acr, meaning in GLOSSARY.items():
        if acr in seen:
            continue
        if re.search(rf"\({acr}\)|(?<!\w){acr} \(", text):
            seen.add(acr)
            continue
        new, n = re.subn(rf"(?<![\w(]){acr}(?![\w)])", f"{acr} ({meaning})", text, count=1)
        if n:
            text = new
            seen.add(acr)
    return text


def terms_in(*names: str) -> list[str]:
    """Glossary lines for acronyms inside proper names, which are never rewritten."""
    found = [a for a in GLOSSARY if any(re.search(rf"(?<!\w){a}(?!\w)", n or "") for n in names)]
    return [f"{a} means {GLOSSARY[a]}." for a in found]


def fmt(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "%":
        return f"{v:.1f}%"
    if unit == "score":
        return f"{v:.2f}"
    return f"{v:,.1f} {unit}"


def pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:+.0f}%".replace("-", "−")


def _trap(report: dict, *codes: str) -> dict | None:
    return next((t for t in report["traps"] if t["code"] in codes), None)


def _checks(r: dict) -> list[dict]:
    eff, rub, rob = r.get("primary_effect"), r["rubric"], r.get("robustness") or {}
    uc, claim = r["use_case"], r.get("claim") or {}
    checks = []

    md = r.get("measure_design")
    if md:
        spec = md["spec"]
        checks.append({"question": "Is this the right thing to measure?", "status": "warn",
                       "answer": f"Probably, but check it. No standard measure fits this project, so Claude designed "
                                 f"one from the {spec['table'].replace('_', ' ')} records: {spec['why']} "
                                 f"An analyst should confirm it before the result is relied on."})

    t = _trap(r, "KPI_BEFORE_PILOT", "CURRENT_NOT_REPRODUCIBLE", "BASELINE_NOT_REPRODUCIBLE", "CLAIM_SHOWS_DECLINE")
    if t:
        answers = {
            "KPI_BEFORE_PILOT": f"No. The result was reported on {claim.get('measured_date')}, before the pilot "
                                f"started on {uc.get('pilot_date')}.",
            "CURRENT_NOT_REPRODUCIBLE": "No. Operations data shows a very different number from the one reported.",
            "BASELINE_NOT_REPRODUCIBLE": "No. The 'before' number in the report does not match operations data.",
            "CLAIM_SHOWS_DECLINE": "No. The report's own numbers show the metric got worse.",
        }
        checks.append({"question": "Are the reported numbers accurate?", "status": "fail", "answer": answers[t["code"]]})
    elif claim and r.get("claim_level"):
        checks.append({"question": "Are the reported numbers accurate?", "status": "pass",
                       "answer": "Yes. They are consistent with operations data."})
    elif claim:
        checks.append({"question": "Are the reported numbers accurate?", "status": "warn",
                       "answer": "Not checked. Operations data has nothing to compare them with."})

    if _trap(r, "SIMPSON", "PRE_TREND"):
        checks.append({"question": "Was it compared fairly?", "status": "fail",
                       "answer": "No. The AI was used on different kinds of work, so the comparison is skewed."})
    elif any("designs disagree" in x for x in rub["reasons"]):
        checks.append({"question": "Was it compared fairly?", "status": "warn",
                       "answer": "Unclear. Different fair comparisons give different answers."})
    elif eff and eff["design"] in ("did", "treated"):
        checks.append({"question": "Was it compared fairly?", "status": "pass",
                       "answer": f"Yes. We compared {DESIGN_TEXT[eff['design']]}."})
    elif eff:
        checks.append({"question": "Was it compared fairly?", "status": "warn",
                       "answer": "Partly. Only before vs after the AI was available; other things changed too."})

    conf, plac = _trap(r, "CONFOUNDER"), _trap(r, "PLACEBO_FAILED")
    if conf:
        checks.append({"question": "Was it the AI, not something else?", "status": "fail",
                       "answer": "No. " + conf["detail"]})
    elif plac:
        checks.append({"question": "Was it the AI, not something else?", "status": "fail",
                       "answer": "No. The same change was already happening before the AI arrived."})
    elif rob.get("placebo"):
        checks.append({"question": "Was it the AI, not something else?", "status": "pass",
                       "answer": "Yes. Nothing similar happened before the AI arrived."})

    nov = _trap(r, "NOVELTY_DECAY")
    if nov:
        checks.append({"question": "Does the benefit last?", "status": "fail", "answer": "No. " + nov["detail"]})
    elif rob.get("durability") and r["rubric"].get("effect_supported"):
        checks.append({"question": "Does the benefit last?", "status": "pass",
                       "answer": "Yes. The benefit is still there in the most recent quarters."})

    blocked = [t for t in r["traps"] if t["blocks_verdict"]]
    strength = rub.get("evidence_strength")
    if blocked:
        checks.append({"question": "Is there enough data to be sure?", "status": "fail",
                       "answer": "No. " + blocked[0]["title"] + "."})
    elif strength in ("decisive", "claim ruled out"):
        checks.append({"question": "Is there enough data to be sure?", "status": "pass",
                       "answer": "Yes. The result is clear-cut."})
    elif strength == "adequately powered":
        checks.append({"question": "Is there enough data to be sure?", "status": "pass",
                       "answer": "Yes. There was enough data to see the claimed benefit; it just isn't there."})
    elif strength == "underpowered":
        checks.append({"question": "Is there enough data to be sure?", "status": "warn",
                       "answer": "Not quite. The data is too thin to rule the benefit in or out."})

    gov = _trap(r, "GHOST_ADOPTION", "GOVERNANCE_GAP", "EVAL_PROD_GAP")
    if gov:
        answers = {"GHOST_ADOPTION": ("fail", "No. It is reported as live, but nobody's usage is recorded."),
                   "GOVERNANCE_GAP": ("warn", "Not fully. It is live without an approved governance review."),
                   "EVAL_PROD_GAP": ("warn", "Not fully. The model looked better in testing than it performs in real work.")}
        status, answer = answers[gov["code"]]
        checks.append({"question": "Is it run responsibly?", "status": status, "answer": answer})
    else:
        checks.append({"question": "Is it run responsibly?", "status": "pass",
                       "answer": "Yes. Usage is recorded and governance reviews are in place."})
    return checks


def lc(label: str) -> str:
    """Lower-case the first letter unless the word is an acronym (keeps 'CSAT', 'QA', 'XML')."""
    first = label.split(" ")[0]
    return label if first.isupper() else label[0].lower() + label[1:]


def _next_step(r: dict) -> str:
    c, v = r.get("pilot_charter"), r["verdict"]
    if not c:
        return v.get("next_validation_step", "")
    plan = r.get("plan") or {}
    metric = lc(METRICS[plan["primary"]["metric"]].label) if plan else "the main metric"
    guards = [lc(METRICS[g["metric"]].label) for g in plan.get("guardrails", [])][:3]
    if not c["duration_months"] or c["duration_months"] > 6:
        size = (f"A controlled test that could detect a {c['margin']} change would need about "
                f"{c['duration_months']} months at current volume" if c["duration_months"] and c["duration_months"] <= 60
                else "At current volume a controlled test could not detect a realistic change")
        return (f"Fix how this is measured first: re-baseline {metric} from operations data. {size}, so agree "
                f"a bigger, meaningful target or include more teams before testing.")
    text = (f"Run a {c['duration_months']}-month controlled test: keep about 20% of teams working without the AI, "
            f"and scale only if {metric} improves by at least {c['margin']}")
    if guards:
        text += " without " + (", ".join(guards[:-1]) + " or " + guards[-1] if len(guards) > 1 else guards[0]) \
                + " getting worse"
    return text + "."


def build_view(r: dict) -> dict:
    v, rub, eff, plan = r["verdict"], r["rubric"], r.get("primary_effect"), r.get("plan")
    label, color, answer = VERDICT_TEXT[v["verdict"]]
    if any(t["code"] == "NOT_LIVE" for t in r["traps"]):
        label, answer = "Not live yet", "Nothing to check yet. This project is still an idea, so it has no results to verify."
    action = v["recommendation"]["action"]
    unit = METRICS[plan["primary"]["metric"]].unit if plan else ""
    metric_label = METRICS[plan["primary"]["metric"]].label if plan else ""
    claim = r.get("claim") or {}

    claimed_rel = rub.get("claimed_rel")
    if claimed_rel is None and claim.get("baseline"):
        claimed_rel = (claim["current"] - claim["baseline"]) / abs(claim["baseline"])
    hib = METRICS[plan["primary"]["metric"]].higher_is_better if plan else True
    comparison = None
    misreported = any(t["code"] == "CURRENT_NOT_REPRODUCIBLE" and t["severity"] == "high" for t in r["traps"])
    if misreported and claim and r.get("claim_level"):
        actual = r["claim_level"]["mean"]
        misreported = actual < claim["current"] if hib else actual > claim["current"]  # only when reality is worse
    if misreported and claim and r.get("claim_level"):
        eff = None  # the reported number itself is wrong: that is the headline fact
    if eff:  # the AI's effect could be measured: show claimed change vs measured change
        comparison = {
            "kind": "change", "metric": metric_label,
            "said_metric": claim.get("kpi_name") if r.get("measure_design") and claim else None,
            "said": pct(claimed_rel) if claimed_rel is not None else "no number given",
            "found": pct(eff["rel"]),
            "before": fmt(eff["baseline"], unit), "after": fmt(eff["current"], unit),
            "before_raw": eff["baseline"], "after_raw": eff["current"],
            "how": ("this project's own area vs the rest of the organisation, before and after its rollout"
                    if r.get("measure_design") and eff["design"] == "did" else DESIGN_TEXT[eff["design"]]),
            "higher_is_better": hib,
        }
    elif claim and r.get("claim_level"):  # effect not measurable: show reported value vs what operations show
        cl = r["claim_level"]
        comparison = {
            "kind": "level", "metric": metric_label or claim.get("kpi_name", ""),
            "said": fmt(claim["current"], unit), "found": fmt(cl["mean"], unit),
            "before": fmt(claim["current"], unit), "after": fmt(cl["mean"], unit),
            "before_raw": claim["current"], "after_raw": cl["mean"],
            "how": f"value reported on {claim.get('measured_date')} vs operations data around that date",
            "higher_is_better": hib,
        }

    stage = (r["use_case"].get("stage") or "").lower()
    serious = {"scaled": "A serious drop, and this AI is already in use at scale: fix it now.",
               "retired": "A serious drop while this AI was in use."}.get(
        stage, "A serious drop: fix this before rolling it out further.")
    hidden = [{"label": h["label"], "note": serious if h["severity"] == "major" else "", "before": fmt(h.get("before"), h.get("unit", "")),
               "after": fmt(h.get("after"), h.get("unit", "")), "before_raw": h.get("before"),
               "after_raw": h.get("after"), "severity": h["severity"],
               "sentence": f"{h['label']} went from {fmt(h.get('before'), h.get('unit', ''))} to "
                           f"{fmt(h.get('after'), h.get('unit', ''))}."} for h in r["harms"]]

    val = r.get("value") or {}
    reported, supported = val.get("reported_usd", 0), val.get("supported_usd", 0)
    if not reported:
        value_sentence = "No realised value has been reported for this project."
    elif not supported:
        value_sentence = "None of the reported value is backed by the data yet."
    else:
        value_sentence = f"The data backs about {supported / reported:.0%} of the reported value."

    major = next((h for h in hidden if h["severity"] == "major"), None)
    if comparison and comparison["kind"] == "level":
        tagline = f"Reported {comparison['said']}; operations data shows {comparison['found']}"
    elif comparison:
        tagline = f"Claimed {comparison['said']}, measured {comparison['found']} in {lc(comparison['metric'])}"
    else:
        tagline = answer
    if major:
        tagline += f", but {lc(major['label'])} {major['before']} → {major['after']}"
    llm_written = not r["mode"].startswith("deterministic")

    view = {
        "verdict": v["verdict"], "verdict_label": label, "color": color, "answer": answer, "tagline": tagline,
        # a contested headline would contradict the stamp; show the measured facts and the objection instead
        "headline": v["headline"] if llm_written and not r.get("contested") else tagline, "summary": v["summary"],
        "contested": r.get("contested", False),
        "contested_reason": v.get("override_reason", "").split(". ")[0].rstrip(".") + "." if r.get("contested") else "",
        "action": {"key": action, "label": ACTION_TEXT.get(action, action), "text": v["recommendation"]["text"]},
        "claim_text": r.get("claim_text") or (f"{claim.get('kpi_name')}: {claim.get('baseline')} → {claim.get('current')}"
                                              if claim else ""),
        "comparison": comparison,
        "no_test_reason": next((t["detail"] for t in r["traps"] if t["code"] in ("NOT_ATTRIBUTABLE", "NOT_LIVE", "NO_MEASURE")), None),
        "designed_measure": ({"label": r["measure_design"]["spec"]["label"],
                              "table": r["measure_design"]["spec"]["table"]} if r.get("measure_design") else None),
        "hidden_costs": hidden,
        "value": {"reported": f"${reported:,.0f}", "supported": f"${supported:,.0f}",
                  "reported_raw": reported, "supported_raw": supported,
                  "sentence": value_sentence, "basis": val.get("basis", "")},
        "checks": _checks(r),
        "next_step": _next_step(r),
        "use_case": {k: r["use_case"].get(k) for k in ("use_case_id", "name", "division", "stage")},
        "mode": r["mode"],
        "ai_note": ("The AI agent could not be reached, so this result uses the rule-based checks only "
                    "(same verdict rules, template wording). Reason: " + r["llm_errors"][0][:200])
        if r.get("llm_errors") else None,
    }
    return _explain_view(view)


def _explain_view(view: dict) -> dict:
    """Spell out acronyms in reading order, once per page."""
    seen: set[str] = set()
    ex = lambda t: explain(t, seen)  # noqa: E731
    view["headline"], view["summary"] = ex(view["headline"]), ex(view["summary"])
    view["contested_reason"] = ex(view["contested_reason"])
    view["claim_text"] = ex(view["claim_text"])
    view["no_test_reason"] = ex(view["no_test_reason"])
    if view["comparison"]:
        view["comparison"]["metric"] = ex(view["comparison"]["metric"])
    for h in view["hidden_costs"]:
        h["label"], h["sentence"] = ex(h["label"]), ex(h["sentence"])
    for c in view["checks"]:
        c["answer"] = ex(c["answer"])
    view["value"]["sentence"], view["value"]["basis"] = ex(view["value"]["sentence"]), ex(view["value"]["basis"])
    view["next_step"], view["action"]["text"] = ex(view["next_step"]), ex(view["action"]["text"])
    view["terms"] = terms_in(view["use_case"]["name"])
    return view
