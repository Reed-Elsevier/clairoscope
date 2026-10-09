"""The LLM guards work: plans are validated, verdict overrides need a reason, invented numbers are caught.
Uses a scripted fake LLM, so no API key is needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor.agent import FOLLOWUP_SCHEMA, PLAN_SCHEMA, VERDICT_SCHEMA, run_audit  # noqa: E402


class FakeLLM:
    name = "fake"

    def __init__(self, plan: dict, verdict: dict):
        self.plan, self.verdict = plan, verdict

    def json(self, system, user, schema):
        if schema is PLAN_SCHEMA:
            return self.plan
        if schema is FOLLOWUP_SCHEMA:
            return {"reasoning": "check model segment", "checks": [
                {"type": "segment", "metric": "legal_qa_pass", "dim": "model_id", "where": []}]}
        if schema is VERDICT_SCHEMA:
            return self.verdict
        raise AssertionError("unexpected schema")


GOOD_PLAN = {
    "hypotheses": ["Speed came at the cost of quality."],
    "primary": {"metric": "legal_minutes", "where": [{"dim": "task_type", "value": "Classify"}]},
    "design": "treated",
    "guardrails": [{"metric": "legal_qa_pass", "where": [{"dim": "task_type", "value": "Classify"}],
                    "why": "QA failures"},
                   {"metric": "not_a_metric", "where": [], "why": "should be dropped"}],
    "investigations": [],
    "rationale": "AI flag per task.",
}


def _verdict(v: str, reason: str = "", text: str = "QA pass fell to 20.3% (E3).") -> dict:
    return {"verdict": v, "agrees_with_rubric": not reason, "override_reason": reason, "headline": "h",
            "summary": text, "findings": [{"text": text, "evidence_ids": ["E1", "E99"]}], "hidden_costs": [],
            "recommendation": {"action": "fix_then_scale", "text": "fix"}, "next_validation_step": "x",
            "confidence": "high"}


def test_llm_plan_is_validated_and_runs():
    r = run_audit("UC0002", llm=FakeLLM(GOOD_PLAN, _verdict("TRADE-OFF")))
    assert r["plan"]["planner"].startswith("LLM planner")
    assert [g["metric"] for g in r["plan"]["guardrails"]] == ["legal_qa_pass"]
    assert any("not_a_metric" in w for w in r["plan_warnings"])
    assert r["verdict"]["verdict"] == "TRADE-OFF"
    assert r["verdict"]["findings"][0]["evidence_ids"] == ["E1"]  # unknown id E99 stripped


def test_invalid_primary_falls_back_to_registered_plan():
    bad = dict(GOOD_PLAN, primary={"metric": "made_up", "where": []})
    r = run_audit("UC0002", llm=FakeLLM(bad, _verdict("TRADE-OFF")))
    assert r["plan"]["primary"]["metric"] == "legal_minutes"


def test_override_without_reason_is_reverted():
    r = run_audit("UC0002", llm=FakeLLM(GOOD_PLAN, _verdict("PROVEN")))
    assert r["verdict"]["verdict"] == "TRADE-OFF" and not r["contested"]


def test_override_with_reason_is_flagged_as_contested():
    r = run_audit("UC0002", llm=FakeLLM(GOOD_PLAN, _verdict("UNPROVEN", reason="sample is small")))
    assert r["verdict"]["verdict"] == "UNPROVEN" and r["contested"]


def test_invented_numbers_are_caught():
    r = run_audit("UC0002", llm=FakeLLM(GOOD_PLAN, _verdict("TRADE-OFF", text="QA pass fell to 37.4%.")))
    assert "37.4" in " ".join(r["grounding"]["unverified"])


def test_own_model_filter_becomes_attribution():
    # Seen live on Bedrock: the planner filtered on the AI's own model_id, which also removed the non-AI records.
    w = [{"dim": "task_type", "value": "Classify"}, {"dim": "model_id", "value": "MDL0096"}]
    plan = dict(GOOD_PLAN, primary={"metric": "legal_minutes", "where": w},
                guardrails=[{"metric": "legal_qa_pass", "where": w, "why": "QA failures"}])
    r = run_audit("UC0002", llm=FakeLLM(plan, _verdict("TRADE-OFF")))
    assert r["plan"]["primary"]["where"] == {"task_type": "Classify", "_ai_model": "MDL0096"}
    assert r["rubric"]["verdict"] == "TRADE-OFF"


def test_other_projects_ai_records_are_not_borrowed():
    # Seen live: UC0423 (model MDL0037) was measured on legal tasks done by other AI models and blamed for
    # UC0002's quality drop. Its own model has no AI-assisted records, so it must be reported as not measurable.
    plan = dict(GOOD_PLAN, primary={"metric": "legal_minutes", "where": []},
                guardrails=[{"metric": "legal_qa_pass", "where": [], "why": "QA failures"}])
    r = run_audit("UC0423", llm=FakeLLM(plan, _verdict("UNPROVEN")))
    assert r["plan"] is None and r["harms"] == []
    assert any(t["code"] == "NOT_ATTRIBUTABLE" for t in r["traps"])
    assert r["rubric"]["verdict"] == "UNPROVEN" and r["value"]["supported_usd"] == 0
