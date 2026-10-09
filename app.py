"""AI Value Auditor: Streamlit UI.  Run: python -m streamlit run app.py"""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from auditor.config import load_env

from auditor import ledger
from auditor.agent import run_audit
from auditor.catalog import METRICS
from auditor.db import tables
from auditor.engine import monthly
from auditor.facts import get_use_case, list_use_cases, portfolio, portfolio_flags
from auditor.judge import LEVEL_GAP_HIGH, SUPPORT_RATIO
from auditor.llm import get_llm
from auditor.memo import decision_memo

load_env()
st.set_page_config(page_title="AI Value Auditor", page_icon=":mag:", layout="wide")

VERDICT_STYLE = {
    "PROVEN": ("#1a7f37", "Claim holds up"),
    "TRADE-OFF": ("#9a6700", "Real benefit, hidden cost"),
    "UNPROVEN": ("#57606a", "Not supported by the data (yet)"),
    "CONTRADICTED": ("#cf222e", "Data contradicts the claim"),
}
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
DECISIONS = ["Scale", "Fix, then scale", "Keep monitoring", "Measure properly first", "Pause", "Stop"]


# ---------------------------------------------------------------- cached data
@st.cache_resource
def _llm():
    try:
        return get_llm(), None
    except Exception as e:  # missing package / bad provider setting: show it, fall back to deterministic
        return None, str(e)


@st.cache_data
def _use_cases() -> pd.DataFrame:
    return list_use_cases()


@st.cache_data
def _portfolio() -> pd.DataFrame:
    return portfolio_flags(portfolio())


@st.cache_data
def _monthly(metric: str, where: tuple, group: str | None) -> pd.DataFrame:
    df = monthly(metric, dict(where), group)
    return df[df["n"] >= 5]


@st.cache_data(show_spinner="Auditing the 8 flagship claims (deterministic mode)...")
def _flagship_scorecard() -> pd.DataFrame:
    rows = []
    for uc_id in (f"UC000{i}" for i in range(1, 9)):
        r = run_audit(uc_id)
        val = r["value"]
        rows.append({"use_case": f"{uc_id} {r['use_case']['name']}", "stage": r["use_case"]["stage"],
                     "verdict": r["verdict"]["verdict"],
                     "evidence": r["rubric"].get("evidence_strength") or "-",
                     "reported_usd": val["reported_usd"], "evidence_supported_usd": round(val["supported_usd"]),
                     "high_traps": sum(t["severity"] == "high" for t in r["traps"]),
                     "hidden_costs": len(r["harms"])})
    return pd.DataFrame(rows)


@st.cache_data
def _table_count() -> int:
    return len(tables())


# ---------------------------------------------------------------- render helpers
def chart(metric_key: str, where: dict, group: str | None, rollout: str | None, cp_month: str | None,
          height: int = 260) -> alt.LayerChart:
    m = METRICS[metric_key]
    df = _monthly(metric_key, tuple(sorted(where.items())), group)
    line = alt.Chart(df).mark_line(point=alt.OverlayMarkDef(size=18)).encode(
        x=alt.X("month:T", title=None),
        y=alt.Y("mean:Q", title=f"{m.label} ({m.unit})", scale=alt.Scale(zero=False)),
        color=alt.Color("grp:N", title=None, legend=alt.Legend(orient="bottom")),
        tooltip=[alt.Tooltip("month:T", format="%b %Y"), "grp:N", alt.Tooltip("mean:Q", format=".2f"), "n:Q"],
    )
    marks = []
    if rollout:
        marks.append({"date": rollout, "label": "AI rollout"})
    if cp_month:
        marks.append({"date": cp_month, "label": "Largest shift"})
    layers = [line]
    if marks:
        mdf = pd.DataFrame(marks)
        layers.append(alt.Chart(mdf).mark_rule(strokeDash=[4, 4]).encode(
            x="date:T", color=alt.value("#888"), tooltip=["label:N", "date:T"]))
        layers.append(alt.Chart(mdf).mark_text(align="left", dx=4, dy=-110, fontSize=11, color="#666").encode(
            x="date:T", text="label:N"))
    return alt.layer(*layers).properties(height=height)


def chart_group(design: str | None, metric_key: str) -> str | None:
    m = METRICS[metric_key]
    if design == "did" and m.has_adopter:
        return "adopter"
    if design == "treated" and m.has_treated:
        return "treated"
    return None


def verdict_banner(v: dict, contested: bool) -> None:
    color, tag = VERDICT_STYLE[v["verdict"]]
    st.markdown(
        f"""<div style="border-left:8px solid {color};padding:14px 18px;border-radius:6px;
        background:rgba(127,127,127,0.07);margin-bottom:8px">
        <div style="font-size:0.85rem;color:{color};font-weight:700;letter-spacing:0.06em">
        {v['verdict']} &middot; {tag}</div>
        <div style="font-size:1.35rem;font-weight:650;margin:4px 0">{v['headline']}</div>
        <div style="opacity:0.85">{v['summary']}</div></div>""", unsafe_allow_html=True)
    if contested:
        st.warning(f"The LLM disagreed with the rubric: {v['override_reason']}")


def cite(ids: list[str]) -> str:
    return " ".join(f"`{i}`" for i in ids)


def render_report(r: dict) -> None:
    v, rub, eff, plan = r["verdict"], r["rubric"], r["primary_effect"], r["plan"]
    verdict_banner(v, r.get("contested", False))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Claimed change", f"{rub['claimed_rel']:+.1%}" if rub.get("claimed_rel") is not None else "n/a")
    c2.metric("Observed change", f"{eff['rel']:+.1%}" if eff and eff.get("rel") is not None else "n/a",
              help=f"Design: {eff['design']}, p={eff['p']:.2g}" if eff else None)
    c3.metric("Hidden costs", len(r["harms"]), help="Guardrail metrics that got significantly worse")
    c4.metric("Evidence traps", sum(t["severity"] == "high" for t in r["traps"]), help="High-severity traps")
    g = r["grounding"]
    c5.metric("Numbers grounded", f"{g.get('verified', 0)}/{g.get('numbers_checked', 0)}",
              help="Numbers in the AI-written text that match the evidence log"
                   + (f". Unverified: {', '.join(g['unverified'])}" if g.get("unverified") else ""))

    val, pw = r.get("value") or {}, r.get("power")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Reported realised value", f"${val.get('reported_usd', 0):,.0f}/yr")
    d2.metric("Evidence-supported value", f"${val.get('supported_usd', 0):,.0f}/yr",
              delta=f"{val.get('support_ratio', 0) - 1:.0%}" if val.get("reported_usd") else None,
              help=val.get("basis"))
    d3.metric("Verified staff hours/yr",
              f"{val['verified_hours']:,.0f}" if val.get("verified_hours") is not None else "n/a",
              help=f"Claimed: {val.get('claimed_hours', 0):,.0f} hours/yr")
    d4.metric("Evidence strength", (rub.get("evidence_strength") or "n/a").capitalize(),
              help=pw["text"] if pw else "Statistical power could not be computed")
    if val.get("basis"):
        st.caption(f"Value basis: {val['basis']}")

    left, right = st.columns([3, 2])
    with left:
        if plan:
            pm = plan["primary"]["metric"]
            st.markdown(f"**{METRICS[pm].label}**  " + (f"({plan['primary']['where']})" if plan["primary"]["where"] else ""))
            st.altair_chart(chart(pm, plan["primary"]["where"], chart_group(eff["design"] if eff else None, pm),
                                  r["rollout"]["date"], (r.get("changepoint") or {}).get("month")),
                            use_container_width=True)
        st.markdown("#### Findings")
        for f in v["findings"]:
            st.markdown(f"- {f['text']} {cite(f['evidence_ids'])}")
        if v["hidden_costs"]:
            st.markdown("#### Hidden costs")
            for h in v["hidden_costs"]:
                st.markdown(f"- {h['text']} {cite(h['evidence_ids'])}")
    with right:
        rec = v["recommendation"]
        st.markdown("#### Recommendation")
        st.info(f"**{rec['action'].replace('_', ' ').title()}**: {rec['text']}\n\n"
                f"*Next validation step:* {v['next_validation_step']}")
        st.markdown("#### Evidence traps")
        if not r["traps"]:
            st.caption("None found.")
        for t in r["traps"]:
            ref = f"<br><span style='opacity:0.55;font-size:0.8rem'>{t['reference']}</span>" if t.get("reference") else ""
            st.markdown(f"{SEVERITY_ICON[t['severity']]} **{t['title']}** {cite(t['evidence_ids'])}  \n"
                        f"<span style='opacity:0.8'>{t['detail']}</span>{ref}", unsafe_allow_html=True)
        st.markdown("#### Rubric")
        for reason in rub["reasons"]:
            st.caption(reason)

    if plan and plan["guardrails"]:
        st.markdown("#### Guardrail metrics")
        cols = st.columns(min(3, len(plan["guardrails"])))
        for i, gr in enumerate(plan["guardrails"]):
            with cols[i % len(cols)]:
                st.caption(f"{METRICS[gr['metric']].label}: {gr['why']}")
                st.altair_chart(chart(gr["metric"], gr["where"], chart_group(eff["design"] if eff else None,
                                                                          gr["metric"]),
                                      r["rollout"]["date"], None, height=200), use_container_width=True)

    render_robustness(r)

    charter = r.get("pilot_charter")
    if charter:
        with st.expander("Pilot charter: pre-registered plan for the next controlled validation"):
            st.markdown(charter_md(charter))
            st.download_button("Download pilot charter (.md)", charter_md(charter),
                               file_name=f"pilot_charter_{r['use_case']['use_case_id']}.md", mime="text/markdown",
                               key=f"charter_{r['audit_id']}")

    with st.expander("Audit plan and cross-checks"):
        if plan:
            st.markdown(f"**Planner:** {plan.get('planner')}  \n**Design:** `{plan['design']}`  \n"
                        f"**Rationale:** {plan.get('rationale', '')}")
            if plan.get("hypotheses"):
                st.markdown("**Hypotheses the audit tested:**\n" + "\n".join(f"- {h}" for h in plan["hypotheses"]))
        for w in r["plan_warnings"]:
            st.caption(f"Plan check: {w}")
        if r["triangulation"]:
            st.markdown("**Cross-check designs** (same metric, different comparison):")
            st.dataframe(pd.DataFrame([{"design": t["design"], "change": t["rel"], "p_value": t["p"],
                                        "AI n": t["n_ai"], "comparison n": t["n_cmp"], "evidence": t["evidence"]}
                                       for t in r["triangulation"]]), hide_index=True)
    with st.expander(f"Evidence receipts ({len(r['evidence'])})"):
        for e in r["evidence"]:
            st.markdown(f"**{e['id']} · {e['title']}**  \n{e['summary']}")
            if e["sample_ids"]:
                st.caption("Sample records: " + "; ".join(f"{k}: {', '.join(vs)}" for k, vs in e["sample_ids"].items()))
            if e["sql"]:
                st.code(e["sql"].strip(), language="sql")
    with st.expander("Agent trace"):
        for s in r["trace"]:
            st.markdown(f"`{s['t']:>5}s` **{s['step']}**: {s['detail']}")

    st.markdown("### Human review")
    with st.form(f"review_{r['audit_id']}"):
        a, b = st.columns(2)
        reviewer = a.text_input("Reviewer (team / role)", value="AI CoE portfolio lead")
        decision = b.selectbox("Decision", DECISIONS, index=_default_decision(v["recommendation"]["action"]))
        agrees = st.checkbox("I agree with the verdict", value=True)
        note = st.text_area("Note", placeholder="Why this decision, what must happen next")
        if st.form_submit_button("Record decision"):
            ledger.save_decision(r["audit_id"], reviewer, agrees, decision, note)
            st.session_state["decision"] = {"reviewer": reviewer, "agrees": agrees, "decision": decision, "note": note}
            st.success("Decision recorded in the ledger.")
    memo = decision_memo(r, st.session_state.get("decision"))
    st.download_button("Download decision memo (.md)", memo, file_name=f"audit_{r['use_case']['use_case_id']}_"
                       f"{r['audit_id']}.md", mime="text/markdown")


ROBUSTNESS_LABELS = {
    "placebo": ("Placebo rollout", "PLACEBO_FAILED", "A fake launch date 6 months early must show no effect."),
    "pre_trend": ("Parallel pre-trends", "PRE_TREND", "Adopters and others must move together before go-live."),
    "mix_adjusted": ("Mix-adjusted (Simpson)", "SIMPSON", "Effect must survive within comparable work."),
    "durability": ("Durability (novelty)", "NOVELTY_DECAY", "Effect must persist in the latest quarters."),
}


def render_robustness(r: dict) -> None:
    rob = r.get("robustness") or {}
    if not rob:
        return
    ev = {e["id"]: e for e in r["evidence"]}
    failed = {t["code"] for t in r["traps"]}
    st.markdown("#### Robustness checks")
    cols = st.columns(5)
    for i, (key, (label, code, why)) in enumerate(ROBUSTNESS_LABELS.items()):
        eid = rob.get(key)
        with cols[i]:
            if not eid:
                st.markdown(f"⚪ **{label}**  \n<span style='opacity:0.6'>not applicable to this design</span>",
                            unsafe_allow_html=True)
            else:
                icon = "🔴" if code in failed else "🟢"
                st.markdown(f"{icon} **{label}** `{eid}`  \n<span style='opacity:0.75'>{ev[eid]['summary']}</span>",
                            unsafe_allow_html=True, help=why)
    with cols[4]:
        pw = r.get("power")
        strength = (r["rubric"].get("evidence_strength") or "n/a")
        icon = {"decisive": "🟢", "claim ruled out": "🔴", "adequately powered": "🟠", "underpowered": "⚪"}.get(strength, "⚪")
        st.markdown(f"{icon} **Power & equivalence**  \n<span style='opacity:0.75'>"
                    f"{pw['text'] if pw else 'not computed'}</span>", unsafe_allow_html=True,
                    help="Separates 'no effect' from 'cannot tell' (minimum detectable effect, TOST equivalence).")


def charter_md(c: dict) -> str:
    return "\n".join([
        f"**Use case:** {c['use_case']}  ",
        f"**Primary metric:** {c['primary_metric']}  ",
        f"**Hypothesis:** {c['hypothesis']}  ",
        f"**Design:** {c['design']}  ",
        f"**Baseline:** {c['baseline']} (sd {c['sd']}); **margin:** {c['margin']}  ",
        f"**Sample size:** {c['n_per_arm']:,} records per arm; at ~{c['volume_per_month']:,} records/month "
        f"that is about **{c['duration_months']} month(s)**  ",
        "**Guardrails:**",
        *[f"- {g}" for g in c["guardrails"]],
        f"\n**Decision rule:** {c['decision_rule']}  ",
        f"*{c['note']}*",
    ])


def _default_decision(action: str) -> int:
    return {"scale": 0, "fix_then_scale": 1, "keep_monitoring": 2, "measure_properly": 3, "pause": 4,
            "stop": 5}.get(action, 2)


# ---------------------------------------------------------------- layout
llm, llm_error = _llm()
with st.sidebar:
    st.markdown("## AI Value Auditor")
    st.caption("Tests what AI projects claim against what the operational data shows.")
    try:
        st.success(f"Data: {_table_count()} tables loaded")
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()
    if llm:
        st.success(f"LLM: {llm.name}")
        use_llm = st.toggle("Use LLM agent", value=True,
                            help="Off = same pipeline with pre-registered plans and templated text")
    else:
        st.warning("LLM: off (deterministic mode)" + (f"\n\n{llm_error}" if llm_error else ""))
        use_llm = False
    st.divider()
    st.caption("All data is synthetic (REPH AI Summit 2026 package). Demonstration prototype: outputs are not "
               "validated REPH findings.")

tab_audit, tab_port, tab_ledger, tab_how = st.tabs(["Audit a claim", "Portfolio truth map", "Decision ledger",
                                                    "How it works"])

with tab_audit:
    ucs = _use_cases()
    labels = {r.use_case_id: f"{r.use_case_id} · {r.use_case_name} ({r.stage})" for r in ucs.itertuples()}
    ids = list(labels)
    mode = st.radio("Input", ["Pick a use case", "Type a claim"], horizontal=True, label_visibility="collapsed")
    claim_text, uc_id = None, None
    if mode == "Pick a use case":
        default = st.session_state.get("selected_uc", "UC0002")
        uc_id = st.selectbox("AI use case", ids, index=ids.index(default) if default in ids else 0,
                             format_func=labels.get)
        uc = get_use_case(uc_id)
        cl = uc.get("claim")
        if cl:
            st.caption(f"**Claim on record:** {cl['kpi_name']} {cl['baseline']} → {cl['current']} "
                       f"(reported {cl['measured_date']}) · stage {uc['stage']} · business case "
                       f"${uc['expected_annual_value_usd']:,.0f}/yr, reported realised "
                       f"${uc['realized_annual_value_usd']:,.0f}/yr")
    else:
        claim_text = st.text_area("Claim", placeholder="e.g. The legal classifier cut classification time by 43% "
                                                       "with no loss of quality", height=80)
    if st.button("Run audit", type="primary", disabled=(mode == "Type a claim" and not claim_text)):
        st.session_state.pop("decision", None)
        with st.status("Auditing...", expanded=True) as status:
            def on_step(name: str, detail: str) -> None:
                status.write(f"**{name}**: {detail}")
            try:
                report = run_audit(uc_id, claim_text=claim_text, llm=llm if use_llm else None, on_step=on_step)
                ledger.save_audit(report)
                st.session_state["report"] = report
                status.update(label=f"Audit complete: {report['verdict']['verdict']}", state="complete",
                              expanded=False)
            except ValueError as e:
                status.update(label="Audit failed", state="error")
                st.error(str(e))
    if "report" in st.session_state:
        render_report(st.session_state["report"])

with tab_port:
    df = _portfolio()
    live = df[df["stage"].isin(["Pilot", "Scaled"])]
    k = st.columns(5)
    k[0].metric("Use cases", len(df))
    k[1].metric("Live (pilot/scaled)", len(live))
    k[2].metric("Live with no usage records", int(live["flag_ghost_adoption"].sum()))
    k[3].metric("Live without approved review", int(live["flag_governance_gap"].sum()))
    k[4].metric("Value realised (scaled)",
                f"{df.loc[df.stage == 'Scaled', 'realized_usd'].sum() / max(df.loc[df.stage == 'Scaled', 'expected_usd'].sum(), 1):.0%}")
    st.markdown("#### Flagship scorecard: reported vs evidence-supported value")
    sc = _flagship_scorecard()
    s1, s2, s3 = st.columns(3)
    s1.metric("Reported realised value (8 flagships)", f"${sc['reported_usd'].sum():,.0f}/yr")
    s2.metric("Evidence-supported value", f"${sc['evidence_supported_usd'].sum():,.0f}/yr")
    s3.metric("Claims that hold up", f"{(sc['verdict'].isin(['PROVEN', 'TRADE-OFF'])).sum()} of {len(sc)}")
    st.dataframe(sc, hide_index=True, column_config={
        "reported_usd": st.column_config.NumberColumn("Reported $/yr", format="$%d"),
        "evidence_supported_usd": st.column_config.NumberColumn("Evidence-supported $/yr", format="$%d")})
    st.markdown("#### Portfolio red-flag screen (all 600 use cases)")
    st.caption("Screened in one query over all use cases, no LLM. Red flags: ghost adoption, governance gap, KPI "
               "measured before pilot, claim numbers showing decline, scaled with <50% of expected value.")
    scatter = alt.Chart(df[df["stage"] != "Idea"]).mark_circle(opacity=0.75).encode(
        x=alt.X("expected_usd:Q", title="Expected annual value (USD)"),
        y=alt.Y("realized_usd:Q", title="Reported realised value (USD)"),
        size=alt.Size("usage_events:Q", title="Usage events", scale=alt.Scale(range=[20, 400])),
        color=alt.Color("red_flags:O", title="Red flags", scale=alt.Scale(scheme="orangered")),
        tooltip=["use_case_id", "use_case_name", "stage", "expected_usd", "realized_usd", "usage_events", "red_flags"],
    ).properties(height=360)
    st.altair_chart(scatter, use_container_width=True)
    show = df.sort_values(["red_flags", "expected_usd"], ascending=False)[
        ["use_case_id", "use_case_name", "stage", "division", "expected_usd", "realized_usd", "usage_events",
         "approved_reviews", "kpi_name", "baseline", "current", "red_flags", "flag_ghost_adoption",
         "flag_governance_gap", "flag_kpi_before_pilot", "flag_claim_shows_decline", "flag_value_gap"]]
    st.dataframe(show, hide_index=True, height=380)
    pick = st.selectbox("Queue a use case for audit", show["use_case_id"].tolist(), format_func=labels.get)
    if st.button("Send to the Audit tab"):
        st.session_state["selected_uc"] = pick
        st.success(f"{pick} selected. Open the 'Audit a claim' tab and press Run audit.")

with tab_ledger:
    hist = ledger.history()
    if hist.empty:
        st.caption("No audits yet.")
    else:
        st.dataframe(hist.drop(columns=["audit_id"]), hide_index=True)
        aid = st.selectbox("Open a past audit", hist["audit_id"].unique().tolist(),
                           format_func=lambda a: f"{a} · " + hist.loc[hist.audit_id == a, "use_case_name"].iloc[0])
        past = ledger.load_report(aid)
        if past:
            st.markdown(decision_memo(past))

with tab_how:
    st.markdown(f"""
### Pipeline
1. **Plan (LLM)**: reads the use case, its claim and a catalog of {len(METRICS)} operational metrics, then designs a
   fair test: the primary metric, the strongest feasible comparison (difference-in-differences > AI vs non-AI
   records > before/after rollout), **guardrail metrics the claim does not mention**, and follow-up checks.
   The LLM never writes SQL; its plan is validated against the catalog.
2. **Execute (code)**: DuckDB computes every number. Each result is logged as an evidence item with its SQL
   and sample record IDs. All other feasible designs run as cross-checks.
3. **Confounder hunt (code)**: finds the month the metric shifted most and checks which dimension
   (tool version, vendor, channel...) changed its mix at the same time.
4. **Skeptic (LLM)**: argues against the emerging conclusion and requests up to three checks that could overturn it.
5. **Robustness (code)**: placebo rollout date, parallel pre-trends, mix-adjusted (Simpson's paradox) effect,
   durability (novelty decay), minimum detectable effect and an equivalence test that separates
   "no effect" from "cannot tell".
6. **Traps + rubric (code)**: evidence traps (KPI measured before pilot, irreproducible baseline, right-censored
   outcomes, ghost adoption, governance gap, offline-eval vs production gap, confounder, small sample) and a
   readable verdict rubric. Value is translated into evidence-supported $ and a pre-registered pilot charter.
7. **Verdict (LLM)**: writes the conclusion citing evidence IDs. It cannot silently change the rubric verdict;
   numbers in its text are checked against the evidence log.
8. **Human decision**: a reviewer records the decision; the ledger keeps audit + decision; a memo is exported.

### Rubric
- **PROVEN**: significant improvement reaching at least {SUPPORT_RATIO:.0%} of the claimed effect, no major hidden cost.
- **TRADE-OFF**: as PROVEN, but a guardrail got significantly worse (≥15% relative or ≥5 pp).
- **UNPROVEN**: no significant improvement, under {SUPPORT_RATIO:.0%} of the claim, designs disagree, a robustness
  check fails (placebo, pre-trend, Simpson), or the data cannot evaluate it yet.
- **CONTRADICTED**: significant change in the wrong direction, the claimed value is more than
  {LEVEL_GAP_HIGH:.0%} worse in the operational data, or an equivalence test rules out even half the claimed effect.
""")
