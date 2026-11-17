"""Offline detector and historical deal replay."""
import argparse
import csv
import json
from decimal import Decimal
from zipfile import BadZipFile

from .data import TEXT_COLUMNS, pack_reader, read_csv, timestamp
from .evidence import assess


def replay(path, sold_out_id, as_of=None, *, max_age_days=90):
    with pack_reader(path) as read:
        events = [r for r in read("sold_out_deals") if r["sold_out_id"] == sold_out_id]
        if len(events) != 1:
            raise ValueError("Expected exactly one matching sold-out event")
        event = events[0]
        decision = timestamp(event["sold_out_date"])
        cutoff = timestamp(as_of) if as_of else decision
        if cutoff < decision:
            raise ValueError("Decision cutoff precedes the sold-out event")
        report = assess(event["merchant_id"], event["deal_id"], cutoff,
                        {source: read(source) for source in TEXT_COLUMNS},
                        schedules=read("auto_replenish_schedules"), history=read("merchant_months"),
                        max_age_days=max_age_days)
    return dict(report, sold_out_id=sold_out_id, sold_out_date=event["sold_out_date"],
                mode="historical_replay")


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Unsupported value: {type(value).__name__}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    offline = commands.add_parser("detect", help="Read an unseen CSV with one supported source schema")
    offline.add_argument("csv")
    offline.add_argument("--source", choices=TEXT_COLUMNS, required=True)
    offline.add_argument("--merchant-id", required=True)
    offline.add_argument("--deal-id", default="")
    historical = commands.add_parser("replay", help="Assess a historical sold-out event in the data pack")
    historical.add_argument("pack")
    historical.add_argument("--sold-out-id", required=True)
    for command in (offline, historical):
        command.add_argument("--as-of", required=command is offline, help="ISO timestamp or date")
        command.add_argument("--max-age-days", type=int, default=90)
    args = parser.parse_args(argv)
    if args.max_age_days < 0:
        parser.error("--max-age-days must be nonnegative")
    try:
        if args.command == "detect":
            records = read_csv(args.csv)
            required = {"merchant_id", *TEXT_COLUMNS[args.source]}
            if records and not required <= records[0].keys():
                raise ValueError(f"Expected columns: {', '.join(sorted(required))}")
            report = assess(args.merchant_id, args.deal_id, timestamp(args.as_of),
                            {args.source: records}, max_age_days=args.max_age_days)
        else:
            report = replay(args.pack, args.sold_out_id, args.as_of, max_age_days=args.max_age_days)
    except (OSError, ValueError, KeyError, csv.Error, BadZipFile) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, default=json_value))


if __name__ == "__main__":
    main()
