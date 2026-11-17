import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from soldouts.data import active_schedules, deduplicate, timestamp
from soldouts.evidence import assess, quote_is_grounded
from soldouts.finance import forecast, merchant_history

CUTOFF = timestamp("2026-09-10T10:00:00")


def note(text, **fields):
    return dict({"note_id": "N1", "merchant_id": "M1", "deal_id": "D1",
                 "rep_id": "R1", "created_at": "2026-09-09 15:30:00", "text": text}, **fields)


def assess_notes(*records, **kwargs):
    return assess("M1", "D1", CUTOFF, {"rep_notes": records}, **kwargs)


class EvidenceTest(unittest.TestCase):
    def test_replay_preserves_ids_and_exact_mojibake(self):
        raw = "We canâ€™t take any more."
        report = assess_notes(note(raw), note(raw, note_id="N2"))
        self.assertEqual(report["action"], "no_call")
        signal, = report["evidence"]
        self.assertEqual(signal["source_ids"], ("N1", "N2"))
        self.assertEqual(signal["quote"], raw)
        self.assertEqual(signal["source_timestamp"], "2026-09-09 15:30:00")

    def test_activity_payload_replay(self):
        records = [{"activity_id": i, "duration_s": "120"} for i in ("A1", "A2")]
        self.assertEqual(deduplicate(records, "activity_id"), [(records[0], ("A1", "A2"))])

    def test_missing_ids_and_truncated_or_malformed_records(self):
        for text, fields, limitation in [
            ("At capacity." + "x" * 500, {}, "possibly_truncated"),
            ("At capacity. \udcff", {}, "invalid_encoding"),
            ("At capacity.", {"rep_id": ""}, "missing_rep_id"),
            ("At capacity.", {"deal_id": ""}, "merchant_scope_only"),
        ]:
            with self.subTest(limitation=limitation):
                report = assess_notes(note(text, **fields))
                self.assertEqual(report["action"], "manual_review")
                self.assertIn(limitation, report["evidence"][0]["limitations"])
        for fields in ({"note_id": ""}, {"merchant_id": ""}, {"deal_id": "D2"}):
            self.assertFalse(assess_notes(note("At capacity.", **fields))["evidence"])

    def test_conflict_and_staleness(self):
        report = assess_notes(note("At capacity."), note("Please raise the cap.", note_id="N2"))
        self.assertEqual(report["action"], "manual_review")
        self.assertIn("different actions", report["reason"])
        old = assess_notes(note("At capacity.", created_at="2026-01-01"))
        self.assertTrue(old["evidence"][0]["stale"])
        self.assertEqual(old["action"], "manual_review")

    def test_future_evidence_cannot_change_decision_including_duplicate_id(self):
        known = note("At capacity.")
        for after in ("2026-09-10", "2026-09-10T09:01:00Z"):
            future = note("Please raise the cap.", created_at=after)
            self.assertEqual(assess_notes(known), assess_notes(known, future))

    def test_no_call_from_customer_text_or_negated_willingness(self):
        review = {"review_id": "V1", "deal_id": "D1", "merchant_id": "M1",
                  "created_at": "2026-09-09", "text": "Please raise the cap. Double booked."}
        report = assess("M1", "D1", CUTOFF, {"reviews": [review]})
        self.assertEqual(report["action"], "ops_followup")
        self.assertEqual(assess_notes(note("Not happy to restock."))["action"], "manual_review")

    def test_exact_quote_requires_eligible_matching_ids(self):
        sources = {"N1": "At capacity — no more.", "N2": "Please raise the cap."}
        self.assertTrue(quote_is_grounded("At capacity", ["N1"], sources))
        for quote, ids in [("No room", ["N1"]), ("", ["N1"]), ("At capacity", ["N1", "N2"]),
                           ("At capacity", ["N9"]), ("At capacity", [])]:
            self.assertFalse(quote_is_grounded(quote, ids, sources))

    def test_schedules_require_both_ids_and_valid_dates(self):
        base = {"merchant_id": "M1", "deal_id": "D1", "schedule_id": "S1",
                "start_date": "2026-09-10", "end_date": "2026-09-10"}
        records = [base, dict(base, merchant_id="M2"), dict(base, deal_id="D2"),
                   dict(base, start_date="2026-09-11", end_date=""),
                   dict(base, end_date="2026-09-09"), dict(base, start_date="invalid")]
        self.assertEqual(list(active_schedules(records, "M1", "D1", CUTOFF)),
                         [{"schedule_id": "S1", "start_date": "2026-09-10"}])
        self.assertEqual(assess_notes(schedules=records)["action"], "check_automation")


class FinanceTest(unittest.TestCase):
    def test_history_and_net_value_ignore_current_month_and_refund_totals(self):
        history = [{"merchant_id": "M1", "month": month, "gp_gbp": gp,
                    "vouchers_sold": "10", "refunds_gbp": "999"}
                   for month, gp in [("2026-08", "100"), ("2026-09", "9999"), ("2026-01", "9999")]]
        report = assess_notes(note("Please raise the cap."), history=history)
        self.assertEqual(report["action"], "call")
        self.assertEqual(report["observed_merchant_history"]["months"], ["2026-08"])
        results = report["financial_forecast_gbp"]["scenarios"]
        self.assertEqual([results[case]["expected_net_value_gbp"] for case in ("low", "base", "high")],
                         [Decimal("2.50"), Decimal("43.00"), Decimal("107.50")])

    def test_missing_or_invalid_history_is_not_zero_value(self):
        history = merchant_history([{"month": "2026-08", "gp_gbp": "NaN", "vouchers_sold": "10"}],
                                   CUTOFF)
        self.assertEqual(forecast(history["net_gp_per_voucher_gbp"])["status"], "insufficient_data")


class CliTest(unittest.TestCase):
    def test_unseen_reply_csv_is_labelled_offline_and_kept_merchant_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "unseen.csv"
            path.write_text("reply_id,merchant_id,received_at,text\nNEW1,NEW2,2026-09-01,Yes top up.\n")
            result = subprocess.run(
                [sys.executable, "-m", "soldouts.cli", "detect", str(path),
                 "--source", "deal_alert_replies", "--as-of", "2026-09-10",
                 "--merchant-id", "NEW2", "--deal-id", "D999"],
                check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertEqual(report["method"], "deterministic_patterns")
        self.assertEqual(report["action"], "manual_review")
        self.assertEqual(report["evidence"][0]["source_ids"], ["NEW1"])


if __name__ == "__main__":
    unittest.main()
