"""Robustness checks fire where expected, and the Bedrock JSON path validates and repairs replies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor.agent import run_audit  # noqa: E402
from auditor.llm import ClaudeLLM, validate  # noqa: E402


def test_placebo_catches_pre_existing_trend():
    r = run_audit("UC0006")
    assert any(t["code"] == "PLACEBO_FAILED" for t in r["traps"])


def test_copilot_passes_robustness_and_value_is_haircut():
    r = run_audit("UC0001")
    codes = {t["code"] for t in r["traps"]}
    assert not codes & {"PLACEBO_FAILED", "PRE_TREND", "NOVELTY_DECAY"}
    assert r["robustness"]["placebo"] and r["robustness"]["pre_trend"] and r["robustness"]["durability"]
    v = r["value"]
    assert 0 < v["supported_usd"] < v["reported_usd"]  # real effect, far smaller than the hours claimed


def test_powered_null_is_labelled():
    r = run_audit("UC0004")
    assert r["rubric"]["evidence_strength"] in ("adequately powered", "claim ruled out")


def test_pilot_charter_has_sample_size():
    c = run_audit("UC0002")["pilot_charter"]
    assert c["n_per_arm"] > 0 and c["duration_months"] >= 2


def test_validate_schema():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "enum": ["x"]}}}
    assert validate({"a": "x"}, schema) == []
    assert validate({"a": "y"}, schema) and validate({}, schema)


def test_bedrock_json_repairs_one_bad_reply():
    llm = ClaudeLLM.__new__(ClaudeLLM)  # no network: replace the transport
    llm.structured = False
    replies = iter(['{"status": "maybe"}', '```json\n{"status": "ok"}\n```'])
    llm._text = lambda system, messages, schema: next(replies)
    schema = {"type": "object", "required": ["status"], "properties": {"status": {"type": "string", "enum": ["ok"]}}}
    assert llm.json("s", "u", schema) == {"status": "ok"}
