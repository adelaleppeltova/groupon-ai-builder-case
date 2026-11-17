"""Input adapters for the exercise pack and standalone CSVs."""
import csv
import io
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

UK = ZoneInfo("Europe/London")
TEXT_COLUMNS = {
    "rep_notes": ("note_id", "created_at", "text"),
    "deal_alert_replies": ("reply_id", "received_at", "text"),
    "reviews": ("review_id", "created_at", "text"),
    "refunds": ("refund_id", "created_at", "reason_text"),
}


def timestamp(value, *, source=False):
    """Treat dates without time as end of day for evidence, start for decisions."""
    value = (value or "").strip()
    if len(value) == 10:
        moment = datetime.combine(date.fromisoformat(value), time.max if source else time.min)
    else:
        moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=moment.tzinfo or UK).astimezone(timezone.utc)


@contextmanager
def pack_reader(path):
    path = Path(path)
    if path.is_dir():
        root = path / "data" if (path / "data").is_dir() else path

        def read(name):
            return read_csv(root / f"{name}.csv")

        yield read
    else:
        with ZipFile(path) as archive:
            def read(name):
                with io.TextIOWrapper(archive.open(f"data/{name}.csv"), **CSV_OPTIONS) as file:
                    return rows(file)

            yield read


CSV_OPTIONS = dict(encoding="utf-8-sig", errors="surrogateescape", newline="")


def rows(file):
    reader = csv.DictReader(file, strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("Expected a CSV with unique column names")
    result = list(reader)
    if any(None in row or None in row.values() for row in result):
        raise ValueError("CSV row does not match the header")
    return result


def read_csv(path):
    with Path(path).open(**CSV_OPTIONS) as file:
        return rows(file)


def deduplicate(records, id_field):
    """Group exact replay payloads; retain every supplied ID."""
    payloads = defaultdict(set)
    originals = {}
    for row in records:
        key = tuple(sorted((k, v) for k, v in row.items() if k != id_field))
        originals.setdefault(key, row)
        if row.get(id_field):
            payloads[key].add(row[id_field])
    return [(row, tuple(sorted(payloads[key]))) for key, row in originals.items()]


def active_schedules(rows, merchant_id, deal_id, as_of):
    """Match both IDs. Interval is inclusive; an empty end date is open ended."""
    local_day = as_of.astimezone(UK).date()
    for row in rows:
        if not merchant_id or not deal_id or (row["merchant_id"], row["deal_id"]) != (merchant_id, deal_id):
            continue
        try:
            start = date.fromisoformat(row["start_date"])
            end = date.fromisoformat(row["end_date"]) if row["end_date"] else date.max
        except ValueError:
            continue
        if row["schedule_id"] and start <= local_day <= end:
            # Don't expose a future end date or other mutable schedule fields.
            yield {"schedule_id": row["schedule_id"], "start_date": row["start_date"]}
