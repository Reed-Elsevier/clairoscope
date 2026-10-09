"""Web API end to end (deterministic mode): overview, audit job, plain view, decision, memo, chart data."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["AUDITOR_LLM"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from web.server import app  # noqa: E402


def _wait(client, path, cond, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        body = client.get(path).json()
        if cond(body):
            return body
        time.sleep(0.3)
    raise AssertionError(f"timeout waiting for {path}")


def test_web_flow():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        assert "AI Value Auditor" in c.get("/").text
        assert len(c.get("/api/use-cases").json()) == 600

        ov = _wait(c, "/api/overview", lambda b: b.get("ready"))
        assert len(ov["flagships"]) == 8 and ov["reported_total"] > ov["supported_total"] > 0

        job_id = c.post("/api/audits", json={"use_case_id": "UC0002", "use_llm": False}).json()["job_id"]
        job = _wait(c, f"/api/jobs/{job_id}", lambda b: b["status"] != "running")
        assert job["status"] == "done", job.get("error")
        view = job["result"]["view"]
        assert view["verdict"] == "TRADE-OFF"
        assert view["hidden_costs"] and view["checks"] and view["next_step"]
        assert job["steps"]

        audit_id = job["result"]["audit_id"]
        assert c.post(f"/api/audits/{audit_id}/decision",
                      json={"reviewer": "test", "decision": "Fix first, then scale", "agrees": True}).json()["saved"]
        memo = c.get(f"/api/audits/{audit_id}/memo").text
        assert "Fix first, then scale" in memo

        s = c.get("/api/series", params={"metric": "legal_qa_pass", "where": json.dumps({"task_type": "Classify"}),
                                         "group": "treated"}).json()
        assert s["points"]

        assert c.post("/api/audits", json={}).status_code == 400
        claim_job = c.post("/api/audits", json={"claim_text": "the support copilot cut handling time by half",
                                                "use_llm": False}).json()["job_id"]
        job = _wait(c, f"/api/jobs/{claim_job}", lambda b: b["status"] != "running")
        assert job["result"]["view"]["use_case"]["use_case_id"] == "UC0001"
