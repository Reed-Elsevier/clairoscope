"""Smoke tests: the UI renders and the 8 flagship audits produce the expected verdicts (deterministic mode).

Run: python -m pytest -q tests   (needs ./data extracted)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AUDITOR_LLM"] = "off"

from auditor.agent import run_audit  # noqa: E402

EXPECTED = {"UC0001": "PROVEN", "UC0002": "TRADE-OFF", "UC0003": "CONTRADICTED", "UC0004": "UNPROVEN",
            "UC0005": "CONTRADICTED", "UC0006": "UNPROVEN", "UC0007": "CONTRADICTED", "UC0008": "CONTRADICTED"}


def test_flagship_verdicts():
    for uc, verdict in EXPECTED.items():
        r = run_audit(uc)
        assert r["verdict"]["verdict"] == verdict, (uc, r["rubric"]["reasons"])
        assert r["evidence"], uc


def test_typed_claim_maps_to_use_case():
    r = run_audit(claim_text="The legal classifier cut handling time 43%")
    assert r["use_case"]["use_case_id"] == "UC0002"


def test_app_renders_and_audits():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception
    at.button[0].click().run()
    assert not at.exception
    assert any("TRADE-OFF" in m.value for m in at.markdown)
