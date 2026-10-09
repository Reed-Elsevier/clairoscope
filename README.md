# AI Value Auditor

**Give it an AI project's claim. An agent designs a fair test, runs it on the Center's operational data,
tries to break its own conclusion, and returns an evidence-backed verdict, including the hidden costs
the claim left out and how much of the reported value is real.**

REPH AI Summit 2026 · Academe Hackathon · Team puuurrrcrammers
Cross-area entry: *AI value audit* (brief §3), touching Tracks 2, 3, 4, 5, 6, 7, 9 and 10.
Core LLM: **Claude on Amazon Bedrock**.

> Demonstration prototype on the synthetic REPH data package. Not production software, not endorsed by
> REPH; outputs are not validated REPH findings. See [Disclosures](#disclosures).

---

## Headline result

Across the 8 flagship AI use cases, project teams report **$827k/yr of realised value**. The operational data
supports **$272k/yr (33%)**. Only 2 of 8 claims hold up, one of them with a severe hidden cost.

| Use case | Claim on record | Verdict | What the auditor found |
|---|---|---|---|
| UC0001 CX Case Copilot | Handling time 14.2 → 9.1 min | **PROVEN** | Diff-in-diff −3.2 min (−20.8%) vs non-adopting teams; passes placebo, pre-trend and durability (92% retained). But 480 staff-hours/yr verified vs **4,667 claimed**. |
| UC0002 Legal Auto-Classify | 22 → 12.5 min | **TRADE-OFF** | Speed is real (−45%), but **QA pass rate 97.9% → 20.3%**. KPI dated before the pilot. Model eval says 0.944; production accuracy is 0.860. |
| UC0003 XML Auto-Validation Agent | Rework 28.7% → 26.7% | **CONTRADICTED** | Operations show 68% rework when the KPI was reported. The confounder hunter traces the jump to **XConvert 4.0** replacing 3.8 (Mar 2025). |
| UC0004 Invoice Exception Agent | 36 → 32 h | **UNPROVEN** | −2.4%, not significant, and the data had 80% power to detect the claimed effect: a real null, not a lack of data. |
| UC0005 Fraud Alert Model | False positives 41% → 29% | **CONTRADICTED** | Reported numbers are not reproducible (real: 67% vs 88% for legacy rules), but the model's improvement itself is real: 78% of the claimed effect is credited. |
| UC0006 Knowledge RAG | Search success 24% → 21% | **UNPROVEN** | **Placebo test fails**: the decline predates the pilot. Hidden cost: zero-result searches +72%. |
| UC0007 Integrity Screening | Pre-publication detection | **CONTRADICTED** | Essentially no integrity flags are raised before publication. |
| UC0008 Lead Scoring | Conversion 39.7% → 28.3% | **CONTRADICTED** | Operations show 6.7% when the KPI was reported, 14 months before the pilot. The score itself is predictive (top band converts 16.5%). |

Reproduce with `python scripts/audit_cli.py --all-flagship --no-llm` (deterministic mode).

## How we meet the judging criteria

| Criterion | Weight | How the prototype answers it |
|---|---|---|
| **Working prototype** | 35% | Live input-to-outcome flow: pick any of 600 use cases *or type a claim* → agent trace streams → verdict, charts, receipts → human decision → memo. Any judge input works (no scripted path). Falls back to deterministic mode if the LLM is unreachable. 14 automated tests. |
| **Business impact & value** | 25% | Answers the brief's central question ("what is really working?"). Converts each verdict into **evidence-supported $** and verified staff-hours, a portfolio red-flag screen over all 600 use cases, and a **pilot charter** that tells leaders how to validate the next step. |
| **AI utilisation** | 15% | Claude plans a counterfactual test, chooses hidden costs to watch, **argues against its own conclusion** (skeptic stage), and writes the verdict. AI does the judgement; code does the arithmetic. |
| **Technical feasibility** | 15% | Small, readable Python package; DuckDB over Parquet; LLM output schema-validated; numeric grounding check; rubric guard; Bedrock auth via standard AWS chain or bearer token; `check_llm.py` doctor; deployable on EC2/ECS as one process. |
| **Presentation** | 10% | 10-minute demo script below; every number on screen has a clickable evidence receipt; limitations and simulated parts disclosed in the UI and here. |

## The problem

> "Leaders now need answers: **what is really working, what is not, and where should AI go next?** Some AI
> projects clearly pay off, some look good on one number and hurt another…" (Challenge brief, p. 2)

The Center has **600 logged AI use cases**. Each reports its own success as one before/after number chosen by
the project team (`ai_use_case_kpis`). Nobody re-measures it against the operational tables or checks what
else moved, so money and headcount follow claims, not evidence. Manual review takes weeks per use case, so it
mostly does not happen.

**Users:** the AI Centre of Excellence and division leaders deciding what to scale, fix, pause or stop.

## The solution

```
 claim / use case
       │
 1 PLAN (Claude)         metric catalog (27 operational metrics) → primary metric, strongest feasible design,
       │                 hidden-cost guardrails, hypotheses. Validated against the catalog: Claude never writes SQL.
 2 EXECUTE (code)        DuckDB: diff-in-diff / AI-vs-non-AI / before-after, all designs as cross-checks.
       │                 Every number → evidence item (SQL + sample record IDs).
 3 CONFOUNDER HUNT       change-point month + "what changed" (tool version, vendor, channel…)
       │
 4 SKEPTIC (Claude)      argues against the emerging conclusion; requests checks that could overturn it
       │
 5 ROBUSTNESS (code)     placebo rollout · parallel pre-trends · mix-adjusted (Simpson) · durability (novelty)
       │                 · power / minimum detectable effect · equivalence test (TOST)
 6 TRAPS + RUBRIC        17 evidence traps, readable thresholds → verdict; evidence-supported $; pilot charter
       │
 7 VERDICT (Claude)      cites evidence IDs; numbers checked against the evidence log; cannot silently
       │                 override the rubric (an override must give a reason and is shown as contested)
 8 HUMAN DECISION        reviewer decides → decision ledger → decision memo (Markdown)
```

**Verdicts:** `PROVEN` · `TRADE-OFF` (real benefit, hidden cost) · `UNPROVEN` · `CONTRADICTED`

## What makes it unique

Other teams can compare a claimed KPI with a dashboard number. This auditor **tests a claim the way an
experimentation team or an econometrician would**, then explains it in plain language.

| Feature | What it does | Grounded in |
|---|---|---|
| **Counterfactual designs** | Diff-in-diff on staggered team go-lives; AI-flagged vs non-AI records in the same period; before/after. All feasible designs run; disagreement blocks a verdict. | Staggered DiD practice [2] |
| **Placebo rollout test** | Pretends the AI launched 6 months early using pre-launch data only. If an "effect" appears, the change is a trend, not the AI. Caught UC0006. | Falsification tests in DiD [2] |
| **Parallel pre-trends check** | Verifies adopters and non-adopters moved together before go-live. | Callaway & Sant'Anna; event-study practice [2] |
| **Simpson's-paradox check** | Re-estimates the effect within strata (category, channel…): is the AI just used on easier work? | Mix-shift auditing [5] |
| **Durability / novelty check** | Effect by quarter since rollout; flags fading benefits. | Novelty effects [3] |
| **Power & equivalence** | Minimum detectable effect and a TOST equivalence test: separates "no effect" from "cannot tell", and can *rule out* a claim. | Lakens 2017 [4] |
| **Hidden-cost guardrails** | Claude picks the metrics the claim does not mention (QA pass, escalations, zero-result searches). | Guardrail metrics [3]; the "jagged frontier": AI can help on some tasks and hurt on others [6] |
| **Confounder hunter** | Finds the month a metric really shifted and which dimension's mix changed then. | Metric-drift checklists [5] |
| **Evidence traps** | KPI before pilot, irreproducible baseline/current value, claim showing decline, right-censoring, ghost adoption, governance gap, offline-eval vs production gap, small sample, value gap. | NIST AI RMF MEASURE 2.3 / 4.3 [1] |
| **Evidence-supported value** | Verified effect × AI volume → staff-hours and $ actually supported vs reported. | Counterfactual, attribution and dollar-translation tests for AI ROI [7] |
| **Pilot charter** | Pre-registered next step: hypothesis, margin, sample size, duration, guardrail bounds, decision rule. | Pre-registered experiments [3][4] |
| **AI that argues with itself** | Skeptic stage hunts for the strongest alternative explanation before the verdict. | Twyman's law: surprising results are usually errors [3] |
| **Receipts, grounding, rubric guard** | Every number has SQL + record IDs; numbers in Claude's text are matched to evidence; Claude cannot silently change the verdict. | Responsible-AI rule "ground your answers" (brief §7) |

## Live demo script (≤ 10 minutes)

1. **Problem (1 min).** "This team says AI cut legal classification time 43%. Should we scale it?"
2. **Audit UC0002 live (3 min).** Watch the agent trace → TRADE-OFF → QA pass 98% → 20% chart → open an evidence receipt (SQL + record IDs) → show the eval-vs-production trap.
3. **Audit UC0006 (1.5 min).** The placebo test fails: the decline started before the RAG pilot.
4. **Judge's input (2 min).** Any use case ID or a typed claim, e.g. *"the support copilot cut handling time by half"*.
5. **Decide and land the value (2.5 min).** Record a decision, download the memo and pilot charter, then the portfolio tab: **$827k reported vs $272k supported**, plus the red-flag screen over 600 use cases.

Backup: `python scripts/audit_cli.py UC0002 -v` runs the same pipeline in a terminal.

## Setup

Requires Python 3.11+ and the REPH data package (`center_data.zip`, kept out of git).

```powershell
pip install -r requirements.txt
python scripts/extract_data.py              # center_data.zip -> ./data (Parquet + _docs)
copy .env.example .env                      # then fill in the Bedrock section
python scripts/check_llm.py                 # prints provider/model/region/auth, makes one test call -> READY
python -m streamlit run app.py              # http://localhost:8501
```

### Amazon Bedrock (core LLM)

1. **Model access.** In the Bedrock console, enable access to the Claude model you will use (default
   `anthropic.claude-opus-5-5`) in your region.
2. **Region.** Set `AWS_REGION` (e.g. `us-east-1`). It is required; the app never guesses one.
3. **Credentials.** Use one of these:
   - AWS credential chain: `aws sso login` / `aws configure`, `AWS_PROFILE`, or the EC2/ECS role (recommended on AWS).
   - Static keys: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (+ `AWS_SESSION_TOKEN`).
   - Bedrock bearer token: `AWS_BEARER_TOKEN_BEDROCK`.
4. **API surface.** `AUDITOR_BEDROCK_API=auto` uses the Bedrock Messages endpoint (`bedrock-mantle`) for
   IDs like `anthropic.claude-opus-5-5`, and InvokeModel for inference-profile IDs like
   `global.anthropic.claude-opus-4-6-v1`. Force either with `mantle` or `invoke`.
5. **IAM.** The caller needs `bedrock-mantle:CreateInference` (Messages endpoint) or `bedrock:InvokeModel`
   (InvokeModel) on the model.
6. Run `python scripts/check_llm.py` until it prints `READY`.

Notes: Bedrock's Messages endpoint does not support structured outputs, so the app puts the JSON schema in the
prompt, validates every reply and asks for one repair if needed. Bedrock has no server-side refusal fallback;
set `AUDITOR_FALLBACK_MODEL` to retry declined requests on another model.

| `check_llm.py` says | Fix |
|---|---|
| `Set AWS_REGION` | Add `AWS_REGION` to `.env` |
| `Authentication failed` | Credentials expired or wrong: re-run `aws sso login` or refresh keys/token |
| `Access denied to <model>` | Enable model access in the Bedrock console; check IAM permissions above |
| `Model '<id>' not found` | Wrong ID for the region or API; try `AUDITOR_BEDROCK_API=invoke` with an inference-profile ID |
| `Cannot reach the endpoint` | Network/proxy, or a region without the endpoint |
| `Credential should be scoped to a valid region` | Bedrock API keys are region-bound: set `AWS_REGION` to the key's region (ours: `ap-southeast-2`) |
| `not available for this account` / 404 on every `anthropic.*` ID | Account only has InvokeModel models: use `global.anthropic.claude-opus-4-6-v1` (verified working for our team) |

Alternatives (same code path): `AUDITOR_LLM=claude` with `ANTHROPIC_API_KEY`, or `AUDITOR_LLM=openai` with
`OPENAI_API_KEY`. With no provider the app runs in **deterministic mode**, labelled in the UI.

### Terminal and tests

```powershell
python scripts/audit_cli.py UC0002 -v
python scripts/audit_cli.py --claim "The legal classifier cut handling time 43%"
python -m pytest -q tests        # 14 tests; a scripted fake LLM tests the guards, no API key needed
```

### Deploying on AWS (EC2/ECS)

One Streamlit process. Copy the repo and extracted `data/`, give the instance/task role Bedrock permissions,
set `AWS_REGION`, then `python -m streamlit run app.py --server.port 8501 --server.address 0.0.0.0`.

## Project structure

```
app.py                  Streamlit UI: audit, portfolio truth map + flagship scorecard, decision ledger, how it works
auditor/
  catalog.py            27 operational metrics (the semantic layer Claude plans with)
  engine.py             diff-in-diff, AI-vs-non-AI, before/after, change-point, what-changed, segments
  robustness.py         placebo, pre-trends, mix-adjusted (Simpson), durability, power / equivalence
  judge.py              evidence traps, framework references, verdict rubric
  value.py              evidence-supported value, pilot charter (sample size, duration, decision rule)
  agent.py              orchestration, prompts and schemas, skeptic stage, grounding check, verdict guard
  llm.py                Claude on Bedrock (Messages / InvokeModel, AWS chain or bearer), Claude API, OpenAI
  facts.py              use-case facts, rollout dates, portfolio red-flag screen
  plans.py              plan validation, reviewed bindings for UC0001-UC0008, keyword heuristic
  ledger.py, memo.py    decision ledger (SQLite) and memo export
scripts/                extract_data.py, check_llm.py, audit_cli.py
tests/                  verdict regression, robustness, LLM guard and Bedrock JSON-repair tests
```

## Data used

Curated Parquet only (no data added, modified or generated): `ai_use_cases`, `ai_use_case_kpis`,
`ai_usage_events`, `ai_governance_reviews`, `model_versions`, `teams`, `support_cases`, `editorial_tasks`,
`regulatory_updates`, `regulatory_update_impacts`, `jurisdictions`, `content_production_jobs`, `manuscripts`,
`invoices`, `invoice_exceptions`, `risk_alerts`, `alert_rules`, `search_logs`, `research_integrity_flags`,
`research_papers_published`, `leads`, `it_tickets`, `access_requests`, `process_event_log`,
`process_definitions`, `divisions`.

## Disclosures

- **Prototype.** Demonstration prototype, not production-ready, not endorsed by REPH.
- **AI models.** Claude (`anthropic.claude-opus-5-5`) on Amazon Bedrock by default; Claude API or OpenAI by
  configuration. No model was trained or fine-tuned.
- **Pre-configured parts.** Reviewed metric bindings for the 8 flagship use cases (`auditor/plans.py`) are used
  when the LLM is off or returns an invalid plan; the UI labels which planner produced each plan. Deterministic
  mode uses templated verdict text, labelled as such. No output is hardcoded: every number is computed live.
- **Assumptions.** Rollout date = pilot date, else scaled date, else first usage event. "Search success" ≈
  click-through. Outcome lags: leads 160 days, AP exceptions 4 days (from the data). Normal-approximation
  tests. Evidence-supported value: staff-effort metrics use verified minutes × AI volume vs claimed hours;
  other metrics scale the reported value by the share of the claimed effect observed. The equivalence margin
  is half the claimed effect. Pilot sample sizes assume record-level independence.
- **Limitations.** Observational data: verdicts are strong evidence, not proof of causation. Use cases whose
  work is not in any catalog table get "no operational data can test this claim".
- **Third-party components.** DuckDB, pandas, PyArrow, Streamlit, Altair, anthropic[bedrock], openai,
  python-dotenv (`requirements.txt`).
- **Responsible AI.** No individual-level ranking. Human decision on every audit. Generated text cites evidence
  and is numerically checked.

## Next validation step

Run the auditor on the next AI pilot **before** it starts: issue its pilot charter (primary metric, guardrails,
margin, holdout teams, analysis date), then let the auditor deliver the verdict on the pre-registered date.

## References

1. NIST AI Risk Management Framework (AI 100-1): MEASURE 2.3 and MEASURE 4.3, e.g. via [STIG Viewer: MEASURE 2.3](https://www.stigviewer.com/controls/ai-rmf/MEASURE%202.3), [MEASURE 4.3](https://www.stigviewer.com/controls/ai-rmf/MEASURE%204.3)
2. Staggered difference-in-differences and event studies: [Choosing an estimator (diff-diff docs)](https://diff-diff.readthedocs.io/en/latest/choosing_estimator.html), [Staggered DiD (bookdown)](https://bookdown.org/mike/data_analysis/staggered-difference-in-differences.html); Callaway & Sant'Anna (2021), *Journal of Econometrics*
3. Kohavi, Tang & Xu, *Trustworthy Online Controlled Experiments* (Cambridge University Press): [guardrail metrics and SRM chapter](https://www.cambridge.org/core/books/trustworthy-online-controlled-experiments/sample-ratio-mismatch-and-other-trustrelated-guardrail-metrics/8DBB0F59AC7729D7BC6B94690DB9CCD5)
4. Lakens (2017), [Equivalence Tests: A Practical Primer](https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/)
5. [Seven myths about AI and productivity (California Management Review, 2025)](https://cmr.berkeley.edu/2025/10/seven-myths-about-ai-and-productivity-what-the-evidence-really-says/); [Evaluating whether AI agents are improving (Arize)](https://arize.com/?p=32083)
6. Dell'Acqua et al., *Navigating the Jagged Technological Frontier* (HBS/BCG, 2023): [HBS summary](https://aiinstitute.hbs.edu/?p=18394)
7. [Why businesses overestimate generative AI ROI (Reworked)](https://www.reworked.co/digital-workplace/why-businesses-overestimate-generative-ai-roi/)
