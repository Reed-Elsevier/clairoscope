// AI Value Auditor front end. No framework, no CDN: works inside a locked-down AWS network.
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Math.round(n || 0).toLocaleString("en-US");

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

const STEP_LABELS = {
  "Parse claim": "Understanding the claim",
  "Load facts": "Reading the project record",
  "Plan": "Designing a fair test",
  "Plan repair": "Adjusting the test",
  "Measure": "Measuring in operations data",
  "Hidden costs": "Looking for hidden costs",
  "Confounder hunt": "Checking what else changed at the time",
  "Investigate": "Trying to prove the result wrong",
  "Robustness": "Stress-testing the result",
  "Rubric": "Applying the verdict rules",
  "Verdict": "Writing the verdict",
};
const DECISIONS = [
  ["scale", "Scale it"], ["fix_then_scale", "Fix first, then scale"], ["keep_monitoring", "Keep going, keep watching"],
  ["measure_properly", "Measure properly first"], ["pause", "Pause and investigate"], ["stop", "Stop"],
];
// auditors' tick marks: verified, query, exception
const TICK = {
  pass: '<path d="M5 15l6 6L23 7"/>',
  warn: '<path d="M10 9.5a4 4 0 1 1 5.5 3.7c-1.2.5-1.5 1.3-1.5 2.6"/><path d="M14 21.4v.2"/>',
  fail: '<path d="M7 7l14 14M21 7L7 21"/>',
};
const tick = (status, label) => `<svg class="tick ${status}" viewBox="0 0 28 28" role="img" aria-label="${label}">${TICK[status]}</svg>`;

const state = { useCases: [], llm: null };

// ---------------------------------------------------------------- init
init();

async function init() {
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  $("#ai-status").addEventListener("click", reloadLlm);
  const [cfg, ucs] = await Promise.all([api("/api/config"), api("/api/use-cases")]);
  renderLlm(cfg.llm);
  state.useCases = ucs;
  $("#uc-list").innerHTML = ucs.map((u) => `<option value="${esc(u.name)} (${esc(u.id)})">${esc(u.stage)}${u.flagship ? ", flagship" : ""}</option>`).join("");
  loadOverview();
  const deep = new URLSearchParams(location.search).get("uc");
  if (new URLSearchParams(location.search).get("ai") === "0") $("#use-llm").checked = false; // fast demo link
  if (deep) { $("#ask-input").value = deep; submit(); }
}

// Claude connection: green = ready, red = unavailable (select to reload .env and retry)
function renderLlm(llm) {
  state.llm = llm;
  const btn = $("#ai-status");
  const model = llm.name ? llm.name.split(" · ")[1] || llm.name : "";
  $(".txt", btn).textContent = {
    ok: `Claude ready${model ? ` (${model.replace(/^global\.anthropic\./, "")})` : ""}`,
    checking: "Connecting to Claude",
    off: "Claude off: rule-based explanations",
    error: "Claude unavailable: select to retry",
  }[llm.state] || llm.state;
  btn.title = llm.error || "Select to re-check the connection to Claude";
  btn.className = "ai-status " + ({ ok: "ok", error: "bad" }[llm.state] || "");
  const usable = llm.state === "ok" || llm.state === "checking";
  $("#use-llm").disabled = !usable;
  if (!usable) $("#use-llm").checked = false;
  const help = $("#ai-help");
  help.classList.toggle("hidden", llm.state !== "error");
  if (llm.state === "error") help.textContent = explainLlmError(llm.error || "");
  if (llm.state === "checking") setTimeout(async () => renderLlm((await api("/api/config")).llm), 2000);
}

function explainLlmError(msg) {
  const tail = " Checks still run; the explanation is written by rules instead of Claude.";
  if (/short-term key/i.test(msg))
    return "Claude can't connect: the Bedrock key stopped working when the AWS console session that created it ended. " +
      "Create a long-term Bedrock API key (or use an IAM role), put it in .env, then select the red status to retry." + tail;
  if (/credentials rejected|api key/i.test(msg))
    return "Claude can't connect: Amazon Bedrock rejected the key. Put a new key in .env, then select the red status to retry." + tail;
  if (/not available|access denied/i.test(msg))
    return "Claude can't connect: this AWS account has no access to the chosen model. Enable it in the Bedrock console or pick another model." + tail;
  return "Claude can't connect: " + msg.split(". ")[0] + "." + tail;
}

async function reloadLlm() {
  $(".txt", $("#ai-status")).textContent = "Reconnecting to Claude";
  try {
    renderLlm((await api("/api/llm/reload", { method: "POST" })).llm);
    if (state.llm.state === "ok") $("#use-llm").checked = true;
  } catch (e) { $(".txt", $("#ai-status")).textContent = "Claude: " + e.message; }
}

// One box: a project (name or ID) or a free-text claim
function resolveInput(text) {
  const t = text.trim();
  if (!t) return null;
  const id = (t.match(/\bUC\d{4}\b/i) || [])[0];
  if (id) return { use_case_id: id.toUpperCase() };
  const low = t.toLowerCase();
  const exact = state.useCases.find((u) => u.name.toLowerCase() === low);
  if (exact) return { use_case_id: exact.id };
  const looksLikeClaim = t.split(/\s+/).length >= 5 || /\d/.test(t);
  if (!looksLikeClaim) {
    const hit = state.useCases.find((u) => u.name.toLowerCase().includes(low));
    if (hit) return { use_case_id: hit.id };
  }
  return { claim_text: t };
}

function submit() {
  $("#ask-error").classList.add("hidden");
  const body = resolveInput($("#ask-input").value);
  if (!body) return showError("Type a project name, an ID like UC0002, or the claim you want checked.");
  runAudit(body);
}

function showError(msg) {
  const el = $("#ask-error");
  el.textContent = msg; el.classList.remove("hidden");
}

// ---------------------------------------------------------------- audit run
async function runAudit(body) {
  body.use_llm = $("#use-llm").checked;
  const btn = $("#run-btn");
  btn.disabled = true; btn.textContent = "Checking";
  $("#result").classList.add("hidden");
  $("#progress").classList.remove("hidden");
  $("#progress-title").textContent = body.use_llm ? "Checking with Claude, about 30 to 40 seconds" : "Checking";
  $("#steps").innerHTML = `<li class="now">Starting</li>`;
  $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const { job_id } = await api("/api/audits", { method: "POST", body: JSON.stringify(body) });
    for (;;) {
      await new Promise((r) => setTimeout(r, 900));
      const job = await api(`/api/jobs/${job_id}`);
      renderSteps(job.steps, job.status === "running");
      if (job.status === "done") {
        renderResult(job.result);
        if (body.use_llm) renderLlm((await api("/api/config")).llm);
        break;
      }
      if (job.status === "error") throw new Error(job.error);
    }
  } catch (err) {
    showError("The check could not finish: " + err.message);
  } finally {
    btn.disabled = false; btn.textContent = "Check it";
    $("#progress").classList.add("hidden");
  }
}

function renderSteps(steps, running) {
  // plain step names only; the technical detail stays in a tooltip and in the evidence section
  $("#steps").innerHTML = steps.map((s, i) => {
    const now = running && i === steps.length - 1;
    return `<li class="${now ? "now" : ""}" title="${esc(String(s.detail ?? "").slice(0, 300))}"><span class="label">${esc(STEP_LABELS[s.step] || s.step)}</span></li>`;
  }).join("");
}

// ---------------------------------------------------------------- result
function renderResult(res) {
  const v = res.view, r = res.report, u = v.use_case;
  const el = $("#result");
  el.className = `sheet finding tone-${v.color}`;
  const today = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  el.innerHTML = `
    <header class="finding-head">
      <p class="project-line">${esc(u.name)}, project ${esc(u.use_case_id)}.
        ${u.division ? `${esc(u.division)}, ` : ""}${esc(String(u.stage || "").toLowerCase())} stage.</p>
      <h2 class="answer">${esc(v.answer)}</h2>
      <p class="headline">${esc(v.headline)}</p>
      ${v.claim_text ? `<p class="headline">Claim checked: “${esc(v.claim_text)}”</p>` : ""}
      ${v.ai_note ? `<p class="note">${esc(v.ai_note)}</p>` : ""}
      ${v.contested ? `<p class="note">Claude disagreed with the rule-based verdict. The evidence below shows both sides.</p>` : ""}
    </header>
    <div class="stamp land" aria-label="Verdict: ${esc(v.verdict_label)}">${esc(v.verdict_label)}<small>Checked ${esc(today)}</small></div>
    ${comparisonPart(v.comparison)}
    ${costsPart(v.hidden_costs)}
    ${checksPart(v.checks)}
    ${moneyPart(v.value)}
    <section class="part"><h3>What we recommend</h3>
      <p class="recommend">${esc(v.action.label)}</p><p class="part-text">${esc(v.next_step)}</p></section>
    ${decisionPart(res.audit_id, v.action.key)}
    ${evidencePart(r)}`;
  el.classList.remove("hidden");
  wireDecision(res.audit_id, v.action.key);
  $("#evidence").addEventListener("toggle", (e) => { if (e.target.open) drawSeries(r); }, { once: true });
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function bar(value, max, cls) {
  const w = max > 0 ? Math.max(1.5, Math.min(100, (Math.abs(value) / max) * 100)) : 0;
  return `<div class="bar ${cls}"><span style="width:${w.toFixed(1)}%"></span></div>`;
}

function comparisonPart(c) {
  if (!c) return `<section class="part"><h3>What they said, and what we found</h3>
    <p class="part-text pencil">No operations data can test this claim yet.</p></section>`;
  const level = c.kind === "level";
  const better = c.higher_is_better ? c.after_raw > c.before_raw : c.after_raw < c.before_raw;
  const max = Math.max(Math.abs(c.before_raw || 0), Math.abs(c.after_raw || 0));
  return `<section class="part">
    <h3>${level ? "What they reported, and what the data shows" : "What they said, and what we found"}</h3>
    <div class="said-found">
      <div class="said"><div class="who">${level ? "They reported" : "They said"}</div><div class="fig">“${esc(c.said)}”</div></div>
      <div class="found"><div class="who">${level ? "Operations data shows" : "We found"}</div><div class="fig">${esc(c.found)}</div></div>
    </div>
    <p class="metric-name">${esc(c.metric)}</p>
    <div class="change"><span class="pencil">${level ? "Reported" : "Without AI"}</span><span class="vals">${esc(c.before)}</span>
      <div class="bars">${bar(c.before_raw, max, "")}</div></div>
    <div class="change"><span class="pencil">${level ? "Actual" : "With AI"}</span><span class="vals">${esc(c.after)}</span>
      <div class="bars">${bar(c.after_raw, max, better ? "better" : "worse")}</div></div>
    <p class="how">How we compared: ${esc(c.how)}.</p>
  </section>`;
}

function costsPart(costs) {
  if (!costs.length) return `<section class="part"><h3>What the claim didn't mention</h3>
    <ul class="ticks"><li>${tick("pass", "Fine")}<div><div class="q">Nothing important got worse</div>
    <div class="a">We checked the related quality, risk and cost measures for this kind of work.</div></div></li></ul></section>`;
  return `<section class="part"><h3>What the claim didn't mention</h3>
    ${costs.map((h) => {
      const max = Math.max(Math.abs(h.before_raw || 0), Math.abs(h.after_raw || 0));
      return `<div class="change"><span class="label">${esc(h.label)}</span>
        <span class="vals"><span class="was">${esc(h.before)}</span> to <span class="now">${esc(h.after)}</span></span>
        <div class="bars">${bar(h.before_raw, max, "")}${bar(h.after_raw, max, "worse")}</div>
        ${h.severity === "major" ? `<span class="serious">A serious drop: this needs fixing before any rollout.</span>` : ""}</div>`;
    }).join("")}</section>`;
}

function checksPart(checks) {
  const label = { pass: "Yes", warn: "Partly", fail: "No" };
  return `<section class="part"><h3>Can we trust this result?</h3><ul class="ticks">
    ${checks.map((c) => `<li>${tick(c.status, label[c.status])}
      <div><div class="q">${esc(c.question)}</div><div class="a">${esc(c.answer)}</div></div></li>`).join("")}
  </ul></section>`;
}

function moneyPart(val) {
  const pct = val.reported_raw ? Math.round((100 * val.supported_raw) / val.reported_raw) : 0;
  return `<section class="part"><h3>Is the money real?</h3>
    <div class="money">
      <div class="money-col"><span class="lbl">Reported by the team</span><span class="amt">${esc(val.reported)}</span><span class="lbl">a year</span></div>
      <div class="money-col"><span class="lbl">Backed by the data</span><span class="amt backed">${esc(val.supported)}</span><span class="lbl">a year</span></div>
    </div>
    ${val.reported_raw ? `<div class="meter" role="img" aria-label="${pct}% of the reported value is backed"><span style="width:${pct}%"></span></div>` : ""}
    <p class="part-text">${esc(val.sentence)}</p><p class="how">${esc(val.basis)}</p></section>`;
}

function decisionPart(auditId, recommended) {
  return `<section class="part decide" id="decide"><h3>Your decision</h3>
    <p class="part-text pencil" style="margin-bottom:12px">The auditor recommends; a person decides. Your decision is saved with this check.</p>
    <div class="options" role="group" aria-label="Decision">${DECISIONS.map(([k, label]) =>
      `<button type="button" class="opt" data-k="${k}" aria-pressed="${k === recommended}">${esc(label)}${k === recommended ? ' <span class="rec">(recommended)</span>' : ""}</button>`).join("")}</div>
    <div class="fields">
      <input id="reviewer" value="AI CoE portfolio lead" aria-label="Your role">
      <textarea id="note" rows="2" placeholder="Why this decision, and what must happen next?" aria-label="Note"></textarea>
    </div>
    <div class="actions"><button type="button" class="btn-ink" id="save-decision">Save decision</button>
      <a class="link" href="/api/audits/${esc(auditId)}/memo">Download the decision memo</a><span id="saved" class="saved"></span></div>
  </section>`;
}

function wireDecision(auditId, recommended) {
  let choice = recommended;
  document.querySelectorAll("#decide .opt").forEach((b) => b.addEventListener("click", () => {
    choice = b.dataset.k;
    document.querySelectorAll("#decide .opt").forEach((x) => x.setAttribute("aria-pressed", x === b));
  }));
  $("#save-decision").addEventListener("click", async () => {
    const label = DECISIONS.find(([k]) => k === choice)[1];
    try {
      await api(`/api/audits/${auditId}/decision`, { method: "POST", body: JSON.stringify({
        reviewer: $("#reviewer").value, decision: label, agrees: choice === recommended, note: $("#note").value }) });
      $("#saved").textContent = `Saved: ${label}. The memo now includes your decision.`;
    } catch (e) { $("#saved").textContent = "Couldn't save the decision: " + e.message; }
  });
}

// ---------------------------------------------------------------- evidence (for analysts)
function evidencePart(r) {
  const plan = r.plan || {};
  const g = r.grounding || {};
  return `<details class="evidence" id="evidence"><summary>Show the evidence (for analysts)</summary>
    <div id="series" class="pencil" style="margin-top:12px">Loading chart</div>
    <h4>Why this verdict</h4><ul>${r.rubric.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    ${r.traps.length ? `<h4>Evidence traps</h4><ul>${r.traps.map((t) => `<li><strong>${esc(t.title)}</strong> (${esc(t.severity)}): ${esc(t.detail)} <span class="pencil">${esc(t.reference || "")}</span></li>`).join("")}</ul>` : ""}
    <h4>Test design</h4><p>${esc(plan.planner || "")}: ${esc(plan.rationale || "")}</p>
    ${(r.plan_warnings || []).map((w) => `<p class="pencil">${esc(w)}</p>`).join("")}
    <p class="pencil">Text written by Claude: ${g.verified ?? 0} of ${g.numbers_checked ?? 0} numbers matched the evidence${g.unverified && g.unverified.length ? ` (unmatched: ${esc(g.unverified.join(", "))})` : ""}. Mode: ${esc(r.mode)}.</p>
    <h4>Evidence receipts (${r.evidence.length})</h4>
    ${r.evidence.map((e) => `<div class="receipt"><span class="rid">${esc(e.id)}</span>${esc(e.summary)}
      ${Object.keys(e.sample_ids || {}).length ? `<div class="pencil">Sample records: ${esc(Object.entries(e.sample_ids).map(([k, v]) => `${k}: ${v.join(", ")}`).join("; "))}</div>` : ""}
      ${e.sql ? `<details><summary class="pencil">SQL</summary><pre>${esc(e.sql.trim())}</pre></details>` : ""}</div>`).join("")}
    <h4>Agent trace</h4><ol>${r.trace.map((s) => `<li><strong>${esc(s.step)}</strong> (${s.t}s): ${esc(s.detail)}</li>`).join("")}</ol>
  </details>`;
}

async function drawSeries(r) {
  const box = $("#series");
  const plan = r.plan, eff = r.primary_effect;
  if (!plan) { box.textContent = "No measure to chart."; return; }
  const group = eff && eff.design === "did" ? "adopter" : eff && eff.design === "treated" ? "treated" : "";
  try {
    const q = new URLSearchParams({ metric: plan.primary.metric, where: JSON.stringify(plan.primary.where || {}) });
    if (group) q.set("group", group);
    const s = await api(`/api/series?${q}`);
    box.classList.remove("pencil");
    box.innerHTML = `<h4 style="margin-top:0">${esc(s.metric)} by month</h4>` + lineChart(s.points, s.unit,
      [r.rollout && r.rollout.date ? { date: r.rollout.date, label: "AI rollout" } : null,
       r.changepoint ? { date: r.changepoint.month, label: "Biggest shift" } : null].filter(Boolean));
  } catch (e) { box.textContent = "Chart unavailable: " + e.message; }
}

function lineChart(points, unit, marks) {
  const W = 760, H = 260, L = 48, R = 12, T = 14, B = 28;
  const months = [...new Set(points.map((p) => p.month))].sort();
  const groups = [...new Set(points.map((p) => p.group))];
  if (!months.length) return "<p class='pencil'>No data.</p>";
  const vals = points.map((p) => p.value);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (m) => L + (months.indexOf(m) / Math.max(1, months.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const colors = ["var(--ink)", "var(--audit)", "var(--works)", "var(--cost)"];
  const lines = groups.map((g, i) => {
    const pts = points.filter((p) => p.group === g).map((p) => `${x(p.month).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
    return `<polyline points="${pts}" fill="none" stroke="${colors[i % 4]}" stroke-width="2.2"/>`;
  }).join("");
  const nearest = (d) => months.reduce((a, m) => (Math.abs(new Date(m) - new Date(d)) < Math.abs(new Date(a) - new Date(d)) ? m : a), months[0]);
  const markSvg = marks.map((mk, i) => {
    const xm = x(nearest(mk.date));
    const right = xm > W - 120; // keep labels inside the chart and off each other
    return `<line x1="${xm}" x2="${xm}" y1="${T}" y2="${H - B}" stroke="var(--pencil)" stroke-dasharray="4 4"/><text x="${right ? xm - 4 : xm + 4}" y="${T + 10 + i * 14}" text-anchor="${right ? "end" : "start"}">${esc(mk.label)}</text>`;
  }).join("");
  const years = months.filter((m) => m.endsWith("-01-01"));
  const xTicks = years.map((m) => `<text x="${x(m)}" y="${H - 8}" text-anchor="middle">${m.slice(0, 4)}</text>`).join("");
  const fmtV = (v) => (unit === "%" ? v.toFixed(0) + "%" : v.toFixed(unit === "score" ? 2 : 1));
  const yTicks = [lo + pad, (lo + hi) / 2, hi - pad].map((v) => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmtV(v)}</text><line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--rule)"/>`).join("");
  const legend = groups.map((g, i) => `<span><i style="background:${colors[i % 4]}"></i>${esc(g)}</span>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly chart">${yTicks}${markSvg}${lines}${xTicks}</svg><div class="legend">${legend}</div>`;
}

// ---------------------------------------------------------------- overview
async function loadOverview() {
  let o = await api("/api/overview");
  while (!o.ready) {
    await new Promise((r) => setTimeout(r, 1500));
    o = await api("/api/overview");
  }
  const n = o.flagships.length;
  $("#hero-line").innerHTML = `Project teams say their AI earns <span class="fig">${money(o.reported_total)}</span> a year.
    The data backs <span class="fig backed">${money(o.supported_total)}</span> of it.`;
  $("#hero-sub").textContent = `${o.holding_up} of the ${n} flagship claims hold up when tested against operations data. ` +
    "Each figure below is measured, not taken from what the teams report.";

  $("#ledger").innerHTML = `
    <div class="ledger-head" aria-hidden="true"><span>Project</span><span>What we found</span><span>Verdict</span>
      <span class="num">Reported a year</span><span class="num">Backed a year</span></div>
    <ul class="ledger-rows">${o.flagships.map((f) => `<li>
      <button type="button" class="ledger-row tone-${f.color}" data-id="${esc(f.use_case_id)}" aria-label="Open the check for ${esc(f.name)}">
        <span class="name">${esc(f.name)}</span>
        <span class="what">${esc(f.headline)}</span>
        <span class="verdict">${esc(f.verdict_label)}</span>
        <span class="num" data-label="Reported">${money(f.reported)}</span>
        <span class="num" data-label="Backed">${money(f.supported)}</span>
      </button></li>`).join("")}</ul>
    <div class="ledger-total"><span>Total, ${n} projects</span><span></span><span></span>
      <span class="num" data-label="Reported">${money(o.reported_total)}</span>
      <span class="num backed" data-label="Backed">${money(o.supported_total)}</span></div>`;
  document.querySelectorAll(".ledger-row").forEach((b) => b.addEventListener("click", () => {
    const u = state.useCases.find((x) => x.id === b.dataset.id);
    $("#ask-input").value = u ? `${u.name} (${u.id})` : b.dataset.id;
    runAudit({ use_case_id: b.dataset.id });
  }));

  const p = o.portfolio;
  $("#portfolio").textContent = `Across all ${p.use_cases} AI projects, ${p.live_no_usage} of the ${p.live} live projects have no recorded usage, ` +
    `and ${p.live_no_review} run without an approved governance review.`;
  $("#redflags").innerHTML = `<table><thead><tr><th>Project</th><th>Stage</th><th class="num">Red flags</th><th class="num">Expected a year</th><th class="num">Usage events</th><th></th></tr></thead><tbody>
    ${o.red_flags.map((r) => `<tr><td>${esc(r.use_case_name)} <span class="pencil">(${esc(r.use_case_id)})</span></td><td>${esc(r.stage)}</td><td class="num">${r.red_flags}</td>
      <td class="num">${money(r.expected_usd)}</td><td class="num">${Number(r.usage_events).toLocaleString("en-US")}</td><td><a class="link" href="?uc=${esc(r.use_case_id)}">Check it</a></td></tr>`).join("")}</tbody></table>`;
}
