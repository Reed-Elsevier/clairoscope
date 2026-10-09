"""Measure builder: Claude's designs are validated, attributed and pre-registered before anything is measured.
Uses a scripted fake LLM, so no API key is needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor import measure_builder, schema  # noqa: E402
from auditor.agent import FOLLOWUP_SCHEMA, PLAN_SCHEMA, VERDICT_SCHEMA, run_audit  # noqa: E402
from auditor.catalog import METRICS  # noqa: E402
from auditor.facts import get_use_case  # noqa: E402

WEAK_PLAN = {"hypotheses": ["Faster reviews may be lower quality."],
             "primary": {"metric": "manuscript_first_decision_days", "where": []}, "design": "before_after",
             "guardrails": [], "investigations": [], "rationale": "closest catalog measure", "catalog_fit": "weak"}
PEER_REVIEW = {  # UC0338 "Copilot for peer review management": peer reviews are recorded per assignment
    "feasible": True, "table": "peer_review_assignments", "label": "Days from reviewer invitation to submitted review",
    "unit": "days", "higher_is_better": False,
    "value_sql": "date_diff('day', invited_at, review_submitted_at)", "time_column": "invited_at",
    "filter_sql": "response = 'Accepted'", "ai_flag": {"column": "", "true_when": ""}, "scope_sql": "",
    "dims": ["recommendation", "not_a_column"], "why": "Throughput of peer review is set by how fast reviews come back.",
    "assumptions": ["Reviews accepted after the rollout used the copilot."],
    "guardrails": [{"label": "Review quality score", "unit": "score", "higher_is_better": True,
                    "value_sql": "review_quality_score", "why": "Faster reviews must not be shallower."}],
}


class FakeLLM:
    name = "fake"

    def __init__(self, plan: dict, *designs: dict):
        self.plan, self.designs, self.design_calls = plan, list(designs), 0

    def json(self, system, user, schema_):
        if schema_ is PLAN_SCHEMA:
            return self.plan
        if schema_ is measure_builder.DESIGN_SCHEMA:
            self.design_calls += 1
            return dict(self.designs.pop(0) if len(self.designs) > 1 else self.designs[0])
        if schema_ is FOLLOWUP_SCHEMA:
            return {"reasoning": "none", "checks": []}
        if schema_ is VERDICT_SCHEMA:
            return None  # forces the rubric template, keeps the test about the measure
        raise AssertionError("unexpected schema")


def test_retrieval_finds_the_table_that_records_the_work():
    tables = [t["table"] for t in schema.search("Copilot for peer review management Throughput Peer Review", 5)]
    assert tables[0] == "peer_review_assignments"
    assert not any(schema.dictionary()[t]["domain"] == "J_ai_portfolio" for t in tables)  # no self-reported tables


def test_weak_catalog_fit_gets_a_designed_and_preregistered_measure():
    r = run_audit("UC0338", llm=FakeLLM(WEAK_PLAN, PEER_REVIEW))
    md = r["measure_design"]
    assert md and r["plan"]["planner"].startswith("Claude measure builder")
    key = r["plan"]["primary"]["metric"]
    assert key.startswith("cd_") and METRICS[key].source == "peer_review_assignments"
    assert md["spec"]["dims"] == ["recommendation"]  # unknown column dropped
    assert len(md["sha256"]) == 64 and md["designs"] == ["before_after"]
    assert r["primary_effect"] is not None and r["primary_effect"]["design"] == "before_after"
    assert len(r["plan"]["guardrails"]) == 1
    assert r["claim"]["generic_kpi"]  # levels are not compared across different units


@pytest.mark.parametrize("bad", ["(select max(review_word_count) from peer_review_assignments)",
                                 "read_csv('/etc/passwd')", "1; drop table x", "no_such_column * 2"])
def test_unsafe_or_invalid_sql_is_rejected_and_the_audit_still_completes(bad):
    llm = FakeLLM(WEAK_PLAN, dict(PEER_REVIEW, value_sql=bad))
    r = run_audit("UC0338", llm=llm)
    assert r["measure_design"] is None and llm.design_calls == 2  # one repair round, then give up
    assert r["plan"]["primary"]["metric"] == "manuscript_first_decision_days"  # falls back to the catalog plan
    assert any("did not pass the checks" in w or "not a plain SQL expression" in w for w in r["plan_warnings"])


def test_ai_flag_of_a_tool_this_project_does_not_use_is_refused():
    uc = get_use_case("UC0377")  # complaint-handling agent; REPH Copilot belongs to UC0001
    spec = dict(PEER_REVIEW, table="support_cases", ai_flag={"column": "ai_copilot_used", "true_when": ""})
    treated, note = measure_builder._flag(spec, uc, measure_builder._tools("UC0377"),
                                          measure_builder._ids("UC0377"))
    assert treated is None and "does not use" in note


def test_scope_must_name_the_projects_own_ids():
    ids = measure_builder._ids("UC0377")
    assert measure_builder._scope({"scope_sql": "team_id = 'TM9999'"}, ids)[0] is None
    assert measure_builder._scope({"scope_sql": f"team_id = '{ids['owner_team_id']}'"}, ids)[0]


def test_designed_measure_is_flagged_for_review_in_view_and_memo_and_survives_restart():
    from auditor.memo import decision_memo
    from auditor.plain import build_view
    r = run_audit("UC0338", llm=FakeLLM(WEAK_PLAN, PEER_REVIEW))
    v = build_view(r)
    assert v["designed_measure"]["table"] == "peer_review_assignments"
    assert v["checks"][0]["question"] == "Is this the right thing to measure?" and v["checks"][0]["status"] == "warn"
    assert "Measure designed by Claude" in decision_memo(r) and r["measure_design"]["sha256"] in decision_memo(r)
    key = r["plan"]["primary"]["metric"]
    sql = METRICS.pop(key).sql
    METRICS.pop(f"{key}_g1")
    measure_builder.restore(r["measure_design"])
    assert METRICS[key].sql == sql and f"{key}_g1" in METRICS


def test_recent_records_whose_outcome_is_not_final_are_cut_off():
    r = run_audit("UC0338", llm=FakeLLM(WEAK_PLAN, PEER_REVIEW))
    lag = r["measure_design"]["outcome_lag_days"]
    assert lag > 20  # reviews take weeks to come back; without the cut-off recent months look artificially fast
    assert METRICS[r["plan"]["primary"]["metric"]].outcome_lag_days == lag


SUPPORT_PLAN = {"hypotheses": [], "primary": {"metric": "support_aht", "where": []}, "design": "did",
                "guardrails": [{"metric": "support_csat", "where": [], "why": "quality"}], "investigations": [],
                "rationale": "closest catalog measure", "catalog_fit": "good"}


def test_another_projects_copilot_teams_are_not_credited_to_this_project():
    # Seen live: UC0089 (author query handling) was rated PROVEN on the REPH Copilot teams' result, which is UC0001's.
    r = run_audit("UC0089", llm=FakeLLM(SUPPORT_PLAN, PEER_REVIEW))
    assert r["plan"]["primary"]["metric"] == "support_aht__no_ai_groups" and r["plan"]["design"] == "before_after"
    assert all(g["metric"].endswith("__no_ai_groups") for g in r["plan"]["guardrails"])
    assert r["primary_effect"] is None or r["primary_effect"]["design"] == "before_after"
    assert run_audit("UC0001")["plan"]["primary"]["metric"] == "support_aht"  # the owner keeps its comparison


def test_when_claude_finds_no_table_for_the_work_the_audit_says_so():
    r = run_audit("UC0089", llm=FakeLLM(dict(SUPPORT_PLAN, catalog_fit="none"),
                                        dict(PEER_REVIEW, feasible=False, why="No table records author queries.")))
    assert r["plan"] is None
    assert any(t["code"] == "NO_MEASURE" and "author queries" in t["detail"] for t in r["traps"])


SYSTEM_EVENTS = dict(PEER_REVIEW, table="system_events", label="Error rate of system events", unit="%",
                     higher_is_better=False, value_sql="CASE WHEN severity = 'ERROR' THEN 100 ELSE 0 END",
                     time_column="event_ts", filter_sql="", dims=["event_type", "severity"], guardrails=[])


def test_before_after_with_a_coinciding_change_is_not_proven():
    # Seen live (UC0181): error share fell because INFO logging started in 2025-01, not because of the bot.
    r = run_audit("UC0181", llm=FakeLLM(dict(WEAK_PLAN, catalog_fit="none"), SYSTEM_EVENTS))
    assert r["plan"]["design"] == "before_after" and any(t["code"] == "CONFOUNDER" for t in r["traps"])
    assert r["rubric"]["verdict"] != "PROVEN"
