"""AI Value Auditor web API + single-page UI.

Run locally:  python -m uvicorn web.server:app --port 8080
In AWS:       see Dockerfile and deploy/ (EC2 or ECS Fargate)

Audits take ~5-40 s (LLM on), so they run as background jobs; the browser polls /api/jobs/{id}
for progress. Polling works through any load balancer or proxy without special timeouts.
"""
from __future__ import annotations

import base64
import json
import math
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auditor import ledger
from auditor.agent import run_audit
from auditor.catalog import METRICS
from auditor.config import load_env
from auditor.db import tables
from auditor.engine import monthly
from auditor.facts import get_use_case, list_use_cases, portfolio, portfolio_flags
from auditor.llm import configured_provider, get_llm
from auditor.memo import decision_memo
from auditor.plain import build_view

load_env()
STATIC = Path(__file__).parent / "static"
FLAGSHIPS = [f"UC000{i}" for i in range(1, 9)]



@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_build_overview, daemon=True).start()  # landing-page numbers, built in background
    if configured_provider() != "off":
        _llm_status.update(state="checking")
    threading.Thread(target=_setup_llm, daemon=True).start()
    yield


app = FastAPI(title="AI Value Auditor", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("AUDITOR_WORKERS", "4")))
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_overview: dict = {"ready": False}

LLM = None
_llm_status: dict = {"state": "off", "error": None, "name": None}
_PROBE_SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


def _setup_llm(reload_env: bool = False) -> None:
    """(Re)create the LLM client from the environment and probe it with one tiny call."""
    global LLM
    if reload_env:
        load_env(override=True)  # pick up a fresh key from .env without restarting
    try:
        LLM = get_llm()
    except Exception as e:  # bad provider settings must not stop the app: it falls back to deterministic mode
        LLM = None
        _llm_status.update(state="error", error=str(e), name=None)
        return
    if LLM is None:
        _llm_status.update(state="off", error=None, name=None)
        return
    _llm_status.update(state="checking", error=None, name=LLM.name)
    try:
        LLM.json("You are a connectivity check.", 'Return {"ok": true}.', _PROBE_SCHEMA)
        _llm_status.update(state="ok")
    except Exception as e:
        _llm_status.update(state="error", error=str(e))


# ---------------------------------------------------------------- helpers
def clean(obj):
    """JSON-safe copy: NaN/inf -> None (JSON has no NaN)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def ok(payload) -> JSONResponse:
    return JSONResponse(clean(payload))


# ---------------------------------------------------------------- optional basic auth (AUDITOR_BASIC_AUTH=user:pass)
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    creds = os.environ.get("AUDITOR_BASIC_AUTH")
    if creds and request.url.path != "/api/health":
        header = request.headers.get("authorization", "")
        given = base64.b64decode(header[6:]).decode("utf-8", "replace") if header.startswith("Basic ") else ""
        if not secrets.compare_digest(given, creds):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="AI Value Auditor"'})
    return await call_next(request)


# ---------------------------------------------------------------- overview (precomputed at startup)
def _build_overview() -> None:
    rows = []
    for uc_id in FLAGSHIPS:
        r = run_audit(uc_id)  # deterministic mode: fast and reproducible for the landing page
        v = build_view(r)
        rows.append({"use_case_id": uc_id, "name": r["use_case"]["name"], "stage": r["use_case"]["stage"],
                     "verdict": v["verdict"], "verdict_label": v["verdict_label"], "color": v["color"],
                     "headline": v["tagline"], "reported": v["value"]["reported_raw"],
                     "supported": v["value"]["supported_raw"]})
    df = portfolio_flags(portfolio())
    live = df[df["stage"].isin(["Pilot", "Scaled"])]
    _overview.update({
        "ready": True, "flagships": rows,
        "reported_total": sum(r["reported"] for r in rows),
        "supported_total": sum(r["supported"] for r in rows),
        "holding_up": sum(r["verdict"] in ("PROVEN", "TRADE-OFF") for r in rows),
        "portfolio": {"use_cases": int(len(df)), "live": int(len(live)),
                      "live_no_usage": int(live["flag_ghost_adoption"].sum()),
                      "live_no_review": int(live["flag_governance_gap"].sum()),
                      "kpi_before_pilot": int(df["flag_kpi_before_pilot"].sum())},
        "red_flags": df.sort_values(["red_flags", "expected_usd"], ascending=False).head(12)[
            ["use_case_id", "use_case_name", "stage", "division", "red_flags", "expected_usd", "realized_usd",
             "usage_events"]].to_dict("records"),
    })


# ---------------------------------------------------------------- routes
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    try:
        n = len(tables())
    except FileNotFoundError as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)
    return {"status": "ok", "tables": n, "llm": _llm_status}


@app.get("/api/config")
def config():
    return {"llm": _llm_status}


@app.post("/api/llm/reload")
def llm_reload():
    _setup_llm(reload_env=True)
    return {"llm": _llm_status}


@app.get("/api/overview")
def overview():
    return ok(_overview)


@app.get("/api/use-cases")
def use_cases():
    df = list_use_cases()
    return [{"id": r.use_case_id, "name": r.use_case_name, "stage": r.stage, "division": r.division_name,
             "flagship": r.use_case_id in FLAGSHIPS} for r in df.itertuples()]


@app.get("/api/use-cases/{uc_id}")
def use_case(uc_id: str):
    uc = get_use_case(uc_id)
    if not uc:
        raise HTTPException(404, "Unknown use case")
    return ok({k: uc.get(k) for k in ("use_case_id", "name", "division", "stage", "claim", "pilot_date",
                                      "expected_annual_value_usd", "realized_annual_value_usd")})


class AuditRequest(BaseModel):
    use_case_id: str | None = None
    claim_text: str | None = None
    use_llm: bool = True


def _run_job(job_id: str, req: AuditRequest) -> None:
    job = _jobs[job_id]

    def on_step(name: str, detail: str) -> None:
        job["steps"].append({"step": name, "detail": detail, "t": round(time.time() - job["started"], 1)})

    try:
        r = run_audit(req.use_case_id, claim_text=req.claim_text,
                      llm=LLM if (req.use_llm and LLM) else None, on_step=on_step)
        ledger.save_audit(r)
        if r.get("llm_errors"):
            _llm_status.update(state="error", error=r["llm_errors"][0])
        elif req.use_llm and LLM:
            _llm_status.update(state="ok", error=None)
        job["result"] = clean({"audit_id": r["audit_id"], "view": build_view(r), "report": r})
        job["status"] = "done"
    except ValueError as e:
        job["status"], job["error"] = "error", str(e)
    except Exception as e:  # surface unexpected failures to the UI instead of hanging the poller
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"


@app.post("/api/audits")
def start_audit(req: AuditRequest):
    if not req.use_case_id and not (req.claim_text or "").strip():
        raise HTTPException(400, "Pick a use case or type a claim")
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        if len(_jobs) > 200:  # keep memory bounded
            for old in list(_jobs)[:100]:
                _jobs.pop(old, None)
        _jobs[job_id] = {"status": "running", "steps": [], "started": time.time(), "result": None, "error": None}
    _pool.submit(_run_job, job_id, req)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = _jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown job")
    return ok({k: j[k] for k in ("status", "steps", "result", "error")})


class Decision(BaseModel):
    reviewer: str
    decision: str
    agrees: bool = True
    note: str = ""


@app.post("/api/audits/{audit_id}/decision")
def decide(audit_id: str, d: Decision):
    if not ledger.load_report(audit_id):
        raise HTTPException(404, "Unknown audit")
    ledger.save_decision(audit_id, d.reviewer, d.agrees, d.decision, d.note)
    return {"saved": True}


@app.get("/api/audits/{audit_id}/memo")
def memo(audit_id: str):
    r = ledger.load_report(audit_id)
    if not r:
        raise HTTPException(404, "Unknown audit")
    text = decision_memo(r, ledger.latest_decision(audit_id))
    return PlainTextResponse(text, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="audit_{r["use_case"]["use_case_id"]}_{audit_id}.md"'})


@app.get("/api/ledger")
def history():
    return ok(ledger.history().drop(columns=["audit_id"]).head(50).to_dict("records"))


@app.get("/api/series")
def series(metric: str, where: str = "{}", group: str | None = None):
    if metric not in METRICS:
        raise HTTPException(400, "Unknown metric")
    try:
        df = monthly(metric, json.loads(where), group)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    df = df[df["n"] >= 5]
    m = METRICS[metric]
    return ok({"metric": m.label, "unit": m.unit, "higher_is_better": m.higher_is_better,
               "points": [{"month": str(r.month.date()), "group": r.grp, "value": r.mean, "n": int(r.n)}
                          for r in df.itertuples()]})
