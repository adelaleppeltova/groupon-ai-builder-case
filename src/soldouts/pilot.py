"""Offline assignment and measurement for the proposed merchant pilot."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import csv
import random

from .data import UK, read_csv

GROUPS = ("treatment", "holdout")
ROSTER = ("merchant_id", "rep_confirmed")
ASSIGNMENT = ("merchant_id", "experiment_group")
OUTCOMES = (*ASSIGNMENT, "sold_out_id", "eligibility_date", "sold_out_date", "window_complete", "gp_30d_gbp",
            "suppressed", "suppressed_at", "rep_minutes", "refunds_30d_gbp", "recovery_within_7d",
            "missed_promised_callback")


def load(path, fields):
    with open(path, encoding="utf-8-sig", newline="") as file:
        header = next(csv.reader(file), [])
    if missing := set(fields) - set(header):
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    return read_csv(path)


def boolean(value):
    if value not in ("", "0", "1"):
        raise ValueError("Boolean fields must be 0, 1 or blank")
    return {"0": False, "1": True, "": None}[value]


def number(value, *, nonnegative=False):
    if not value:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid numeric value") from None
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise ValueError("Invalid numeric value")
    return parsed


def allocate(rows, seed=42, holdout_fraction=Decimal("0.20")):
    if not holdout_fraction.is_finite() or not 0 < holdout_fraction < 1:
        raise ValueError("Holdout fraction must be between 0 and 1")
    confirmed = {}
    for row in rows:
        merchant = row["merchant_id"].strip()
        result = boolean(row["rep_confirmed"])
        if not merchant or (merchant in confirmed and confirmed[merchant] != result):
            raise ValueError("Missing merchant ID or inconsistent confirmation")
        confirmed[merchant] = result
    eligible = sorted(m for m, result in confirmed.items() if result is True)
    if len(eligible) < 2:
        raise ValueError("At least two rep-confirmed merchants are needed")
    size = min(len(eligible) - 1, max(1, round(len(eligible) * holdout_fraction)))
    holdout = set(random.Random(seed).sample(eligible, size))
    return {m: "holdout" if m in holdout else "treatment" for m in eligible}


def read_assignments(path):
    assigned = {}
    for row in load(path, ASSIGNMENT):
        merchant, group = row["merchant_id"].strip(), row["experiment_group"]
        if not merchant or merchant in assigned or group not in GROUPS:
            raise ValueError("Assignment rows need unique merchant IDs and valid groups")
        assigned[merchant] = group
    return assigned


def parse_outcomes(rows, assigned, as_of, start=None):
    parsed, seen = [], set()
    for row in rows:
        merchant, event = row["merchant_id"].strip(), row["sold_out_id"].strip()
        if not event or event in seen or assigned.get(merchant) != row["experiment_group"] or merchant not in assigned:
            raise ValueError("Outcomes require unique sold-out IDs and the original merchant assignment")
        seen.add(event)
        sold = date.fromisoformat(row["sold_out_date"])
        eligible = date.fromisoformat(row["eligibility_date"])
        if eligible > sold or sold > as_of or (start and sold < start):
            raise ValueError(f"Sold-out {event} is outside the reporting period")
        suppressed = boolean(row["suppressed"])
        at = datetime.fromisoformat(row["suppressed_at"]) if row["suppressed_at"] else None
        if at and at.utcoffset() is None:
            raise ValueError("Suppression timestamps require a UTC offset")
        if at and (suppressed is not True or not sold <= at.astimezone(UK).date() <= as_of):
            raise ValueError(f"Invalid suppression timing for {event}")
        recovery = boolean(row["recovery_within_7d"])
        callback = boolean(row["missed_promised_callback"])
        if (recovery is True or callback is True) and suppressed is not True:
            raise ValueError(f"Attributed incidents require confirmed suppression for {event}")
        complete = boolean(row["window_complete"]) is True and sold + timedelta(days=30) <= as_of
        parsed.append({"merchant": merchant, "group": assigned[merchant], "event": event,
                       "suppressed": suppressed, "at": at, "recovery": recovery, "callback": callback,
                       "complete": complete, "gp": number(row["gp_30d_gbp"]),
                       "refunds": number(row["refunds_30d_gbp"], nonnegative=True),
                       "minutes": number(row["rep_minutes"], nonnegative=True)})
    return parsed


def mean(values):
    return sum(values) / len(values) if values else None


def metrics(rows, size):
    by_merchant = defaultdict(list)
    for row in rows:
        if row["complete"] and row["gp"] is not None:
            by_merchant[row["merchant"]].append(row["gp"])
    refunds = [row["refunds"] for row in rows if row["complete"] and row["refunds"] is not None]
    minutes = [row["minutes"] for row in rows if row["minutes"] is not None]
    return dict(assigned=size, observed=len({r["merchant"] for r in rows}), sold_outs=len(rows),
                suppressed=sum(r["suppressed"] is True for r in rows),
                suppression_unknown=sum(r["suppressed"] is None for r in rows),
                gp=mean([mean(values) for values in by_merchant.values()]),
                gp_merchants=len(by_merchant), gp_events=sum(map(len, by_merchant.values())),
                incomplete=sum(not r["complete"] for r in rows),
                gp_missing=sum(r["complete"] and r["gp"] is None for r in rows),
                refunds=sum(refunds) if refunds else None, refund_events=len(refunds),
                minutes=sum(minutes) if minutes else None, minute_events=len(minutes),
                recovery=sum(r["recovery"] is True for r in rows),
                recovery_reported=sum(r["recovery"] is not None for r in rows if r["suppressed"] is True))


def stops(rows, as_of):
    suppressed = [r for r in rows if r["suppressed"] is True]
    unknown = any(r["suppressed"] is None for r in rows)
    misses = sum(r["callback"] is True for r in suppressed)
    callback = ("REACHED" if misses else "indeterminate" if not suppressed or unknown
                or any(r["callback"] is None for r in suppressed) else "not reached")
    ordered = not unknown and all(r["at"] is not None for r in suppressed)
    chronological = sorted(suppressed, key=lambda r: r["at"]) if ordered else []
    first = chronological[:20]
    boundary_tied = len(chronological) > 20 and chronological[19]["at"] == chronological[20]["at"]
    if boundary_tied:
        first = [r for r in first if r["at"] < chronological[19]["at"]]
    cases = sum(r["recovery"] is True for r in first)
    pending = sum(r["recovery"] is None or (r["recovery"] is False and
                  r["at"].astimezone(UK).date() + timedelta(days=7) >= as_of) for r in first)
    recovery = ("REACHED" if cases >= 2 else "indeterminate" if not ordered or boundary_tied or not first or pending
                else "not reached to date")
    return callback, misses, recovery, cases, len(first), pending


def show_number(value, prefix=""):
    return "unavailable" if value is None else f"{prefix}{value:,.2f}"


def report(rows, assigned, args):
    print("SYNTHETIC EXAMPLE — no pilot has started" if args.synthetic else
          "No pilot outcomes supplied" if not rows else "Pilot export — confirm randomisation and data provenance")
    print("Historical context: Priya's reported £11.08 association is observational and excluded from the estimate.")
    if args.start:
        end = args.start + timedelta(days=30)
        print(f"First cohort: observation window ends {end}; earliest readout "
              f"{end + timedelta(days=args.processing_days)} with {args.processing_days} processing day(s).")
    else:
        print("First readout date unavailable. Supply a proposed start date to model timing.")
    print("Later cohorts need their own 30-day windows. Refunds can arrive after the window.")
    print("Illustrative time value per 100 eligible tasks: £50–£150 if 10–30 tasks are safely avoided "
          "at 12 minutes each and £25/hour. Net GP scenario unavailable without assumptions about lost GP and recovery.")
    results = {}
    for group in GROUPS:
        subset = [r for r in rows if r["group"] == group]
        result = metrics(subset, sum(g == group for g in assigned.values()))
        results[group] = result
        m = result
        print(f"\n{group}: {m['assigned']} assigned merchants, {m['observed']} with events, {m['sold_outs']} sold-outs")
        print(f"  Actually suppressed: {m['suppressed']} ({m['suppression_unknown']} unknown)")
        estimate = None
        if args.minutes_per_task is not None and m["sold_outs"] > m["suppression_unknown"]:
            estimate = m["suppressed"] * args.minutes_per_task
        print(f"  Rep minutes spent: {show_number(m['minutes'])} ({m['minute_events']} reported); "
              f"estimated gross minutes avoided: {show_number(estimate)}")
        print(f"  Mean 30-day GP per sold-out, equal merchant weights: {show_number(m['gp'], '£')} "
              f"({m['gp_merchants']} merchants, {m['gp_events']} events)")
        print(f"  Incomplete/unknown windows: {m['incomplete']}; complete windows missing GP: {m['gp_missing']}")
        print(f"  30-day refunds: {show_number(m['refunds'], '£')} ({m['refund_events']} complete with refunds reported); "
              f"seven-day recovery cases: {m['recovery']} ({m['recovery_reported']} suppression records reported)")
    t, c = (results[group] for group in GROUPS)
    difference = t["gp"] - c["gp"] if t["gp"] is not None and c["gp"] is not None else None
    print(f"\nAssigned-group GP difference (treatment minus holdout): {show_number(difference, '£')}")
    if min(t["gp_merchants"], c["gp_merchants"]) < 2:
        print("Too few merchants with GP to assess between-merchant variation.")
    if rows and any(m["gp_events"] < m["sold_outs"] or m["gp_merchants"] < m["assigned"]
                    for m in results.values()):
        print("GP coverage is incomplete. The difference uses available outcomes and may be biased.")
    print("Uncertainty interval omitted. Merchant assignment makes repeated sold-outs non-independent.")
    if c["suppressed"]:
        print("Warning: holdout suppression recorded. Check for contamination and keep original assignments.")
    callback, misses, recovery, count, first, pending = stops(rows, args.as_of)
    print(f"Proposed callback stop: {callback} ({misses} attributed missed callback(s) reported).")
    print(f"Proposed recovery stop: {recovery} ({count} case(s) among {first} ordered suppressions "
          f"of the first 20; {pending} pending).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    assignment = commands.add_parser("assign")
    assignment.add_argument("eligible_csv")
    assignment.add_argument("output_csv")
    assignment.add_argument("--seed", type=int, default=42)
    assignment.add_argument("--holdout", type=Decimal, default=Decimal("0.20"))
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("assignments_csv")
    evaluation.add_argument("outcomes_csv")
    evaluation.add_argument("--start", type=date.fromisoformat)
    evaluation.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(UK).date())
    evaluation.add_argument("--processing-days", type=int, default=3)
    evaluation.add_argument("--minutes-per-task", type=Decimal)
    evaluation.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "assign":
            assigned = allocate(load(args.eligible_csv, ROSTER), args.seed, args.holdout)
            with open(args.output_csv, "x", newline="", encoding="utf-8") as file:
                writer = csv.writer(file, lineterminator="\n")
                writer.writerow(ASSIGNMENT)
                writer.writerows(assigned.items())
        else:
            if args.processing_days < 0:
                raise ValueError("Processing days must be nonnegative")
            if args.minutes_per_task is not None:
                number(str(args.minutes_per_task), nonnegative=True)
            assigned = read_assignments(args.assignments_csv)
            rows = parse_outcomes(load(args.outcomes_csv, OUTCOMES), assigned, args.as_of, args.start)
            report(rows, assigned, args)
    except (OSError, ValueError, InvalidOperation, csv.Error) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
