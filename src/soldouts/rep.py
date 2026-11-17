"""Evidence and financial context for the rep view."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from .data import TEXT_COLUMNS, UK, active_schedules, pack_reader, timestamp
from .evidence import detect, recommend
from .finance import forecast, merchant_history
from .sandbox import SOURCE_PATHS, SandboxError, string

DEMO_IDS = ("SO-043678", "SO-000006", "SO-044275")


@dataclass(frozen=True)
class SoldOut:
    merchant_id: str
    deal_id: str
    event_id: str
    sold_out_at: str
    merchant_name: str = ""
    deal_name: str = ""
    rep_id: str = ""
    rep_name: str = ""
    do_not_contact: bool | None = None

    @property
    def label(self):
        return (f"{self.merchant_name or self.merchant_id} · "
                f"{self.deal_name or self.deal_id} · {self.event_id or self.sold_out_at or 'No event ID'}")

    @property
    def cutoff(self):
        try:
            return timestamp(self.sold_out_at)
        except (ValueError, OverflowError):
            return None


def live_events(rows):
    events, seen, skipped = [], set(), 0
    for row in rows:
        if not isinstance(row, dict) or not all(string(row.get(k)) for k in ("merchant_id", "deal_id")):
            skipped += 1
            continue
        at, mid, did = (string(row.get(k)) for k in ("sold_out_at", "merchant_id", "deal_id"))
        event_id = string(row.get("sold_out_id")) or string(row.get("restock_task_id"))
        key = (mid, did, event_id, at)
        if key in seen:
            continue
        seen.add(key)
        contact = row.get("do_not_contact")
        events.append(SoldOut(mid, did, event_id, at, string(row.get("merchant_name")),
                              string(row.get("deal_name")), string(row.get("rep_id")),
                              string(row.get("rep_name")), contact if isinstance(contact, bool) else None))
    return events, skipped


def demo_data(path):
    with pack_reader(path) as read:
        merchants = {row["merchant_id"]: row for row in read("merchants")}
        events = {}
        for row in read("sold_out_deals"):
            event_id = row["sold_out_id"]
            if event_id in DEMO_IDS:
                merchant = merchants.get(row["merchant_id"], {})
                # Display metadata is from the snapshot. Do not use current contact
                # preferences to reproduce historical decisions.
                events[event_id] = SoldOut(row["merchant_id"], row["deal_id"], event_id,
                                           row["sold_out_date"], merchant.get("merchant_name", ""),
                                           rep_id=merchant.get("rep_id", ""))
        selected = [events[i] for i in DEMO_IDS if i in events]
        ids = {event.merchant_id for event in selected}
        tables = {name: [r for r in read(name) if r["merchant_id"] in ids]
                  for name in TEXT_COLUMNS}
        for key, table in (("schedules", "auto_replenish_schedules"), ("history", "merchant_months")):
            tables[key] = [r for r in read(table) if r["merchant_id"] in ids]
    return selected, tables


def live_context(api, event, max_age_days):
    """Load one selected merchant's context. A failed feed is displayed as missing."""
    if event.cutoff is None:
        return {}, []
    since = (event.cutoff - timedelta(days=max_age_days)).astimezone(UK).date().isoformat()
    tables, missing = {}, []
    for table, path in {**SOURCE_PATHS, "schedules": "/automation/schedules",
                        "history": "/merchant-months", "past": "/soldouts"}.items():
        filters = {"merchant_id": event.merchant_id}
        if table in SOURCE_PATHS:
            filters["since"] = since
        elif table == "past":
            filters["to"] = (event.cutoff.astimezone(UK).date() - timedelta(days=1)).isoformat()
        try:
            rows = api.pages(path, **filters)["data"]
        except SandboxError:
            missing.append(table)
            continue
        tables[table] = [r for r in rows if isinstance(r, dict) and r.get("merchant_id") == event.merchant_id]
    return tables, missing


def previous_cap_increase(event, past, schedules, max_age_days):
    """Return the latest unambiguous prior increase with no observed schedule overlap."""
    day = event.cutoff.astimezone(UK).date()
    prior = []
    try:
        for row in past:
            if (row.get("merchant_id"), row.get("deal_id")) != (event.merchant_id, event.deal_id):
                continue
            sold = date.fromisoformat(row.get("sold_out_date"))
            if sold < day:
                prior.append((sold, row))
        if not prior:
            return None
        latest = max(sold for sold, _ in prior)
        rows = [row for sold, row in prior if sold == latest]
        if len(rows) != 1 or not string(rows[0].get("sold_out_id")):
            return None
        row = rows[0]
        restocked = date.fromisoformat(row.get("restocked_date"))
        if not (max(latest, day - timedelta(days=max_age_days)) <= restocked < day):
            return None
        cap, raised = (Decimal(str(row.get(key))) for key in ("cap", "new_cap"))
        if not (cap.is_finite() and raised.is_finite() and 0 <= cap < raised):
            return None
        for schedule in schedules:
            if (schedule.get("merchant_id"), schedule.get("deal_id")) != (event.merchant_id, event.deal_id):
                continue
            start = date.fromisoformat(schedule.get("start_date"))
            if start > day:
                continue
            end_value = schedule["end_date"]
            end = date.max if end_value in ("", None) else date.fromisoformat(end_value)
            if end < start or (start <= restocked and end >= latest) or start <= day <= end:
                return None
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    return {"event_id": row["sold_out_id"], "date": restocked.isoformat(), "cap": cap, "new_cap": raised}


def evidence_empty_message(counts, scope, *, unavailable=False):
    if unavailable:
        return "Some source feeds could not be loaded. Evidence may be incomplete."
    if not counts["returned"]:
        return "No source records were returned for this window."
    if not counts[scope]:
        return ("Source records were returned, but none were eligible for this deal."
                if scope == "deal" else
                "Source records were returned, but none were eligible as merchant context.")
    return "Eligible text was found, but no rule recognised a signal."



def assess_event(event, tables, *, max_age_days=90, live=False, incomplete=False):
    cutoff = event.cutoff
    counts = Counter(returned=sum(len(tables.get(source, ())) for source in TEXT_COLUMNS))
    if cutoff is None:
        action, reason = (
            ("no_call", "The sandbox lists this merchant as do not contact.")
            if live and event.do_not_contact is True else
            ("manual_review", "The event time is missing or invalid. Verify it before assessing evidence.")
        )
        return {"action": action, "reason": reason, "deal_evidence": [], "merchant_context": [],
                "schedules": [], "finance": None, "previous_increase": None, "evidence_counts": counts}
    signals = []
    for source, columns in TEXT_COLUMNS.items():
        id_field, date_field, text_field = columns
        records = [{k: string(row.get(k)) for k in
                    ("merchant_id", "deal_id", "rep_id", id_field, date_field, text_field)}
                   for row in tables.get(source, ()) if row.get("merchant_id") == event.merchant_id]
        matches, _ = detect(records, source, cutoff, deal_id=event.deal_id,
                            max_age_days=max_age_days, eligible_counts=counts)
        signals.extend(matches)
    deal = [signal for signal in signals if signal["deal_id"]]
    context = [signal for signal in signals if not signal["deal_id"]]
    schedule_fields = ("schedule_id", "merchant_id", "deal_id", "start_date", "end_date")
    schedules = list(active_schedules(
        ({k: string(row.get(k)) for k in schedule_fields} for row in tables.get("schedules", ())
         if "end_date" in row and (row["end_date"] is None or isinstance(row["end_date"], str))),
        event.merchant_id, event.deal_id, cutoff,
    ))
    action, reason = recommend(deal, schedules)
    if live and event.do_not_contact is True:
        action, reason = "no_call", "The sandbox lists this merchant as do not contact."
    elif live and incomplete:
        action, reason = "manual_review", "Some evidence feeds could not be loaded. Confirm the deal context before acting."
    elif live and event.do_not_contact is None and action == "call":
        action, reason = "manual_review", "The merchant contact status is missing. Verify it before calling."
    elif action == "manual_review" and any(not s["stale"] for s in context):
        reason += " Review the merchant context for questions to verify on this deal."

    previous = None
    if (action == "manual_review" and event.do_not_contact is False and not incomplete
            and all(source in tables for source in (*TEXT_COLUMNS, "schedules", "past"))
            and not schedules and not any(not signal["stale"] for signal in deal)):
        previous = previous_cap_increase(event, tables["past"], tables["schedules"], max_age_days)
        if previous:
            action = "call"
            reason = (f"This deal's cap rose from {previous['cap']:f} to {previous['new_cap']:f} "
                      f"on {previous['date']} ({previous['event_id']}). Ask whether the merchant "
                      "can and wants to add capacity now.")

    baseline = financial = None
    if action == "call":
        history = (
            {k: str(r.get(k, "")) for k in ("month", "gp_gbp", "vouchers_sold")}
            for r in tables.get("history", ()) if r.get("merchant_id") == event.merchant_id
        )
        baseline = merchant_history(history, cutoff)
        financial = forecast(baseline["net_gp_per_voucher_gbp"])
    return {"action": action, "reason": reason, "deal_evidence": deal, "merchant_context": context,
            "schedules": schedules, "finance": financial, "baseline": baseline,
            "previous_increase": previous, "evidence_counts": counts}
