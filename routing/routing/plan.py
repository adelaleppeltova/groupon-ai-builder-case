"""Build the morning task list for one rep."""
import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

if __package__:
    from .suppressions import dated_entries
else:
    from suppressions import dated_entries

ROOT = Path(__file__).resolve().parent.parent

# Used when a key is missing from config/routing.json.
DEFAULTS = {
    "suppressed_deal_ids": [],
    "suppress_soldout_tasks": False,
    "suppression_file": "input/suppressions.csv",
    "suppression_max_age_days": 7,
}


def load_overrides():
    return json.loads((ROOT / "config" / "env_overrides.json").read_text())


def load_config():
    config = dict(DEFAULTS)
    config.update(json.loads((ROOT / "config" / "routing.json").read_text()))
    config.update(load_overrides().get("routing", {}))
    return config


def load_priors():
    priors = json.loads((ROOT / "priors.json").read_text())
    priors.update(load_overrides().get("priors", {}))
    return priors


def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def expected_gp(row, priors):
    p = priors["restock"]
    return (p["called"] - p["left_alone"]) * float(row["value_30d_gbp"])


def is_active(row, config):
    if row["do_not_contact"] == "1":
        return False
    if row["deal_id"] in set(config.get("suppressed_deal_ids", [])):
        return False
    return True


def build_plan(rows, priors=None, config=None, *, as_of=None, audit=None):
    """Return (today's list, carried over). Each item is a dict with task and rank details."""
    priors = priors or load_priors()
    config = config or load_config()
    active = [r for r in rows if is_active(r, config)]
    promised = [r for r in active if r["callback_promised"] == "1"]
    rest = [r for r in active
            if r["callback_promised"] != "1" and int(r["failed_attempts"]) < config["max_failed_attempts"]]
    if config.get("suppress_soldout_tasks") is True:
        rest = apply_suppressions(rest, promised, config, as_of, audit)
    rest.sort(key=lambda r: expected_gp(r, priors), reverse=True)

    today, carried, minutes = [], [], 0
    for r in promised + rest:
        item = {
            "task_id": r["task_id"], "merchant_id": r["merchant_id"], "merchant_name": r["merchant_name"],
            "deal_id": r["deal_id"], "reason": "callback" if r in promised else "restock",
            "expected_gp": round(expected_gp(r, priors), 2),
        }
        if minutes + config["minutes_per_task"] <= config["minutes_per_day"]:
            today.append(item)
            minutes += config["minutes_per_task"]
        else:
            carried.append(item)
    return today, carried


def apply_suppressions(rest, promised, config, as_of, audit):
    """Apply to otherwise eligible restock tasks, before the existing sort."""
    if as_of is None:
        as_of = datetime.now(ZoneInfo("Europe/London")).date()
    run = {"run_id": uuid4().hex, "as_of": as_of.isoformat()}

    def emit(event, **fields):
        record = {"event": event, **run, **fields}
        if audit is None:
            print(json.dumps(record), file=sys.stderr)
        else:
            audit(record)

    filename = config.get("suppression_file", DEFAULTS["suppression_file"])
    path = ROOT / filename if isinstance(filename, str) and filename.strip() else None
    entries, diagnostics = dated_entries(
        path, as_of, config.get("suppression_max_age_days", DEFAULTS["suppression_max_age_days"])
    )
    for diagnostic in diagnostics:
        emit("suppression_entry_ignored", **diagnostic)

    kept, removed = [], []
    protected = 0
    for row in [*promised, *rest]:
        entry = entries.get((row["merchant_id"], row["deal_id"]))
        if entry is None:
            if row["callback_promised"] != "1":
                kept.append(row)
            continue
        if row["callback_promised"] == "0":
            status = "suppressed"
            removed.append(row)
        elif row["callback_promised"] == "1":
            status = "promised_callback_protected"
            protected += 1
        else:
            status = "unknown_callback_status"
            kept.append(row)
        emit("suppression_decision", status=status, task_id=row["task_id"],
             deal_id=row["deal_id"], merchant_id=row["merchant_id"], entry=entry)

    emit("suppression_summary", file=path.name if path else None, suppressed_tasks=len(removed),
         promised_callbacks_protected=protected,
         promised_callbacks_removed=sum(r["callback_promised"] == "1" for r in removed),
         diagnostic_count=len(diagnostics))
    return kept


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Evaluation date (YYYY-MM-DD)")
    args = parser.parse_args()
    today, carried = build_plan(load_rows(args.input), as_of=args.as_of)
    for item in today:
        print(item["task_id"], item["merchant_name"], item["reason"], item["expected_gp"])
    print(f"carried over: {len(carried)}")
