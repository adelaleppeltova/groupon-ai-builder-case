import unittest
from dataclasses import replace

from soldouts.data import TEXT_COLUMNS
from soldouts.rep import assess_event, evidence_empty_message

from test_phase2 import EVENT, note

PRIOR = {"merchant_id": "M1", "deal_id": "D1", "sold_out_id": "S0", "sold_out_date": "2026-07-21",
         "restocked_date": "2026-07-22", "cap": 10, "new_cap": 15}
TABLES = {**{source: [] for source in TEXT_COLUMNS}, "past": [PRIOR], "schedules": []}
SCHEDULE = {"merchant_id": "M1", "deal_id": "D1", "schedule_id": "A1",
            "start_date": "2026-07-21", "end_date": "2026-07-22"}


class CapPolicyTest(unittest.TestCase):
    def test_recent_increase_with_merchant_context_and_no_finance(self):
        report = assess_event(EVENT, {**TABLES, "rep_notes": [note("N1", "At capacity.")]}, live=True)
        self.assertEqual(report["action"], "call")
        self.assertEqual(report["previous_increase"]["event_id"], "S0")
        self.assertEqual(len(report["merchant_context"]), 1)
        self.assertEqual(report["finance"]["status"], "insufficient_data")

    def test_ineligible_or_ambiguous_prior_history(self):
        for past in (
            [dict(PRIOR, merchant_id="other")], [dict(PRIOR, deal_id="other")],
            [dict(PRIOR, new_cap=10)], [dict(PRIOR, new_cap=None)],
            [dict(PRIOR, sold_out_date="2026-07-20", restocked_date="2026-07-21")],
            [dict(PRIOR, restocked_date="2026-10-20")], [dict(PRIOR, restocked_date="2026-10-21")],
            [dict(PRIOR, restocked_date="2026-07-20")],
            [PRIOR, dict(PRIOR, sold_out_id="duplicate")],
            [PRIOR, dict(PRIOR, sold_out_id="newer", sold_out_date="2026-10-10", new_cap=None)],
        ):
            with self.subTest(past=past):
                self.assertEqual(assess_event(EVENT, {**TABLES, "past": past})["action"], "manual_review")

    def test_future_events_cannot_displace_known_increase(self):
        tables = {**TABLES, "past": [PRIOR, dict(PRIOR, sold_out_date="2026-10-21", new_cap=None)],
                  "rep_notes": [note("N1", "At capacity.", deal_id="D1", date="2026-10-21")],
                  "schedules": [dict(SCHEDULE, start_date="2026-10-21", end_date=None)]}
        self.assertEqual(assess_event(EVENT, tables)["action"], "call")

    def test_schedule_and_current_deal_evidence_take_priority(self):
        for tables, expected in (
            ({"schedules": [SCHEDULE]}, "manual_review"),
            ({"schedules": [dict(SCHEDULE, end_date=None)]}, "check_automation"),
            ({"schedules": [dict(SCHEDULE, end_date="invalid")]}, "manual_review"),
            ({"rep_notes": [note("N1", "At capacity.", deal_id="D1")]}, "no_call"),
            ({"rep_notes": [note("N1", "Double booked.", deal_id="D1")]}, "ops_followup"),
            ({"rep_notes": [note("N1", "At capacity.", deal_id="D1"),
                            note("N2", "Please raise the cap.", deal_id="D1")]}, "manual_review"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(assess_event(EVENT, {**TABLES, **tables}, live=True)["action"], expected)

    def test_contact_and_feed_guards(self):
        for contact, expected in ((True, "no_call"), (None, "manual_review")):
            self.assertEqual(assess_event(replace(EVENT, do_not_contact=contact), TABLES, live=True)["action"],
                             expected)
        for source in (*TEXT_COLUMNS, "schedules", "past"):
            tables = {k: v for k, v in TABLES.items() if k != source}
            self.assertEqual(assess_event(EVENT, tables, live=True)["action"], "manual_review")

    def test_evidence_empty_states_and_scope(self):
        for tables, scope, expected in (
            ({}, "deal", "No source records were returned"),
            ({"rep_notes": [note("N1", "Unrecognised", deal_id="other")]}, "deal", "none were eligible"),
            ({"rep_notes": [note("N1", "Unrecognised", deal_id="D1")]}, "deal", "no rule recognised"),
            ({"rep_notes": [note("N1", "Unrecognised")]}, "merchant", "no rule recognised"),
            ({"rep_notes": [note("N1", "Unrecognised")]}, "deal", "none were eligible"),
        ):
            counts = assess_event(EVENT, tables)["evidence_counts"]
            self.assertIn(expected, evidence_empty_message(counts, scope))
            self.assertIn("could not be loaded", evidence_empty_message(counts, scope, unavailable=True))
