"""Sold-out suppression list: deals or merchants whose sold-outs should not become rep tasks.

Agreed shape (Ops, October): one row per deal or merchant, read at start of run, only
when `suppress_soldout_tasks` is on in config. The pilot planner accepts only deal entries.
"""
import csv
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ["deal_id", "merchant_id", "reason", "source", "added_on"]


def load_suppressions(path=None):
    """Rows of the suppression file as dicts. A missing file means nothing is suppressed.

    A row needs a deal_id or a merchant_id (merchant-wide). `reason` and `source`
    are free text for the audit trail, e.g. "auto-replenish weekly" / "merchant_ops".
    """
    path = Path(path) if path else ROOT / "input" / "suppressions.csv"
    if not path.exists():
        return []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("deal_id", "merchant_id") if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name}: missing column(s) {', '.join(missing)}")
        rows = []
        for n, row in enumerate(reader, start=2):
            if not (row["deal_id"] or row["merchant_id"]):
                raise ValueError(f"{path.name} line {n}: needs a deal_id or a merchant_id")
            rows.append(row)
    return rows


# Proposed file vocabulary. Maintainers must verify the confirmation outside routing.
REASON_CODES = frozenset({"capacity_confirmed", "restock_declined", "already_restocked"})


def dated_entries(path, as_of, max_age_days):
    """Return eligible deal entries and diagnostics without exposing rejected text."""
    if type(max_age_days) is not int or max_age_days < 0:
        return {}, [{"status": "invalid_max_age"}]
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file, strict=True)
            if len(reader.fieldnames or ()) != len(COLUMNS) or set(reader.fieldnames) != set(COLUMNS):
                return {}, [{"status": "invalid_header"}]
            entries, diagnostics, duplicates = {}, [], set()
            for row in reader:
                line = reader.line_num
                entry, status = validate_entry(row, as_of, max_age_days)
                if status:
                    diagnostics.append({"line": line, "status": status})
                    continue
                key = entry.pop("merchant_id"), entry.pop("deal_id")
                if key in entries or key in duplicates:
                    entries.pop(key, None)
                    duplicates.add(key)
                    diagnostics.append({"line": line, "status": "ambiguous_entry"})
                    continue
                entries[key] = {**entry, "line": line}
    except FileNotFoundError:
        return {}, [{"status": "missing_file"}]
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError):
        return {}, [{"status": "unreadable_file"}]
    return entries, diagnostics


def validate_entry(row, as_of, max_age_days):
    if None in row or None in row.values():
        return None, "invalid_row"
    entry = {k: v.strip() for k, v in row.items()}
    if entry["merchant_id"] and not entry["deal_id"]:
        return None, "merchant_wide_ignored"
    if not entry["deal_id"] or not entry["merchant_id"]:
        return None, "missing_id"
    if entry["reason"] not in REASON_CODES:
        return None, "invalid_reason_code"
    if not re.fullmatch(r"rep_confirmed:R[0-9]{2}", entry["source"]):
        return None, "invalid_confirmation_source"
    try:
        added = date.fromisoformat(entry["added_on"])
        if added.isoformat() != entry["added_on"]:
            raise ValueError
    except ValueError:
        return None, "invalid_date"
    age = (as_of - added).days
    if age < 0:
        return None, "future_entry"
    if age > max_age_days:
        return None, "stale_entry"
    return entry, None
