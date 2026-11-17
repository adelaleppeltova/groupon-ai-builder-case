import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from routing.plan import build_plan, load_config

DAY = date(2026, 11, 9)
ENTRY = ("D1", "M1", "capacity_confirmed", "rep_confirmed:R01", "2026-11-02")
PRIORS = {"restock": {"called": .5, "left_alone": .25}}
HEADER = ("deal_id", "merchant_id", "reason", "source", "added_on")


def task(task_id, deal, merchant="M1", callback="0", value="8", attempts="0"):
    return {"task_id": task_id, "deal_id": deal, "merchant_id": merchant, "merchant_name": "Private name",
            "do_not_contact": "0", "callback_promised": callback, "value_30d_gbp": value,
            "failed_attempts": attempts}


ROWS = [
    task("S1", "D1", value="16"), task("C1", "D1", callback="1"),
    task("O1", "D2", value="12"), task("M2", "D1", merchant="M2"),
    task("L1", "LEGACY", callback="1"), task("P1", "D1", attempts="3"),
]


def ids(plan):
    return [[r["task_id"] for r in bucket] for bucket in plan]


class PilotTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.file = self.root / "suppressions.csv"
        self.config = {**load_config(), "minutes_per_day": 24, "suppressed_deal_ids": ["LEGACY"],
                       "suppression_file": str(self.file)}
        self.events = []

    def write(self, *entries):
        with self.file.open("w", newline="") as file:
            csv.writer(file).writerows((HEADER, *entries))

    def run_plan(self, enabled=True, **config):
        return build_plan(ROWS, PRIORS, {**self.config, "suppress_soldout_tasks": enabled, **config},
                          as_of=DAY, audit=self.events.append)

    def test_off_preserves_full_baseline_and_disabling_rolls_back(self):
        self.write(ENTRY)
        expected = [
            [{"task_id": "C1", "merchant_id": "M1", "merchant_name": "Private name", "deal_id": "D1",
              "reason": "callback", "expected_gp": 2.0},
             {"task_id": "S1", "merchant_id": "M1", "merchant_name": "Private name", "deal_id": "D1",
              "reason": "restock", "expected_gp": 4.0}],
            [{"task_id": "O1", "merchant_id": "M1", "merchant_name": "Private name", "deal_id": "D2",
              "reason": "restock", "expected_gp": 3.0},
             {"task_id": "M2", "merchant_id": "M2", "merchant_name": "Private name", "deal_id": "D1",
              "reason": "restock", "expected_gp": 2.0}],
        ]
        with patch("routing.plan.dated_entries", side_effect=AssertionError("Flag off must not read the file")):
            self.assertEqual(list(self.run_plan(False)), expected)
        self.assertEqual(self.events, [])
        self.assertEqual(ids(self.run_plan()), [["C1", "O1"], ["M2"]])
        count = len(self.events)
        self.assertEqual(list(self.run_plan(False)), expected)
        self.assertEqual(len(self.events), count)

    def test_enabled_audit_attributes_decisions_to_entry(self):
        self.write(ENTRY)
        self.run_plan()
        protected, removed, summary = self.events
        self.assertEqual({r["run_id"] for r in self.events}, {summary["run_id"]})
        self.assertEqual(protected["status"], "promised_callback_protected")
        self.assertEqual(protected["task_id"], "C1")
        self.assertEqual({k: removed[k] for k in ("event", "status", "task_id", "deal_id",
                                                    "merchant_id", "as_of", "entry")}, {
            "event": "suppression_decision", "status": "suppressed", "task_id": "S1",
            "deal_id": "D1", "merchant_id": "M1", "as_of": "2026-11-09",
            "entry": {"reason": "capacity_confirmed", "source": "rep_confirmed:R01",
                      "added_on": "2026-11-02", "line": 2},
        })
        self.assertEqual(summary["event"], "suppression_summary")
        self.assertEqual(summary["suppressed_tasks"], 1)
        self.assertEqual(summary["promised_callbacks_removed"], 0)
        self.assertEqual(summary["promised_callbacks_protected"], 1)
        self.assertNotIn("Private name", json.dumps(self.events))

    def test_ignored_entries_fail_safe_individually(self):
        self.write(("", "M1", *ENTRY[2:]), ("D1", "", *ENTRY[2:]), ENTRY[:4], (*ENTRY, "extra"),
                   (*ENTRY[:2], "private notes", *ENTRY[3:]), (*ENTRY[:3], "auto", ENTRY[4]),
                   (*ENTRY[:4], "bad"), (*ENTRY[:4], "2026-11-10"), (*ENTRY[:4], "2026-11-01"))
        self.assertEqual(self.run_plan(), self.run_plan(False))
        self.assertEqual([r["status"] for r in self.events if r["event"] == "suppression_entry_ignored"],
                         ["merchant_wide_ignored", "missing_id", "invalid_row", "invalid_row",
                          "invalid_reason_code", "invalid_confirmation_source", "invalid_date",
                          "future_entry", "stale_entry"])
        self.assertNotIn("private notes", json.dumps(self.events))

    def test_missing_malformed_duplicate_and_invalid_policy_fail_safe(self):
        baseline = self.run_plan(False)
        self.assertEqual(self.run_plan(), baseline)
        self.assertEqual(self.events[0]["status"], "missing_file")
        self.file.write_text("deal_id,merchant_id\nD1,M1\n")
        self.assertEqual(self.run_plan(), baseline)
        self.assertEqual(self.events[-2]["status"], "invalid_header")
        self.write(ENTRY)
        with self.file.open("a") as file:
            file.write('"broken')
        self.assertEqual(self.run_plan(), baseline)
        self.assertEqual(self.events[-2]["status"], "unreadable_file")
        self.write(ENTRY, ENTRY)
        self.assertEqual(self.run_plan(), baseline)
        self.assertEqual(self.events[-2]["status"], "ambiguous_entry")
        self.assertEqual(self.run_plan(suppression_max_age_days=-1), baseline)
        self.assertEqual(self.events[-2]["status"], "invalid_max_age")

    def test_inclusive_freshness_and_configurable_boundary(self):
        baseline = self.run_plan(False)
        self.write(ENTRY)
        self.assertNotEqual(self.run_plan(suppression_max_age_days=7), baseline)
        self.assertEqual(self.run_plan(suppression_max_age_days=6), baseline)
        self.write((*ENTRY[:4], DAY.isoformat()))
        self.assertNotEqual(self.run_plan(suppression_max_age_days=0), baseline)

    def test_env_overrides_take_precedence_over_base_config(self):
        self.write(ENTRY)
        directory = self.root / "config"
        directory.mkdir()
        (directory / "routing.json").write_text(json.dumps({
            **self.config, "suppress_soldout_tasks": True, "suppression_max_age_days": 0,
        }))
        overrides = directory / "env_overrides.json"
        with patch("routing.plan.ROOT", self.root):
            overrides.write_text('{"routing":{"suppress_soldout_tasks":false,"suppression_max_age_days":7}}')
            off = build_plan(ROWS, PRIORS, as_of=DAY, audit=self.events.append)
            self.assertEqual(ids(off), [["C1", "S1"], ["O1", "M2"]])
            self.assertEqual(self.events, [])
            overrides.write_text('{"routing":{"suppress_soldout_tasks":true,"suppression_max_age_days":7}}')
            on = build_plan(ROWS, PRIORS, as_of=DAY, audit=self.events.append)
            self.assertEqual(ids(on), [["C1", "O1"], ["M2"]])
