"""Audit plans: which metric tests a claim, with what design, and which hidden costs to watch.

Plans come from the LLM planner (agent.py). This module provides
  * REGISTERED: reviewed metric bindings for the 8 flagship use cases (used when the LLM is off,
    and shown to the LLM as worked examples),
  * a keyword heuristic for any other use case,
  * validate_plan(): every plan, from any source, is checked against the catalog before it runs.
"""
from __future__ import annotations

import copy

from .catalog import METRICS

DESIGNS = ("did", "treated", "before_after")

REGISTERED: dict[str, dict] = {
    "UC0001": {
        "primary": {"metric": "support_aht", "where": {}}, "design": "did",
        "guardrails": [
            {"metric": "support_csat", "where": {}, "why": "Faster handling must not cost satisfaction."},
            {"metric": "support_reopen", "where": {}, "why": "Rushed answers show up as reopened cases."},
            {"metric": "support_escalation", "where": {}, "why": "Copilot answers may push hard cases upward."},
            {"metric": "support_sla", "where": {}, "why": "Resolution SLA is a Center baseline metric."},
        ],
        "investigations": [{"type": "segment", "metric": "support_escalation", "dim": "category", "where": {}}],
        "rationale": "Copilot went live team by team (teams.ai_copilot_go_live), so non-adopting teams are a "
                     "natural control group: difference-in-differences removes Center-wide trends.",
    },
    "UC0002": {
        "primary": {"metric": "legal_minutes", "where": {"task_type": "Classify"}}, "design": "treated",
        "guardrails": [
            {"metric": "legal_accuracy", "where": {"task_type": "Classify"}, "why": "Speed is worthless if labels are wrong."},
            {"metric": "legal_qa_pass", "where": {"task_type": "Classify"}, "why": "QA failures create rework downstream."},
        ],
        "investigations": [{"type": "compare", "metric": "legal_qa_pass", "where": {"task_type": "Headnote"}}],
        "rationale": "editorial_tasks flags each AI-assisted task, so AI and manual classifications from the "
                     "same period can be compared directly.",
    },
    "UC0003": {
        "primary": {"metric": "xml_rework_rate", "where": {}}, "design": "before_after",
        "guardrails": [
            {"metric": "xml_sla", "where": {}, "why": "Rework drives SLA breaches."},
            {"metric": "xml_hours", "where": {}, "why": "Validation must not slow conversion."},
            {"metric": "production_sla", "where": {}, "why": "XML delays cascade to release."},
        ],
        "investigations": [{"type": "segment", "metric": "xml_rework_rate", "dim": "tool_version", "where": {}}],
        "rationale": "Jobs carry no per-job agent flag, so compare XML conversion before vs after the agent's "
                     "first recorded use, and check what else changed.",
    },
    "UC0004": {
        "primary": {"metric": "ap_exception_hours", "where": {}}, "design": "before_after",
        "guardrails": [
            {"metric": "ap_exception_rate", "where": {}, "why": "Faster triage is moot if exceptions multiply."},
            {"metric": "ap_no_po_rate", "where": {}, "why": "Missing-PO invoices are the top exception driver."},
        ],
        "investigations": [{"type": "segment", "metric": "ap_exception_hours", "dim": "exception_type", "where": {}}],
        "rationale": "Exceptions have no agent flag; compare resolution time a year either side of the pilot.",
    },
    "UC0005": {
        "primary": {"metric": "fraud_fp_rate", "where": {}}, "design": "treated",
        "guardrails": [],
        "investigations": [{"type": "segment", "metric": "fraud_fp_rate", "dim": "rule_name", "where": {}}],
        "rationale": "Alerts from model-based rules vs rule-based rules in the same period isolates the model.",
    },
    "UC0006": {
        "primary": {"metric": "search_click_rate", "where": {}}, "design": "before_after",
        "guardrails": [{"metric": "search_zero_rate", "where": {}, "why": "Zero-result searches are unmet knowledge needs."}],
        "investigations": [{"type": "segment", "metric": "search_zero_rate", "dim": "source", "where": {}}],
        "rationale": "Search success before vs after the RAG pilot, with zero-result rate as the hidden cost.",
    },
    "UC0007": {
        "primary": {"metric": "integrity_prepub_rate", "where": {}}, "design": "treated",
        "guardrails": [],
        "investigations": [{"type": "segment", "metric": "integrity_prepub_rate", "dim": "detected_by", "where": {}}],
        "rationale": "The claim is about catching issues before publication: compare flag timing vs publication date.",
    },
    "UC0008": {
        "primary": {"metric": "lead_conversion", "where": {}}, "design": "before_after",
        "guardrails": [],
        "investigations": [{"type": "segment", "metric": "lead_conversion", "dim": "score_band", "where": {}}],
        "rationale": "Conversion before vs after the scoring pilot; leads need months to convert, so recent "
                     "leads are excluded.",
    },
}

# keyword -> (metric, where) used when no registered plan and no LLM
_KEYWORDS = [
    ("headnote", "legal_minutes", {"task_type": "Headnote"}),
    ("classif", "legal_minutes", {"task_type": "Classify"}),
    ("summar", "legal_minutes", {"task_type": "Summarize"}),
    ("legal content qa", "legal_qa_pass", {}),
    ("regulatory", "legal_update_latency", {}),
    ("xml", "xml_rework_rate", {}),
    ("content release", "production_sla", {}),
    ("book production", "production_sla", {}),
    ("peer review", "manuscript_first_decision_days", {}),
    ("manuscript", "manuscript_first_decision_days", {}),
    ("integrity", "integrity_prepub_rate", {}),
    ("invoice", "ap_exception_rate", {}),
    ("purchase", "ap_no_po_rate", {}),
    ("alert", "fraud_fp_rate", {}),
    ("fraud", "fraud_fp_rate", {}),
    ("lead", "lead_conversion", {}),
    ("search", "search_zero_rate", {}),
    ("knowledge", "search_zero_rate", {}),
    ("ticket", "it_resolution_hours", {}),
    ("incident", "it_resolution_hours", {}),
    ("access", "access_turnaround_hours", {}),
    ("complaint", "support_aht", {}),
    ("case", "support_aht", {}),
    ("customer", "support_aht", {}),
]


def registered_plan(use_case_id: str) -> dict | None:
    plan = REGISTERED.get(use_case_id)
    if not plan:
        return None
    out = copy.deepcopy(plan)
    out["planner"] = "registered binding (reviewed in advance)"
    return out


def heuristic_plan(uc: dict) -> dict | None:
    name = (uc["name"] + " " + (uc.get("process_name") or "")).lower()
    if uc.get("process_has_event_log"):
        metric, where = "process_cycle_hours", {"process_id": uc["process_id"]}
        guard = [{"metric": "process_rework_rate", "where": {"process_id": uc["process_id"]},
                  "why": "Faster cases that loop back are not faster."}]
    else:
        hit = next(((m, w) for kw, m, w in _KEYWORDS if kw in name), None)
        if not hit:
            return None
        metric, where = hit
        guard = []
    m = METRICS[metric]
    design = "treated" if m.has_treated else "before_after"
    return {"primary": {"metric": metric, "where": where}, "design": design, "guardrails": guard,
            "investigations": [], "planner": "keyword heuristic",
            "rationale": f"Matched '{uc['name']}' to {m.label} ({m.source})."}


def validate_plan(plan: dict, cutoff: str | None) -> tuple[dict, list[str]]:
    """Drop anything the catalog cannot run and make the design feasible. Returns (plan, warnings)."""
    warnings: list[str] = []

    def clean_where(metric_key: str, where) -> dict:
        if isinstance(where, list):  # LLM schema uses [{dim, value}]
            where = {w["dim"]: w["value"] for w in where if isinstance(w, dict) and "dim" in w}
        where = dict(where or {})
        bad = [d for d in where if d not in METRICS[metric_key].dims]
        for d in bad:
            warnings.append(f"Dropped unknown filter '{d}' on {metric_key}.")
            where.pop(d)
        return where

    p = plan.get("primary") or {}
    if p.get("metric") not in METRICS:
        raise ValueError(f"Primary metric '{p.get('metric')}' is not in the catalog")
    plan["primary"] = {"metric": p["metric"], "where": clean_where(p["metric"], p.get("where"))}
    m = METRICS[p["metric"]]
    design = plan.get("design")
    feasible = [d for d in DESIGNS if (d != "did" or m.has_adopter) and (d != "treated" or m.has_treated)
                and (d != "before_after" or cutoff)]
    if design not in feasible:
        if feasible:
            warnings.append(f"Design '{design}' not possible for {m.key}; using '{feasible[0]}'.")
            plan["design"] = feasible[0]
        else:
            plan["design"] = None
            warnings.append(f"No comparison design is possible for {m.key} (no AI flag and no rollout date).")
    guards = []
    for g in plan.get("guardrails") or []:
        if g.get("metric") in METRICS and g["metric"] != m.key:
            guards.append({"metric": g["metric"], "where": clean_where(g["metric"], g.get("where")),
                           "why": g.get("why", "")})
        else:
            warnings.append(f"Dropped guardrail '{g.get('metric')}'.")
    plan["guardrails"] = guards[:5]
    invs = []
    for inv in plan.get("investigations") or []:
        if inv.get("metric") not in METRICS or inv.get("type") not in ("segment", "compare", "what_changed"):
            warnings.append(f"Dropped investigation {inv}.")
            continue
        if inv["type"] == "segment" and inv.get("dim") not in METRICS[inv["metric"]].dims:
            warnings.append(f"Dropped segment on unknown dim '{inv.get('dim')}'.")
            continue
        invs.append({"type": inv["type"], "metric": inv["metric"], "dim": inv.get("dim"),
                     "where": clean_where(inv["metric"], inv.get("where"))})
    plan["investigations"] = invs[:4]
    return plan, warnings
