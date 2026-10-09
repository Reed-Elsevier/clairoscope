"""Decision memo (Markdown) built only from the audit report: every figure cites its evidence id."""
from __future__ import annotations


def decision_memo(r: dict, decision: dict | None = None) -> str:
    uc, v, claim = r["use_case"], r["verdict"], r.get("claim") or {}
    ev = {e["id"]: e for e in r["evidence"]}
    lines = [
        f"# AI Value Audit: {uc['name']} ({uc['use_case_id']})",
        "",
        f"*Audit {r['audit_id']}, {r['created_at']}, mode: {r['mode']}. Demonstration prototype on synthetic "
        f"REPH data; not a validated REPH finding.*",
        "",
        f"## Verdict: {v['verdict']}" + ("  (contested: LLM disagreed with rubric)" if r.get("contested") else ""),
        "",
        f"**{v['headline']}**",
        "",
        v["summary"],
        "",
        "## The claim",
        "",
        f"- KPI: {claim.get('kpi_name', 'n/a')}: {claim.get('baseline')} -> {claim.get('current')} "
        f"(source: {claim.get('source', 'n/a')}, reported {claim.get('measured_date') or 'n/a'})",
        f"- Stage: {uc['stage']}; rollout: {r['rollout']['date']} ({r['rollout']['source']})",
        f"- Business case: ${uc['expected_annual_value_usd']:,.0f} expected, "
        f"${uc['realized_annual_value_usd']:,.0f} reported as realised",
        "",
        "## Findings",
        "",
    ]
    md = r.get("measure_design")
    if md:
        s = md["spec"]
        lines[-2:-2] = ["## Measure designed by Claude", "",
                        f"No catalog measure fits this project, so Claude designed one from the data dictionary: "
                        f"**{s['label']}** ({s['unit']}) from `{s['table']}`. {s['why']}",
                        f"- Assumptions: {' '.join(s['assumptions']) or 'none stated'}",
                        f"- Validated before measuring (safe SQL, columns exist, {md['counts']['n']:,} records); "
                        f"pre-registered {md['registered_at']}, SHA-256 `{md['sha256']}`.",
                        f"- Comparisons: {', '.join(md['designs'])}. {' '.join(md['notes'])}",
                        "- **An analyst should confirm this measure before the result is relied on.**",
                        "", "```sql", md["sql"], "```", ""]
    for f in v["findings"]:
        lines.append(f"- {f['text']} [{', '.join(f['evidence_ids'])}]")
    if v["hidden_costs"]:
        lines += ["", "## Hidden costs", ""]
        lines += [f"- {h['text']} [{', '.join(h['evidence_ids'])}]" for h in v["hidden_costs"]]
    if r["traps"]:
        lines += ["", "## Evidence traps", ""]
        lines += [f"- **{t['title']}** ({t['severity']}): {t['detail']}"
                  + (f" *[{t['reference']}]*" if t.get("reference") else "") for t in r["traps"]]
    val = r.get("value") or {}
    if val:
        lines += ["", "## Value", "",
                  f"- Reported realised value: ${val['reported_usd']:,.0f}/yr; evidence-supported: "
                  f"**${val['supported_usd']:,.0f}/yr** ({val['support_ratio']:.0%})",
                  f"- Basis: {val['basis']}"]
    rob = {k: v for k, v in (r.get("robustness") or {}).items() if v}
    if rob or r.get("power"):
        lines += ["", "## Robustness checks", ""]
        lines += [f"- {k.replace('_', ' ')}: {ev[v]['summary']} [{v}]" for k, v in rob.items()]
        if r.get("power"):
            lines.append(f"- power: {r['power']['text']} -> evidence strength "
                         f"**{r['rubric'].get('evidence_strength')}**")
    lines += ["", "## Rubric", ""] + [f"- {x}" for x in r["rubric"]["reasons"]]
    lines += ["", "## Recommendation", "",
              f"**{v['recommendation']['action'].replace('_', ' ').title()}**: {v['recommendation']['text']}",
              "", f"Next validation step: {v['next_validation_step']}"]
    c = r.get("pilot_charter")
    if c:
        lines += ["", "## Pilot charter (pre-registration)", "",
                  f"- Hypothesis: {c['hypothesis']}", f"- Design: {c['design']}",
                  f"- Margin {c['margin']} on baseline {c['baseline']}; {c['n_per_arm']:,} records per arm; "
                  f"~{c['duration_months']} month(s) at current volume",
                  *[f"- Guardrail: {g}" for g in c["guardrails"]],
                  f"- Decision rule: {c['decision_rule']}"]
    if decision:
        lines += ["", "## Human decision", "",
                  f"- Reviewer: {decision['reviewer']}",
                  f"- Agrees with verdict: {'yes' if decision['agrees'] else 'no'}",
                  f"- Decision: {decision['decision']}",
                  f"- Note: {decision['note'] or '-'}"]
    lines += ["", "## Evidence receipts", ""]
    for e in ev.values():
        lines.append(f"- **{e['id']}** {e['summary']}")
    lines += ["", "## Method notes", "",
              "- Documents measurable performance improvements or declines of a deployed AI system "
              "(cf. NIST AI RMF MEASURE 4.3).",
              "- Numbers are computed by deterministic SQL over the REPH data package; the LLM only plans the test "
              "and writes the narrative.",
              "- Observational data: verdicts are strong evidence, not proof of causation.",
              f"- Numeric grounding: {r['grounding'].get('verified', 0)}/{r['grounding'].get('numbers_checked', 0)} "
              f"numbers in the narrative matched evidence."]
    return "\n".join(lines)
