"""Metric catalog: the semantic layer the auditor (and the LLM) may measure with.

The planner LLM chooses metrics, filters and designs from this catalog; the engine turns them into
parameterised queries. Projects the catalog does not fit go to measure_builder.py, where Claude writes
SQL *expressions* that code validates before running. Every metric's SQL yields one row per
operational record with columns: id, ts, value, plus optional treated / adopter / go_live
and the declared dimension columns. Rate metrics emit 0 or 100 so means are percentages.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str  # "min" | "hours" | "%" | "score"
    higher_is_better: bool
    source: str
    description: str
    sql: str
    dims: tuple[str, ...] = ()
    treated_label: str | None = None  # set when rows carry a per-record AI flag
    adopter_label: str | None = None  # set when rows carry adopter group + go_live (enables diff-in-diff)
    outcome_lag_days: int = 0  # outcome needs this long to materialise (right-censoring risk)
    tags: tuple[str, ...] = field(default=())
    labor: bool = False  # value is staff effort per record (minutes saved = capacity released)

    @property
    def has_treated(self) -> bool:
        return self.treated_label is not None

    @property
    def has_adopter(self) -> bool:
        return self.adopter_label is not None


def _support(expr: str) -> str:
    return f"""
        select s.case_id as id, s.created_at as ts, {expr} as value,
               s.ai_copilot_used as treated,
               t.ai_copilot_go_live is not null as adopter, t.ai_copilot_go_live as go_live,
               s.category, s.channel, s.priority, s.team_id, s.division_id
        from support_cases s join teams t using (team_id)"""


def _legal(expr: str) -> str:
    return f"""
        select task_id as id, created_at as ts, {expr} as value, ai_assisted as treated,
               task_type, team_id, model_id
        from editorial_tasks"""


def _xml(expr: str) -> str:
    return f"""
        select job_id as id, started_at as ts, {expr} as value,
               tool_version, vendor_name, team_id
        from content_production_jobs where stage = 'XML Conversion'"""


_SUPPORT_DIMS = ("category", "channel", "priority", "team_id", "division_id")

METRICS: dict[str, Metric] = {m.key: m for m in [
    # --- Customer support (UC0001 CX Case Copilot) ---
    Metric("support_aht", "Support handling time", "min", False, "support_cases",
           "Average agent handling time per support case.",
           _support("s.handle_time_min"), _SUPPORT_DIMS, "Copilot used on case", "Copilot teams", tags=("support",),
           labor=True),
    Metric("support_csat", "Customer satisfaction score (CSAT)", "score", True, "support_cases",
           "Customer satisfaction score (1-5) per case.",
           _support("s.csat"), _SUPPORT_DIMS, "Copilot used on case", "Copilot teams", tags=("support",)),
    Metric("support_reopen", "Support reopen rate", "%", False, "support_cases",
           "Share of cases reopened after resolution.",
           _support("s.reopened::int * 100"), _SUPPORT_DIMS, "Copilot used on case", "Copilot teams", tags=("support",)),
    Metric("support_escalation", "Support escalation rate", "%", False, "support_cases",
           "Share of cases escalated.",
           _support("s.escalated::int * 100"), _SUPPORT_DIMS, "Copilot used on case", "Copilot teams", tags=("support",)),
    Metric("support_sla", "Support cases resolved on time (SLA)", "%", True, "support_cases",
           "Share of cases resolved within SLA.",
           _support("s.sla_met::int * 100"), _SUPPORT_DIMS, "Copilot used on case", "Copilot teams", tags=("support",)),
    # --- Legal editorial (UC0002 Legal Auto-Classify) ---
    Metric("legal_minutes", "Legal task minutes", "min", False, "editorial_tasks",
           "Editor minutes per legal editorial task (filter task_type, e.g. Classify).",
           _legal("minutes_spent"), ("task_type", "team_id", "model_id"), "AI-assisted task", tags=("legal",),
           labor=True),
    Metric("legal_accuracy", "Legal task accuracy", "score", True, "editorial_tasks",
           "Accuracy score (0-1) assigned by legal QA.",
           _legal("accuracy_score"), ("task_type", "team_id", "model_id"), "AI-assisted task", tags=("legal",)),
    Metric("legal_qa_pass", "Legal quality check (QA) pass rate", "%", True, "editorial_tasks",
           "Share of editorial tasks passing QA.",
           _legal("qa_passed::int * 100"), ("task_type", "team_id", "model_id"), "AI-assisted task", tags=("legal",)),
    Metric("legal_update_latency", "Regulatory update latency", "hours", False, "regulatory_update_impacts",
           "Hours from regulatory update to content updated (72h SLA).",
           """select i.impact_id as id, u.captured_at as ts, i.latency_hours as value,
                     j.jurisdiction_name as jurisdiction, u.update_type
              from regulatory_update_impacts i join regulatory_updates u using (update_id)
              join jurisdictions j using (jurisdiction_id)""", ("jurisdiction", "update_type"), tags=("legal",)),
    # --- Publishing production (UC0003 XML Auto-Validation Agent) ---
    Metric("xml_rework_rate", "Article file (XML) conversion rework rate", "%", False, "content_production_jobs",
           "Share of XML conversion jobs needing at least one rework.",
           _xml("(rework_count >= 1)::int * 100"), ("tool_version", "vendor_name", "team_id"), tags=("publishing",)),
    Metric("xml_sla", "Article file (XML) conversions on time", "%", True, "content_production_jobs",
           "Share of XML conversion jobs finished within SLA.",
           _xml("sla_met::int * 100"), ("tool_version", "vendor_name", "team_id"), tags=("publishing",)),
    Metric("xml_hours", "Article file (XML) conversion hours", "hours", False, "content_production_jobs",
           "Elapsed hours per XML conversion job.",
           _xml("elapsed_hours"), ("tool_version", "vendor_name", "team_id"), tags=("publishing",)),
    Metric("production_sla", "Production jobs on time, all stages", "%", True, "content_production_jobs",
           "Share of production jobs (all stages) within SLA.",
           """select job_id as id, started_at as ts, sla_met::int * 100 as value, stage, vendor_name
              from content_production_jobs""", ("stage", "vendor_name"), tags=("publishing",)),
    Metric("manuscript_first_decision_days", "Days to first decision", "days", False, "manuscripts",
           "Days from submission to first editorial decision.",
           """select manuscript_id as id, submitted_at as ts,
                     datediff('hour', submitted_at, first_decision_at) / 24.0 as value, journal_id, article_type
              from manuscripts where first_decision_at is not null""", ("journal_id", "article_type"), tags=("publishing",)),
    # --- Finance AP (UC0004 Invoice Exception Triage Agent) ---
    Metric("ap_exception_hours", "Hours to resolve invoice exceptions", "hours", False, "invoice_exceptions",
           "Hours from exception raised to resolved.",
           """select exception_id as id, raised_at as ts,
                     datediff('minute', raised_at, resolved_at) / 60.0 as value, exception_type, resolution
              from invoice_exceptions where resolved_at is not null""", ("exception_type", "resolution"),
           outcome_lag_days=4, tags=("finance",)),
    Metric("ap_exception_rate", "Invoice exception rate", "%", False, "invoices, invoice_exceptions",
           "Share of invoices raising at least one exception.",
           """select i.invoice_id as id, i.received_at as ts,
                     (exists (select 1 from invoice_exceptions e where e.invoice_id = i.invoice_id))::int * 100 as value,
                     i.channel, i.approval_level
              from invoices i""", ("channel", "approval_level"), tags=("finance",)),
    Metric("ap_no_po_rate", "Invoices without a purchase order", "%", False, "invoices",
           "Share of invoices received without a purchase order.",
           """select invoice_id as id, received_at as ts, (po_id is null)::int * 100 as value, channel, approval_level
              from invoices""", ("channel", "approval_level"), tags=("finance",)),
    # --- Risk (UC0005 Fraud Alert Prioritisation Model) ---
    Metric("fraud_fp_rate", "Alert false-positive rate", "%", False, "risk_alerts, alert_rules",
           "Share of closed alerts that were false positives.",
           """select a.alert_id as id, a.created_at as ts, a.is_false_positive::int * 100 as value,
                     r.rule_type = 'Model-based' as treated, r.rule_id, r.rule_name, r.rule_type
              from risk_alerts a join alert_rules r using (rule_id) where a.disposition <> 'Open'""",
           ("rule_id", "rule_name", "rule_type"), "Model-based rule", tags=("risk",)),
    # --- Knowledge (UC0006 Enterprise Knowledge RAG Assistant) ---
    Metric("search_click_rate", "Search success (click-through)", "%", True, "search_logs",
           "Share of searches where the user opened a result.",
           """select search_id as id, searched_at as ts, (clicked_article_id is not null)::int * 100 as value,
                     source = 'Knowledge RAG Assistant' as treated, source
              from search_logs""", ("source",), "Knowledge RAG Assistant", tags=("knowledge",)),
    Metric("search_zero_rate", "Zero-result search rate", "%", False, "search_logs",
           "Share of searches returning no results (knowledge gaps).",
           """select search_id as id, searched_at as ts, (results_count = 0)::int * 100 as value,
                     source = 'Knowledge RAG Assistant' as treated, source
              from search_logs""", ("source",), "Knowledge RAG Assistant", tags=("knowledge",)),
    # --- Publishing integrity (UC0007 Research Integrity Screening) ---
    Metric("integrity_prepub_rate", "Integrity issues caught pre-publication", "%", True,
           "research_integrity_flags, research_papers_published",
           "Share of integrity flags raised before the paper was published.",
           """select f.flag_id as id, f.flagged_at as ts,
                     coalesce(f.flagged_at < p.published_date, false)::int * 100 as value,
                     f.detected_by = 'Screening tool' as treated, f.flag_type, f.detected_by
              from research_integrity_flags f left join research_papers_published p using (paper_id)""",
           ("flag_type", "detected_by"), "Detected by screening tool", tags=("publishing",)),
    # --- Events (UC0008 Event Lead Scoring) ---
    Metric("lead_conversion", "Lead-to-customer conversion", "%", True, "leads",
           "Share of exhibitor leads that became customers.",
           """select lead_id as id, captured_at as ts, (converted_customer_id is not null)::int * 100 as value,
                     case when lead_score >= 80 then '80-100' when lead_score >= 60 then '60-79'
                          when lead_score >= 40 then '40-59' else '0-39' end as score_band,
                     lead_status, event_id
              from leads""", ("score_band", "lead_status", "event_id"), outcome_lag_days=160, tags=("events",)),
    # --- Technology ---
    Metric("it_resolution_hours", "IT ticket resolution hours", "hours", False, "it_tickets",
           "Hours from ticket creation to resolution.",
           """select ticket_id as id, created_at as ts, datediff('minute', created_at, resolved_at) / 60.0 as value,
                     category, priority, ticket_type
              from it_tickets where resolved_at is not null""", ("category", "priority", "ticket_type"), tags=("technology",)),
    Metric("it_sla", "IT tickets resolved on time", "%", True, "it_tickets",
           "Share of IT tickets resolved within SLA.",
           """select ticket_id as id, created_at as ts, sla_met::int * 100 as value, category, priority, ticket_type
              from it_tickets where sla_met is not null""", ("category", "priority", "ticket_type"), tags=("technology",)),
    Metric("it_reopen", "IT ticket reopen rate", "%", False, "it_tickets",
           "Share of IT tickets reopened.",
           """select ticket_id as id, created_at as ts, reopened::int * 100 as value, category, priority, ticket_type
              from it_tickets""", ("category", "priority", "ticket_type"), tags=("technology",)),
    Metric("access_turnaround_hours", "Access request turnaround", "hours", False, "access_requests",
           "Hours to fulfil an access request.",
           """select access_request_id as id, requested_at as ts, turnaround_hours as value, access_type, status
              from access_requests where turnaround_hours is not null""", ("access_type", "status"), tags=("technology",)),
    # --- Process mining (generic: any use case whose process has an event log) ---
    Metric("process_cycle_hours", "Process cycle time", "hours", False, "process_event_log",
           "Hours from first to last event of a process case (filter process_id).",
           """select case_id as id, min(event_ts) as ts,
                     datediff('minute', min(event_ts), max(event_ts)) / 60.0 as value, any_value(process_id) as process_id
              from process_event_log group by case_id""", ("process_id",), tags=("process",)),
    Metric("process_rework_rate", "Process rework rate", "%", False, "process_event_log",
           "Share of process cases that repeat an activity (filter process_id).",
           """select case_id as id, min(event_ts) as ts,
                     (count(*) > count(distinct activity))::int * 100 as value, any_value(process_id) as process_id
              from process_event_log group by case_id""", ("process_id",), tags=("process",)),
]}


def catalog_for_prompt() -> list[dict]:
    """Compact catalog description handed to the LLM planner."""
    return [
        {
            "key": m.key, "label": m.label, "unit": m.unit,
            "better": "higher" if m.higher_is_better else "lower",
            "source": m.source, "description": m.description, "dims": list(m.dims),
            "per_record_ai_flag": m.treated_label, "adopter_groups_for_did": m.adopter_label,
            "outcome_lag_days": m.outcome_lag_days,
        }
        for m in METRICS.values()
    ]
