# Clairoscope

*An AI value auditor: see clearly what your AI projects really deliver.*

**Give it an AI project's claim. An agent designs a fair test, runs it on the Center's operational data,
tries to break its own conclusion, and returns an evidence-backed verdict, including the hidden costs
the claim left out and how much of the reported value is real.**

**New here? Read [HOW_IT_WORKS.md](HOW_IT_WORKS.md)**: each step in plain words, where AI is and is not used,
and exactly which data is used.

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
| UC0005 Fraud Alert Model | False positives 41% → 29% | **CONTRADICTED** | Reported numbers are not reproducible (operations show 88% before and 67.6% when the KPI was reported), but model-based rules do beat the others by 20.7 pp: 78% of the claimed effect is credited. |
| UC0006 Knowledge RAG | Search success 24% → 21% | **UNPROVEN** | **Placebo test fails**: the decline predates the pilot. Hidden cost: zero-result searches +72%. |
| UC0007 Integrity Screening | Pre-publication detection | **CONTRADICTED** | Essentially no integrity flags are raised before publication. |
| UC0008 Lead Scoring | Conversion 39.7% → 28.3% | **CONTRADICTED** | Operations show 6.7% when the KPI was reported, 14 months before the pilot. The score itself is predictive (top band converts 16.5%). |

Reproduce with `python scripts/audit_cli.py --all-flagship --no-llm` (deterministic mode).

## How we meet the judging criteria

| Criterion | Weight | How the prototype answers it |
|---|---|---|
| **Working prototype** | 35% | Web app packaged for AWS (EC2 or ECS): pick any of 600 use cases *or type a claim* → progress streams in plain words → verdict a non-analyst understands at a glance → human decision → memo. Any judge input works (no scripted path). If the LLM is unreachable it says so and falls back to rule-based mode. 29 automated tests. |
| **Business impact & value** | 25% | Answers the brief's central question ("what is really working?"). Converts each verdict into **evidence-supported $** and verified staff-hours, a portfolio red-flag screen over all 600 use cases, and a **pilot charter** that tells leaders how to validate the next step. |
| **AI utilisation** | 15% | Claude does the work an analyst does by hand: for any of the 600 projects it **searches the data dictionary and designs the measurement** when no standard one fits (the measure builder), plans a fair test, chooses hidden costs to watch, **argues against its own conclusion** (skeptic stage) and explains the verdict in plain words. Code validates every design and does all the arithmetic. |
| **Technical feasibility** | 15% | Small, readable Python package; FastAPI + dependency-free web UI; DuckDB over Parquet; LLM output schema-validated; numeric grounding check; rubric guard; Bedrock via IAM role or bearer token; one Docker image with EC2 and ECS Fargate recipes ([deploy/](deploy/DEPLOY.md)). |
| **Presentation** | 10% | First screen answers "does it work, should we scale it, can we trust this?" in plain words (no charts to interpret); analysts can open the evidence, SQL and agent trace. 10-minute demo script below. |

## The problem

> "Leaders now need answers: **what is really working, what is not, and where should AI go next?** Some AI
> projects clearly pay off, some look good on one number and hurt another…" (Challenge brief, p. 2)

The Center has **600 logged AI use cases**; the 402 that have run report their own success as one before/after
number chosen by the project team (`ai_use_case_kpis`), and 198 are still ideas. Nobody re-measures it against the operational tables or checks what
else moved, so money and headcount follow claims, not evidence. Manual review takes weeks per use case, so it
mostly does not happen.

**Users:** the AI Centre of Excellence and division leaders deciding what to scale, fix, pause or stop.

## The solution

```
 claim / use case
       │
 1 PLAN (Claude)         metric catalog (28 operational metrics) → primary metric, strongest feasible design,
       │                 hidden-cost guardrails, hypotheses, and how well the catalog fits this project.
 1b MEASURE BUILDER      weak or no fit → retrieve the best tables from the data dictionary (BM25 over 116 tables,
    (Claude + code)      1,042 column descriptions) → Claude designs a measure as SQL expressions → code checks it
       │                 (expressions only, columns exist, AI flag really is this project's, enough records on both
       │                 sides, outcome-delay cut-off) → fingerprinted (SHA-256) before anything is measured.
 2 EXECUTE (code)        DuckDB: diff-in-diff / AI-vs-non-AI / before-after, all designs as cross-checks.
       │                 Only this project's own AI records count. Every number → evidence item (SQL + record IDs).
 3 CONFOUNDER HUNT       change-point month + "what changed" (tool version, vendor, channel…)
       │
 4 SKEPTIC (Claude)      argues against the emerging conclusion; requests checks that could overturn it
       │
 5 ROBUSTNESS (code)     placebo rollout · parallel pre-trends · mix-adjusted (Simpson) · durability (novelty)
       │                 · power / minimum detectable effect · equivalence test (TOST)
 6 TRAPS + RUBRIC        19 evidence traps, readable thresholds → verdict; evidence-supported $; pilot charter
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
| **Evidence traps** | 19, incl. KPI before pilot, irreproducible baseline/current value, claim showing decline, right-censoring, ghost adoption, governance gap, offline-eval vs production gap, another project's AI, not live yet, small sample, value gap. | NIST AI RMF MEASURE 2.3 / 4.3 [1] |
| **Evidence-supported value** | Verified effect × AI volume → staff-hours and $ actually supported vs reported. | Counterfactual, attribution and dollar-translation tests for AI ROI [7] |
| **Pilot charter** | Pre-registered next step: hypothesis, margin, sample size, duration, guardrail bounds, decision rule. | Pre-registered experiments [3][4] |
| **AI that argues with itself** | Skeptic stage hunts for the strongest alternative explanation before the verdict. | Twyman's law: surprising results are usually errors [3] |
| **Measure builder (retrieval over the data dictionary)** | When no standard measure fits a project, Claude reads the most relevant tables' documentation and designs one; code rejects unsafe SQL, other projects' AI flags (a tool shared by 327 projects cannot prove one project's effect), thin samples and not-yet-final records, and fingerprints the design before measuring. Results are labelled "designed by Claude" for analyst sign-off. | Pre-registration [3][4]; NIST AI RMF MEASURE 2.3 [1] |
| **Attribution guard** | Another project's AI records never count: AI flags in the standard measures belong to one flagship each, records naming a model count only for that model's project, and a tool shared by 327 projects is never accepted as proof. Found and fixed cases where projects were credited with REPH Copilot's results. | NIST AI RMF MEASURE 2.3 [1] |
| **Receipts, grounding, rubric guard** | Every number has SQL + record IDs; numbers in Claude's text are matched to evidence; Claude cannot silently change the verdict. | Responsible-AI rule "ground your answers" (brief §7) |

## Live demo script (≤ 10 minutes)

1. **Problem (1 min).** "This team says AI cut legal classification time 43%. Should we scale it?"
2. **Audit UC0002 live (3 min).** Click its row in the flagship table → watch the plain-language progress → *"Partly. The benefit is real, but something important got worse."* → "They said −43%, we found −45%" → "QA pass rate 97.9% → 20.3%" → trust checklist → open *Show the evidence* for the SQL receipt.
3. **Audit UC0006 (1.5 min).** The placebo test fails: the decline started before the RAG pilot.
4. **Judge's input and the AI at work (2 min).** Any of the 600 projects or a typed claim, e.g. *"the support copilot cut handling time by half"*. With Claude on, try **UC0435**: no standard measure fits, so Claude searches the data dictionary and designs one (shown as "Measure designed by Claude", with its SQL under the evidence).
5. **Decide and land the value (2.5 min).** Record a decision, download the memo, then scroll to the landing numbers: **$827k reported vs $272k backed by data**, 2 of 8 claims hold up, plus the red-flag table over 600 use cases.

Backup: `python scripts/audit_cli.py UC0002 -v` runs the same pipeline in a terminal.

## Setup

Requires Python 3.11+ and the REPH data package (`center_data.zip`, kept out of git).

```powershell
pip install -r requirements.txt
python scripts/extract_data.py              # center_data.zip -> ./data (Parquet + _docs)
python scripts/make_used_data.py            # optional: ./used = 3 files to upload (the 26 tables in one .duckdb + dictionary)
copy .env.example .env                      # then fill in the Bedrock section
python scripts/check_llm.py                 # prints provider/model/region/auth, makes one test call -> READY
python -m uvicorn web.server:app --port 8080 # http://localhost:8080  (add ?uc=UC0002&ai=0 for a fast rule-based demo link)
```

### Amazon Bedrock (core LLM)

1. **Model access.** In the Bedrock console, enable access to the Claude model you will use (default
   `global.anthropic.claude-opus-4-6-v1`, verified on our account; `anthropic.claude-opus-5-5` where enabled).
2. **Region.** Set `AWS_REGION` (ours: `ap-southeast-2`). It is required; the app never guesses one.
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
| `Authentication failed` | Credentials expired or wrong: re-run `aws sso login` or refresh keys/token. A **short-term** Bedrock API key (`bedrock-api-key-...`) dies when the console session that created it ends, often well before the 12 h shown: use a long-term key or an IAM role for the demo |
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
pip install -r requirements-dev.txt
python -m pytest -q tests        # 29 tests incl. the web API and measure builder; a scripted fake LLM, no API key needed
```

### Deploying on AWS (EC2 or ECS)

One Docker image ([Dockerfile](Dockerfile)) serves the API and the web UI on port 8080. Full guide:
**[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

- **EC2 (fastest):** launch Amazon Linux 2023 with the IAM role from [deploy/iam-policy.json](deploy/iam-policy.json)
  and paste [deploy/ec2-user-data.sh](deploy/ec2-user-data.sh) as user data. It builds and runs the container.
- **ECS Fargate:** push with [deploy/build-and-push.sh](deploy/build-and-push.sh), register
  [deploy/ecs-task-definition.json](deploy/ecs-task-definition.json), run 1 task behind an ALB (health check `/api/health`).
- **Data** is read from a private S3 prefix in the event account (`AUDITOR_DATA_S3_URI`), never from git.
- **Bedrock** uses the instance/task IAM role (no key to expire); a bearer key also works.
- **Login:** set `AUDITOR_BASIC_AUTH=user:password` on any public URL.

## Project structure

```
web/
  server.py             FastAPI: audit jobs + progress, overview, decisions, memo, chart data, AI status / reload
  static/               single-page UI (HTML/CSS/JS, no build step, no CDN), self-hosted fonts, icons
auditor/
  catalog.py            28 operational metrics (the semantic layer Claude plans with)
  engine.py             diff-in-diff, AI-vs-non-AI, before/after, change-point, what-changed, segments
  robustness.py         placebo, pre-trends, mix-adjusted (Simpson), durability, power / equivalence
  judge.py              evidence traps, framework references, verdict rubric
  value.py              evidence-supported value, pilot charter (sample size, duration, decision rule)
  agent.py              orchestration, prompts and schemas, skeptic stage, grounding check, verdict guard
  llm.py                Claude on Bedrock (Messages / InvokeModel, AWS chain or bearer), Claude API, OpenAI
  facts.py              use-case facts, rollout dates, portfolio red-flag screen
  plans.py              plan validation, reviewed bindings for UC0001-UC0008, keyword heuristic
  plain.py              plain-language view for non-analysts (verdict, said vs found, trust checks, next step)
  schema.py             data-dictionary retrieval (BM25 over table and column descriptions)
  measure_builder.py    Claude-designed measures: validation, attribution, outcome-delay cut-off, fingerprint
  ledger.py, memo.py    decision ledger (SQLite) and memo export
scripts/                extract_data.py, make_used_data.py, fetch_data.py (S3), check_llm.py, audit_cli.py
deploy/                 DEPLOY.md, IAM policy, EC2 user data, ECS task definition, ECR push script
Dockerfile              one container for EC2 / ECS
tests/                  verdict regression, robustness, LLM guards, attribution, measure builder, web API
HOW_IT_WORKS.md         plain-language explanation of every step and the data used
```

## Data used

Only the synthetic REPH dataset provided for the hackathon (`center_data.zip`, 116 tables), unmodified, with
nothing added or generated. The code reads **26 tables across all 11 folders** under `data/`:

| Folder | Tables |
|---|---|
| `J_ai_portfolio` | `ai_use_cases`, `ai_use_case_kpis`, `ai_usage_events`, `ai_governance_reviews`, `model_versions` |
| `A_workforce` | `teams`, `divisions` |
| `B_publishing` | `content_production_jobs`, `manuscripts`, `research_integrity_flags`, `research_papers_published` |
| `C_legal` | `editorial_tasks`, `regulatory_updates`, `regulatory_update_impacts`, `jurisdictions` |
| `D_risk` | `risk_alerts`, `alert_rules` |
| `E_events` | `leads` |
| `F_customer` | `support_cases` |
| `G_finance` | `invoices`, `invoice_exceptions` |
| `H_technology` | `it_tickets`, `access_requests` |
| `I_process` | `process_definitions`, `process_event_log` |
| `K_knowledge` | `search_logs` |

The measure builder also searches the data dictionary in `data/_docs/` and may measure any other work-record
table it finds (e.g. `peer_review_assignments`). What each table contributes:
[HOW_IT_WORKS.md, section 5](HOW_IT_WORKS.md#5-data-used).

## Disclosures

- **Prototype.** Demonstration prototype, not production-ready, not endorsed by REPH.
- **AI models.** Claude Opus 4.6 (`global.anthropic.claude-opus-4-6-v1`) on Amazon Bedrock by default; Claude
  Opus 5.5, the Claude API or OpenAI by configuration. No model was trained or fine-tuned.
- **Pre-configured parts.** Reviewed metric bindings for the 8 flagship use cases (`auditor/plans.py`) are used
  when the LLM is off or returns an invalid plan; the UI labels which planner produced each plan. Deterministic
  mode uses templated verdict text, labelled as such. No output is hardcoded: every number is computed live.
- **Assumptions.** Rollout date = pilot date, else scaled date, else first usage event; idea-stage projects
  are treated as not deployed. Claude-designed measures drop records newer than their 95th-percentile outcome delay. "Search success" ≈
  click-through. Outcome lags: leads 160 days, AP exceptions 4 days (from the data). Normal-approximation
  tests. Evidence-supported value: staff-effort metrics use verified minutes × AI volume vs claimed hours;
  other metrics scale the reported value by the share of the claimed effect observed. The equivalence margin
  is half the claimed effect. Pilot sample sizes assume record-level independence.
- **Limitations.** Observational data: verdicts are strong evidence, not proof of causation. Measures designed
  by Claude approximate the claimed KPI and need analyst sign-off; they were tried live on 7 projects only.
  Projects whose work no table records get "no operational data can test this claim".
- **Third-party components.** DuckDB, pandas, PyArrow, FastAPI, uvicorn, anthropic[bedrock], openai,
  python-dotenv (`requirements.txt`); fonts Public Sans and Source Serif 4 (SIL Open Font License, self-hosted).
- **Responsible AI.** No individual-level ranking. Human decision on every audit. Generated text cites evidence
  and is numerically checked. SQL written by Claude is limited to validated expressions and fingerprinted
  before it runs. Raw tables are never sent to the model.

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
