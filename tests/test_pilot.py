import contextlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from soldouts.pilot import OUTCOMES, ROSTER, allocate, load, main, metrics, parse_outcomes, read_assignments, stops

FIXTURES = Path(__file__).parent / "fixtures"
AS_OF = date(2027, 2, 13)


class PilotTest(unittest.TestCase):
    def test_seed_reproducibility_and_one_arm_per_merchant(self):
        rows = load(FIXTURES / "synthetic_eligible_merchants.csv", ROSTER)
        assigned = allocate(rows)
        self.assertEqual(assigned, allocate([*reversed(rows), rows[0]]))
        self.assertEqual(assigned, read_assignments(FIXTURES / "synthetic_assignments.csv"))
        self.assertEqual(list(assigned.values()).count("holdout"), 1)
        self.assertEqual(list(assigned.values()).count("treatment"), 4)
        self.assertNotIn("SYN-F", assigned)

    def test_gp_is_merchant_weighted_and_completeness_is_checked(self):
        raw = load(FIXTURES / "synthetic_outcomes.csv", OUTCOMES)
        assigned = read_assignments(FIXTURES / "synthetic_assignments.csv")
        raw[4]["window_complete"] = "1"  # The reporting date still rules this row out.
        rows = parse_outcomes(raw, assigned, AS_OF)
        treatment = metrics([r for r in rows if r["group"] == "treatment"], 4)
        holdout = metrics([r for r in rows if r["group"] == "holdout"], 1)
        self.assertEqual(treatment["gp"], Decimal("115"))
        self.assertEqual(treatment["gp"] - holdout["gp"], Decimal("15"))
        self.assertEqual((treatment["incomplete"], treatment["gp_missing"]), (1, 1))
        self.assertEqual(treatment["suppressed"], 3)
        raw[2]["experiment_group"] = "holdout"
        with self.assertRaisesRegex(ValueError, "original merchant assignment"):
            parse_outcomes(raw, assigned, AS_OF)

    def test_stop_rules_count_confirmed_cases_in_first_twenty(self):
        start = datetime(2027, 1, 11, tzinfo=timezone.utc)
        rows = [{"event": f"E{i}", "at": start + timedelta(minutes=i), "suppressed": True,
                 "callback": False, "recovery": i in {19, 20}} for i in range(21)]
        self.assertEqual(stops(list(reversed(rows)), AS_OF), ("not reached", 0, "not reached to date", 1, 20, 0))
        rows[0]["recovery"] = rows[20]["callback"] = True
        self.assertEqual(stops(rows, AS_OF), ("REACHED", 1, "REACHED", 2, 20, 0))
        rows[0]["recovery"], rows[0]["callback"] = None, None
        rows[20]["callback"] = False
        self.assertEqual(stops(rows, AS_OF)[:3], ("indeterminate", 0, "indeterminate"))

    def test_synthetic_cli_and_empty_export(self):
        assigned = FIXTURES / "synthetic_assignments.csv"
        outcomes = FIXTURES / "synthetic_outcomes.csv"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            main(["evaluate", str(assigned), str(outcomes), "--as-of", AS_OF.isoformat(),
                  "--start", "2027-01-11", "--synthetic"])
        self.assertIn("SYNTHETIC EXAMPLE", output.getvalue())
        self.assertIn("2027-02-13", output.getvalue())
        self.assertIn("holdout): £15.00", output.getvalue())
        self.assertIn("recovery stop: REACHED", output.getvalue())
        with TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()) as output:
            empty = Path(temp) / "empty.csv"
            empty.write_text(",".join(OUTCOMES) + "\n")
            main(["evaluate", str(assigned), str(empty)])
        self.assertIn("No pilot outcomes supplied", output.getvalue())
        self.assertIn("holdout): unavailable", output.getvalue())
