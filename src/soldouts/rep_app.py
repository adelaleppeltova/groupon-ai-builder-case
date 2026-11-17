"""Single-page Streamlit interface for sold-out triage."""
import csv
import os
from pathlib import Path
from zipfile import BadZipFile

import streamlit as st

from soldouts.llm import DEFAULT_MODEL, interpret, select_records
from soldouts.rep import assess_event, demo_data, evidence_empty_message, live_context, live_events
from soldouts.sandbox import Sandbox, SandboxError, string

PACK = Path(__file__).resolve().parents[2] / "data-pack.zip"
ACTION_LABELS = {"call": "Call the merchant", "no_call": "Do not call",
                 "check_automation": "Check automation", "ops_followup": "Follow up with operations",
                 "manual_review": "Review before acting"}
SOURCE_LABELS = {"rep_notes": "Rep note", "deal_alert_replies": "Merchant alert reply",
                 "reviews": "Customer review", "refunds": "Refund reason",
                 "history": "Merchant GP history", "schedules": "Automation schedules", "past": "Prior sold-outs"}
LIMIT_LABELS = {"stale": "Older than the evidence window", "missing_rep_id": "Rep ID missing",
                "possibly_truncated": "Note may be truncated", "invalid_encoding": "Text encoding issue"}


@st.cache_data(show_spinner=False)
def load_demo(path, modified_at):
    return demo_data(path)


def show_finance(report):
    finance = report["finance"]
    if not finance or finance["status"] != "scenario_estimate":
        st.caption("GBP estimate unavailable. There is no usable prior voucher history or decision time.")
        return
    scenarios = finance["scenarios"]
    values = []
    for name in ("low", "base", "high"):
        amount = scenarios[name]["expected_net_value_gbp"]
        sign = "-" if amount < 0 else ""
        values.append(f"{name.title()} {sign}£{abs(amount):,.2f}")
    st.markdown(" · ".join(values))
    st.caption("Illustrative net value if a cap raise is pursued. Assumptions need Priya’s review.")
    with st.expander("How the GBP estimates were calculated"):
        baseline = report["baseline"]
        st.text(f"Prior net GP per voucher £{baseline['net_gp_per_voucher_gbp']:,.2f} "
                f"across {len(baseline['months'])} completed months with records.")
        for name, scenario in scenarios.items():
            inputs = scenario["assumptions"]
            st.text(f"{name.title()} · +{inputs['added_capacity']} vouchers · "
                    f"{inputs['incremental_fill_rate']:.0%} incremental fill · "
                    f"£{scenario['rep_cost_gbp']:.2f} rep time · "
                    f"£{inputs['excess_refund_gp_loss_gbp']:.2f} excess refund GP risk")


def show_evidence(label, signals, empty_message, schedules=()):
    with st.expander(label, expanded=label == "Deal evidence"):
        if not signals and not schedules:
            st.caption(empty_message)
        for schedule in schedules:
            st.text(f"Schedule {schedule['schedule_id']} · starts {schedule['start_date']}")
            st.caption("The schedule covers this date. Verify that the top-up ran and check the current capacity.")
        for signal in sorted(signals, key=lambda item: item["stale"]):
            ids = ", ".join(signal["source_ids"])
            st.text(f"{SOURCE_LABELS[signal['source']]} · {ids} · {signal['source_timestamp']}")
            st.text(signal["quote"].encode("utf-8", errors="backslashreplace").decode("utf-8"))
            limits = set(signal["limitations"])
            if signal["stale"]:
                limits.add("stale")
            labels = [value for key, value in LIMIT_LABELS.items() if key in limits]
            if labels:
                st.caption(" · ".join(labels))


def show_mark(event, api):
    st.subheader("Merchant mark")
    st.caption("Records your judgement for the merchant. Routing is independent of this mark.")
    if api is None:
        st.caption("Demo marks are kept in this session.")
    marks = st.session_state.setdefault("marks", {})
    merchant_id = event.merchant_id
    if merchant_id not in marks:
        try:
            marks[merchant_id] = api.merchant_mark(merchant_id) if api else None
        except (SandboxError, ValueError) as exc:
            st.warning(f"Could not load the current mark. {exc}")
            return
    current = marks[merchant_id] or {}
    if flash := st.session_state.pop(f"mark_flash_{merchant_id}", None):
        st.success(flash)
    if current:
        st.text(f"{current['mark']} · {current['marked_by']}")
        st.text(current["reason"])
    else:
        st.caption("No mark is set.")
    revision = st.session_state.get(f"mark_revision_{merchant_id}", 0)
    with st.form(f"mark_{merchant_id}_{revision}"):
        code = st.text_input("Mark code", current.get("mark", ""), max_chars=64, placeholder="capacity_confirmed")
        reason = st.text_area("Reason", current.get("reason", ""), placeholder="What did you verify?")
        by = st.text_input("Marked by (your rep ID)", placeholder=event.rep_id or "R01")
        save = st.form_submit_button("Save mark", type="primary")
        remove = st.form_submit_button("Remove mark") if current else False
    if not (save or remove):
        return
    try:
        if remove:
            if api:
                api.remove_mark(merchant_id)
            marks[merchant_id] = None
        else:
            code, reason, by = code.strip(), reason.strip(), by.strip()
            if not (code and reason and by):
                raise ValueError("Enter a mark code, a reason, and your rep ID.")
            marks[merchant_id] = (api.set_mark(merchant_id, code, reason, by) if api else
                                  {"mark": code, "reason": reason, "marked_by": by})
    except SandboxError as exc:
        st.error(f"{exc} Refresh the sandbox before trying another edit.")
        return
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state[f"mark_revision_{merchant_id}"] = revision + 1
    st.session_state[f"mark_flash_{merchant_id}"] = "Mark removed." if remove else "Mark saved."
    st.rerun()


def show_ai(event, tables, api, max_age_days):
    if api is None or os.getenv("GROUPON_ENABLE_LLM") != "1":
        return
    model = os.getenv("GROUPON_LLM_MODEL", DEFAULT_MODEL)
    with st.expander("Optional AI interpretation"):
        st.caption(f"{model} reviews up to eight recent records. Review suggestions before acting.")
        results = st.session_state.setdefault("ai_results", {})
        if event not in results:
            used = st.session_state.get("ai_requests", 0)
            if st.button("Analyse evidence (uses gateway budget)", disabled=used >= 3):
                records = select_records(event, tables, max_age_days)
                if records:
                    st.session_state["ai_requests"] = used + 1
                with st.spinner("Checking the evidence…"):
                    results[event] = interpret(api, records, model)
            if used >= 3:
                st.caption("This session has used its three analysis requests.")
        result = results.get(event)
        if not result:
            return
        if result["status"] == "fallback":
            st.warning(f"Using deterministic evidence. {result['reason']}")
            return
        st.caption("Model interpretation · Quotations checked against source records")
        st.text(result["uncertainty"])
        if not result["findings"]:
            st.caption("The model found no clear signals in the selected records.")
        for title, deal_linked in (("Deal interpretations", True), ("Merchant context interpretations", False)):
            matches = [f for f in result["findings"] if bool(f["deal_id"]) == deal_linked]
            if matches:
                st.text(title)
            for finding in matches:
                st.text(f"{ACTION_LABELS[finding['proposed_action']]} · {finding['category'].replace('_', ' ')}")
                st.text(f"{SOURCE_LABELS[finding['source']]} · {', '.join(finding['source_ids'])} · {finding['timestamp']}")
                st.text(finding["quote"])
                st.text(finding["rationale"])
                st.text(finding["limitation"])


def main():
    st.set_page_config(page_title="Sold-out triage", layout="centered")
    st.title("Sold-out triage")
    key = os.getenv("GROUPON_API_KEY", "").strip()
    api = Sandbox(key) if key else None
    try:
        max_age_days = int(os.getenv("GROUPON_EVIDENCE_DAYS", "90"))
        if not 0 <= max_age_days <= 3650:
            raise ValueError
    except ValueError:
        st.error("Set GROUPON_EVIDENCE_DAYS to a whole number from 0 to 3650.")
        return

    if api:
        if st.button("Refresh sandbox"):
            for name in ("today", "context", "marks", "ai_results"):
                st.session_state.pop(name, None)
        if "today" not in st.session_state:
            try:
                with st.spinner("Loading today’s sold-outs…"):
                    st.session_state.today = api.today()
            except SandboxError as exc:
                st.error(f"Could not load today’s sandbox sold-outs. {exc}")
                return
        page = st.session_state.today
        events, skipped = live_events(page["data"])
        team_date = string(page.get("date"))
        st.caption(f"Live sandbox · Team date {team_date}" if team_date else "Live sandbox · Today on the team clock")
        if skipped:
            st.warning(f"{skipped} rows lack merchant or deal details and could not be shown.")
    else:
        st.info("Offline demo · Historical sold-outs from the supplied data pack")
        path = Path(os.getenv("GROUPON_DATA_PACK", str(PACK)))
        try:
            events, tables = load_demo(str(path), path.stat().st_mtime_ns)
        except (OSError, ValueError, KeyError, csv.Error, BadZipFile):
            st.error("Could not read the demo pack. Set GROUPON_DATA_PACK to the supplied ZIP or CSV directory.")
            return

    if not events:
        st.info("No sold-outs to show.")
        return
    rep_names = {event.rep_id: event.rep_name or event.rep_id for event in events if event.rep_id}
    if rep_names:
        rep = st.selectbox("Rep", [None, *sorted(rep_names)], format_func=lambda id: rep_names[id] if id else "All reps")
        if rep is not None:
            events = [event for event in events if event.rep_id == rep]
    event = st.selectbox("Sold-out", events, format_func=lambda item: item.label)
    missing = []
    if api:
        contexts = st.session_state.setdefault("context", {})
        context_key = (event.merchant_id, event.sold_out_at, max_age_days)
        if context_key not in contexts:
            with st.spinner("Loading evidence for this sold-out…"):
                contexts[context_key] = live_context(api, event, max_age_days)
        tables, missing = contexts[context_key]
    if missing:
        st.warning("Unavailable: " + ", ".join(SOURCE_LABELS[source] for source in missing)
                   + ". Refresh the sandbox to try again.")
    report = assess_event(event, tables, max_age_days=max_age_days, live=api is not None,
                          incomplete=any(table not in {"history", "past"} for table in missing))
    st.caption(f"Merchant {event.merchant_id} · Deal {event.deal_id} · {event.sold_out_at or 'Time unavailable'}")
    title = "Call to verify capacity" if report["previous_increase"] else ACTION_LABELS[report["action"]]
    st.subheader(title)
    st.write(report["reason"])
    if report["action"] == "call":
        show_finance(report)
    st.caption("Quoted text is verifiable at its source. Check whether the reported conditions still apply.")
    text_unavailable = any(source in missing for source in ("rep_notes", "deal_alert_replies", "reviews", "refunds"))
    for scope, label, signals, schedules in (
        ("deal", "Deal evidence", report["deal_evidence"], report["schedules"]),
        ("merchant", "Merchant context", report["merchant_context"], ()),
    ):
        message = evidence_empty_message(report["evidence_counts"], scope, unavailable=text_unavailable)
        if event.cutoff is None:
            message = "Evidence could not be assessed without the sold-out time."
        show_evidence(label, signals, message, schedules)
    show_mark(event, api)
    show_ai(event, tables, api, max_age_days)


if __name__ == "__main__":
    main()
