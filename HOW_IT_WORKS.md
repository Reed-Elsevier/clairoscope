# How Clairoscope works

Clairoscope answers one question for each AI project: **does it really deliver what its team says it does?**
It takes the team's claim, measures the same thing in the day-to-day work records, checks whether anything
other than the AI could explain the result, and returns a plain verdict with the evidence behind it.

This page explains how a check works, where AI is used (and where it deliberately is not), which data is
used, and what the limits are. The [README](README.md) covers setup, results and the demo.

---

## 1. Who it is for and what they see

**Users:** the AI Centre of Excellence and division leaders who decide whether to scale, fix, pause or stop an
AI project. They should not need to read charts or statistics.

A check returns one page, in this order:

| Section | What it says |
|---|---|
| **The answer** | One sentence and a verdict stamp, e.g. *"Partly. The benefit is real, but something important got worse."* |
| **They said vs we found** | The team's claimed change next to the measured change, e.g. "−43%" vs "−45%" |
| **What the claim didn't mention** | Hidden costs: related measures that got worse, e.g. legal quality-check pass rate 97.9% → 20.3% |
| **Can we trust this result?** | Up to seven yes/no questions with tick marks: are the reported numbers accurate, was it compared fairly, was it the AI and not something else, does the benefit last, is there enough data, is it run responsibly, and (for a measure Claude designed) is this the right thing to measure |
| **Is the money real?** | Value the team reports per year vs the part the data backs |
| **What we recommend** | Scale, fix first, keep watching, measure properly, pause or stop, plus the next step |
| **Your decision** | A person records the decision; a decision memo can be downloaded |
| **Show the evidence** | For analysts: charts, every number with its SQL and sample record IDs, the agent's steps |

Any of the **600** projects can be checked, by name or ID, or by pasting a claim in plain words.

---

## 2. How a check works, step by step

### Step 1: Read the project record
Clairoscope loads the project from the AI portfolio: its stage, owning team, model, rollout date, the value it
reports and the claim it logged (a KPI with a "before" and an "after" number).

- **Rollout date** = pilot date, else scaled date, else the first recorded use of the AI.
- **Idea-stage projects** (198 of 600) have never run and have no result, so the check stops here and says
  *"Not live yet: nothing to check."*

### Step 2: Choose what to measure
The claim names a KPI ("classification minutes per document"). Clairoscope needs the same thing in the work
records. There are two routes:

1. **Standard measures.** A catalog of 28 hand-built measures over the work records (support handling time,
   legal task minutes, XML rework rate, invoice exception hours, alert false-positive rate and so on). Claude
   picks the measure, the fair comparison and the hidden costs to watch, and says how well the catalog fits
   this project. The 8 flagship projects also have analyst-reviewed plans, used whenever Claude is off.
2. **Measure builder (when no standard measure fits).** Claude searches the dataset's data dictionary
   (descriptions of all 116 tables and 1,042 columns) for the tables that record this project's work, and
   designs a new measure from them. Code then decides whether to trust the design; see
   [section 4](#4-keeping-results-honest). If no table records the work, Clairoscope says so instead of
   stretching a poor match.

### Step 3: Make a fair comparison
Comparing "before" and "after" alone is weak: many things change over time. Clairoscope uses the strongest
comparison the data allows, and runs the others as cross-checks:

| Comparison | Plain meaning | Used when |
|---|---|---|
| **Teams with vs without the AI, before and after** (difference-in-differences) | Did teams that switched the AI on improve *more* than teams that did not, over the same period? | The data records which teams adopted it and when |
| **Work done with vs without the AI** | In the same months, is AI-assisted work better than similar work done without it? | Each record says whether the AI was used |
| **Before vs after the rollout** | Did the measure change after the AI arrived? Weakest: anything else that changed at the same time also counts | Only a rollout date is known |

If the comparisons disagree, no verdict of success is given.

### Step 4: Look for hidden costs
Speed is worthless if quality drops. For each project Clairoscope also measures related outcomes the claim does
not mention (quality-check pass rate, accuracy, reopened cases, escalations, zero-result searches) and flags
any that got worse.

### Step 5: Try to prove the result wrong
Before a result is believed it has to survive these checks:

| Check | What it catches |
|---|---|
| **Placebo rollout** | Pretend the AI launched 6 months earlier. If an "effect" appears anyway, the change was a trend, not the AI. (Caught UC0006.) |
| **Matching trends before rollout** | Adopting and non-adopting teams must have moved together before the AI arrived |
| **Mix check** (Simpson's paradox) | Is the AI just used on easier work? The effect is recomputed within each kind of work |
| **Durability** | Does the benefit last, or fade after the novelty wears off? |
| **Enough data** | Could the data have detected the claimed effect at all? Separates "no effect" from "can't tell" |
| **What else changed** | Finds the month the measure really shifted and what changed then (e.g. a tool upgrade). (Caught XConvert 4.0 behind UC0003.) |
| **Skeptic (Claude)** | Claude reads the evidence and asks for extra checks that could overturn the conclusion |

### Step 6: Decide the verdict
Fixed, readable rules decide the verdict from the evidence. Claude does not.

| Verdict | Meaning |
|---|---|
| **Works** (PROVEN) | A real improvement that survives the checks |
| **Works, with a hidden cost** (TRADE-OFF) | The benefit is real, but something important got worse |
| **Not proven** (UNPROVEN) | The data does not show the claimed benefit, or cannot test it |
| **Doesn't hold up** (CONTRADICTED) | The data contradicts what was reported |

The rules also apply 19 "evidence traps", such as: the KPI was measured before the pilot started; the reported
number can't be reproduced; recent records are too new to judge; the project is "live" but nobody uses it; it
has no approved governance review; or the AI only looked good in testing. Claude may disagree with a verdict,
but only with a stated reason, and the page then says *"Claude disagrees with this verdict"*.

### Step 7: Put a dollar figure on it
- For staff-time measures: minutes saved × AI-assisted volume in the last 12 months = staff hours verified,
  compared with the hours claimed.
- For other measures: the reported value is scaled by the share of the claimed effect that was actually seen.
- Nothing is credited unless the improvement survived the checks.

### Step 8: Explain and record
Claude writes the headline and summary in plain words. Every number in its text is checked against the
evidence. A person records the decision, and a Markdown decision memo is produced, including a **pilot
charter** (what to measure, by how much, for how long, with how many teams held back) for the next step.

---

## 3. Where AI is used, and where it is not

The code does all of the arithmetic and makes every verdict. Claude (on Amazon Bedrock) does the analyst work
around it:

| Task | Who does it | Why |
|---|---|---|
| Understand a claim typed in plain words | Claude | Free text |
| Choose a measure, comparison and hidden costs | Claude, validated by code | Judgement across 28 measures |
| Design a new measure when none fits | Claude, validated by code | Reading 116 tables' documentation is analyst work |
| Argue against the emerging result | Claude | A second reviewer catches what rules miss |
| Explain the verdict in plain words | Claude, every number checked | Clarity for non-analysts |
| Run queries, statistics, robustness checks | Code | Must be exact and repeatable |
| Decide the verdict | Code (rules) | Must be consistent and auditable |
| Compute the money backed by data | Code | Must be exact |

If Claude is unavailable, every check still runs in rule-based mode; the page says so on every result.

**Measured contribution, honestly:** on the 8 flagship projects the verdicts are the same with or without
Claude, because they have reviewed plans. Claude's value shows on the other projects. In a live test on 7
projects that the standard measures could not cover, Claude designed 4 validated measures, said honestly that
no data records the work for 2, and on 1 it caught a confounder (a logging change) that the rules had missed;
the rules were then fixed. This is a small sample and is not yet a measured success rate.

---

## 4. Keeping results honest

| Safeguard | What it prevents |
|---|---|
| **Only this project's AI counts** | Records of another project's AI must not credit or blame this one. AI flags in the standard measures belong to one flagship each (e.g. REPH Copilot on support cases belongs to UC0001); other projects measured there are compared before vs after their own rollout only. Where records name the model that did the work, only this project's model counts. |
| **Shared tools prove nothing** | General tools such as CodePilot, Atlas Chat, DocAssist and Investigation Assistant are used by 327 projects each, so their use cannot show one project's effect |
| **Claude-designed measures are checked before running** | Only plain SQL expressions (no queries of other tables, no file or network access); every column must exist; at least 300 records and 30 per comparison group; the AI flag must be this project's |
| **Pre-registration** | A designed measure is fixed and fingerprinted (SHA-256) before any result is computed. While repairing a design Claude sees only errors and row counts, never results |
| **Outcome delay** | Records too recent for their outcome to be final are excluded (e.g. reviews not yet returned), otherwise any recent rollout would look like an improvement |
| **No success without a control when something else changed** | A before/after result cannot be "Works" if another change coincided with it |
| **Grounding check** | Every number in Claude's text must match the evidence; mismatches are listed |
| **Verdict guard** | Claude cannot silently change a verdict; a disagreement needs a reason and is shown |
| **Labelled for review** | Results from a Claude-designed measure say so and ask for analyst sign-off |
| **Human decision** | Clairoscope recommends; a person decides, and the decision is recorded |

---

## 5. Data used

**Source:** only the synthetic REPH AI Summit 2026 dataset provided for the hackathon (`center_data.zip`):
116 tables, about 8.9 million rows, in 11 domain folders, plus its documentation. No outside data is added and
no data is modified or generated. Locally the files live in `data/` (kept out of git); on AWS they are read from
a private S3 location in the event account. `python scripts/make_used_data.py` copies just the tables below plus
the data dictionary into one flat folder, `used/` (78 MB instead of 228 MB), for upload; with that subset the measure builder
only considers the tables present.

**Tables the code reads (26), by folder under `data/`:**

| Folder | Tables | What they tell us |
|---|---|---|
| `J_ai_portfolio` | `ai_use_cases`, `ai_use_case_kpis`, `ai_usage_events`, `ai_governance_reviews`, `model_versions` | **What teams claim**: the 600 projects, their stage and dates, reported value, the KPI they logged, whether the AI is used, governance reviews, model test scores |
| `F_customer` | `support_cases` | Support handling time, satisfaction, reopened and escalated cases, on-time resolution; which cases used REPH Copilot |
| `C_legal` | `editorial_tasks`, `regulatory_updates`, `regulatory_update_impacts`, `jurisdictions` | Legal task minutes, accuracy and quality-check results, which tasks were AI-assisted and by which model; regulatory update turnaround |
| `B_publishing` | `content_production_jobs`, `manuscripts`, `research_integrity_flags`, `research_papers_published` | XML conversion rework, on-time production, days to first decision, integrity issues caught before publication |
| `G_finance` | `invoices`, `invoice_exceptions` | Invoice exception resolution hours and rates, invoices without a purchase order |
| `D_risk` | `risk_alerts`, `alert_rules` | Alert false-positive rate, model-based vs other rules |
| `K_knowledge` | `search_logs` | Search success and zero-result searches |
| `E_events` | `leads` | Lead-to-customer conversion and lead scores |
| `H_technology` | `it_tickets`, `access_requests` | IT ticket resolution, access request turnaround |
| `I_process` | `process_definitions`, `process_event_log` | Process cycle times and rework |
| `A_workforce` | `teams`, `divisions` | Which teams adopted the copilot and when; names |

**Also used:**
- `data/_docs/data_dictionary.csv` and `03_data_dictionary.md`: the measure builder searches these to find the
  right tables, and may then measure any other work-record table it finds, such as `peer_review_assignments`
  or `system_events`. Tables where teams self-report about AI (`J_ai_portfolio`) are never used as evidence of
  an effect.

**Not used:** free text such as emails, chat and meeting notes. In this synthetic dataset it is templated (for
example, the 24,000 written comments in AI feedback use only 10 distinct phrases), so reading it would add little.

**What is sent to Claude:** the project record, the claim, table and column descriptions, and computed evidence
summaries. Raw tables are never sent, and Claude runs on Amazon Bedrock inside the approved environment.

---

## 6. Coverage across the 600 projects

| Stage | Projects | Result |
|---|---|---|
| Idea | 198 | "Not live yet": never run, nothing to check |
| PoC, Pilot, Scaled, Retired | 402 | 167 get a measured result from the standard measures alone (no Claude); the measure builder covers more of the rest when Claude is on |

Across the 8 flagships, teams report **$827,000 a year**; the data backs **$272,423 (33%)**. Two of the eight
claims hold up, one of them with a severe hidden cost.

---

## 7. Limits

- **Observational data.** The comparisons are strong evidence, not proof of cause and effect.
- **Claude-designed measures are approximations** of the claimed KPI and need an analyst's sign-off; their
  quality has been tried on a small live sample only.
- **Most designed measures can only compare before vs after**, so they often end in "Not proven" rather than a
  firm answer.
- **Synthetic data.** Results are demonstrations, not validated REPH findings.
- **Statistics are simplified**: normal approximations, record-level independence, an equivalence margin of
  half the claimed effect.

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Claim / KPI** | The before and after numbers a project team logged to show its AI works |
| **Reported value** | Annual value the team entered in the AI portfolio |
| **Backed value** | The part of the reported value the work records support |
| **Hidden cost** | A related measure the AI made worse that the claim did not mention |
| **Difference-in-differences** | Compare how much adopting teams changed with how much other teams changed over the same time |
| **Placebo test** | Run the same comparison at a fake earlier rollout date; a real AI effect should not appear there |
| **Confounder** | Something else that changed at the same time and could explain the result |
| **Pre-registration** | Fixing what will be measured, and how, before seeing any result |
| **Data dictionary** | The dataset's own description of every table and column |
| **Evidence receipt** | A number in the result with the exact SQL and sample record IDs behind it |
