"""Conservative pattern matching: preserve raw text and keep advice separate."""
import re
from collections import Counter
from datetime import timedelta

from .data import TEXT_COLUMNS, active_schedules, deduplicate, timestamp
from .finance import forecast, merchant_history

CAPACITY = {
    "capacity_refusal": r"\b(?:at (?:full )?capacity|fully booked|cap stays|no more bookings|can't (?:take|handle|cope with) (?:any )?more|(?:won't|will not|don't want to|do not want to) (?:restock|raise the cap|top up))\b",
    "restock_willingness": r"\b(?:yes[ ,]+(?:please )?top[ -]up|(?:happy|willing) to (?:restock|top[ -]up|(?:raise|increase|push up) the cap)|please (?:restock|top[ -]up|raise the cap|increase the cap))\b",
    "automation_problem": r"\b(?:link (?:doesn't work|keeps timing out|is broken)|(?:auto(?:matic)?[ -]top[ -]up|auto[ -]replenish) (?:failed|isn't working|didn't run)|never set up a password)\b",
}
RISK = {
    "operational_issue": r"\b(?:double[ -]booked|short[ -]staffed|booking system (?:down|broken)|cancelled on the day)\b",
    "customer_refund_risk": r"\b(?:money back|(?:asked|requested) (?:for )?a refund|duplicate (?:order|charge)|couldn't (?:book|get an appointment)|no appointments available)\b",
}
ACTIONS = {
    "capacity_refusal": "no_call",
    "restock_willingness": "call",
    "automation_problem": "check_automation",
    "operational_issue": "ops_followup",
    "customer_refund_risk": "ops_followup",
}
PUNCTUATION = str.maketrans({"’": "'", "‘": "'", "–": "-", "—": "-"})


def matching_text(raw):
    # Repair common Windows-1252 mojibake for matching only. Preserve stored quotes.
    raw = re.sub(r"â[\u0080-\u00ff\u0152-\u2122]{2}", repair_punctuation, raw)
    return raw.translate(PUNCTUATION)


def repair_punctuation(match):
    for encoding in ("cp1252", "latin1"):
        try:
            return match[0].encode(encoding).decode("utf-8")
        except UnicodeError:
            pass
    return match[0]


def detect(records, source, as_of, *, deal_id="", max_age_days=90, eligible_counts=None):
    """Input records must already be scoped to one merchant."""
    id_field, date_field, text_field = TEXT_COLUMNS[source]
    patterns = (CAPACITY | RISK) if source in ("rep_notes", "deal_alert_replies") else RISK
    signals, skipped = [], Counter()
    visible = []
    for row in records:
        if row.get("deal_id") and row["deal_id"] != deal_id:
            continue
        try:
            seen = timestamp(row.get(date_field), source=True)
        except ValueError:
            skipped["invalid_timestamp"] += 1
            continue
        if seen <= as_of:
            visible.append(row)
    unique = deduplicate(visible, id_field)
    id_counts = Counter(record_id for _, ids in unique for record_id in ids)
    for row, ids in unique:
        raw = row.get(text_field) or ""
        row_deal = row.get("deal_id") or ""
        if not row.get("merchant_id") or not ids or any(id_counts[i] != 1 for i in ids):
            skipped["missing_or_ambiguous_identity"] += 1
            continue
        seen = timestamp(row[date_field], source=True)
        if not raw.strip():
            skipped["blank_text"] += 1
            continue
        stale = as_of - seen > timedelta(days=max_age_days)
        if eligible_counts is not None and not stale:
            eligible_counts["deal" if row_deal else "merchant"] += 1
        normalized = matching_text(raw)
        categories = [label for label, pattern in patterns.items()
                      if re.search(pattern, normalized, re.IGNORECASE)]
        if "restock_willingness" in categories and re.search(
            r"\b(?:not|never|isn't|aren't|wasn't|weren't)\b", normalized, re.IGNORECASE
        ):
            categories.remove("restock_willingness")  # A negation may qualify the match.
        truncated = source == "rep_notes" and len(raw) >= 500
        malformed = "\ufffd" in raw or any("\udc80" <= ch <= "\udcff" for ch in raw)
        for category in categories:
            signals.append({
                "category": category,
                "source": source,
                "source_ids": ids,
                "source_timestamp": row[date_field],
                "merchant_id": row["merchant_id"],
                "deal_id": row_deal or None,
                "rep_id": row.get("rep_id") or None,
                "quote": raw,
                "stale": stale,
                "limitations": [label for condition, label in (
                    (not row_deal, "merchant_scope_only"),
                    (truncated, "possibly_truncated"),
                    (malformed, "invalid_encoding"),
                    (source == "rep_notes" and not row.get("rep_id"), "missing_rep_id"),
                ) if condition],
            })
    return signals, dict(skipped)


def quote_is_grounded(quote, source_ids, eligible_texts):
    """Validate exact substrings against every cited ID in one eligible source."""
    return (isinstance(quote, str) and bool(quote.strip())
            and isinstance(source_ids, (list, tuple)) and bool(source_ids)
            and all(isinstance(i, str) and i in eligible_texts
                    and quote in eligible_texts[i] for i in source_ids))


def recommend(signals, schedules=()):
    fresh = [s for s in signals if not s["stale"]]
    eligible = [s for s in fresh if s["deal_id"] and not s["limitations"]]
    actions = {ACTIONS[s["category"]] for s in fresh}
    deal_actions = {ACTIONS[s["category"]] for s in eligible}
    if len(actions) > 1:
        return "manual_review", "Recent signals suggest different actions; ask the rep to resolve them."
    if schedules and actions == {"no_call"}:
        return "manual_review", "Capacity refusal and a live schedule need rep verification."
    if deal_actions and (not schedules or deal_actions == {"ops_followup"}):
        action = next(iter(deal_actions))
        return action, "Recent deal-linked text suggests this action; confirm the context with the rep."
    if schedules:
        return "check_automation", "A matching schedule covers the decision date; confirm whether it ran."
    if fresh:
        return "manual_review", "Recent evidence needs rep validation for this deal or has source limitations."
    if signals:
        return "manual_review", "Matching text is outside the evidence freshness window."
    return "manual_review", "Insufficient usable evidence for this deal."


def assess(merchant_id, deal_id, as_of, sources, *, schedules=(), history=(), max_age_days=90):
    signals, skipped = [], {}
    for source, records in sources.items():
        matches, counts = detect(
            (row for row in records if row.get("merchant_id") == merchant_id),
            source, as_of, deal_id=deal_id, max_age_days=max_age_days,
        )
        signals.extend(matches)
        skipped[source] = counts
    active = list(active_schedules(schedules, merchant_id, deal_id, as_of))
    action, reason = recommend(signals, active)
    report = {"merchant_id": merchant_id, "deal_id": deal_id or None,
              "as_of": as_of.isoformat(), "method": "deterministic_patterns",
              "max_age_days": max_age_days, "action": action, "reason": reason,
              "evidence": signals, "skipped": skipped, "active_schedules": active}
    if action == "call":
        observed = merchant_history((r for r in history if r["merchant_id"] == merchant_id), as_of)
        report["observed_merchant_history"] = observed
        report["financial_forecast_gbp"] = forecast(observed["net_gp_per_voucher_gbp"])
    return report
