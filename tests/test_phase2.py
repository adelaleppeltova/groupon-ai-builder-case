from http.client import IncompleteRead
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from soldouts.llm import DEFAULT_MODEL, interpret, select_records
from soldouts.rep import SoldOut, assess_event, live_context, live_events
from soldouts.sandbox import Sandbox, SandboxError

EVENT = SoldOut("M1", "D1", "S1", "2026-10-20T08:00:00+01:00", "Merchant One",
                rep_id="R1", do_not_contact=False)


class HttpMock:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append(request)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, bytes):
            return io.BytesIO(result)
        return io.BytesIO(json.dumps(result).encode())


def note(record_id, text, *, deal_id="", date="2026-10-19T10:30:00"):
    return {"note_id": record_id, "merchant_id": "M1", "deal_id": deal_id, "rep_id": "R1",
            "text": text, "created_at": date}


class SandboxTest(unittest.TestCase):
    def test_pages_forward_opaque_cursor_and_preserve_team_date(self):
        http = HttpMock({"date": "2026-10-20", "data": [{"sold_out_id": "S1"}], "next_cursor": "a+b /"},
                        {"date": "2026-10-20", "data": [{"sold_out_id": "S2"}], "next_cursor": None})
        result = Sandbox("test-only", open_request=http).today()
        self.assertEqual([s["sold_out_id"] for s in result["data"]], ["S1", "S2"])
        self.assertEqual(result["date"], "2026-10-20")
        request = http.requests[1]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only")
        self.assertEqual(urlsplit(request.full_url).path, "/api/sandbox/v1/soldouts/today")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)["cursor"], ["a+b /"])

    def test_invalid_pagination_and_new_team_day_fail(self):
        first = {"date": "2026-10-20", "data": [], "next_cursor": "loop"}
        for second in (first, {"date": "2026-10-21", "data": []}, {"data": None}):
            with self.subTest(second=second), self.assertRaises(SandboxError):
                Sandbox("test-only", open_request=HttpMock(first, second)).today()

    def test_errors_never_echo_response_body(self):
        http = HttpMock(HTTPError("", 401, "", {}, io.BytesIO(b"private-key")), b"broken",
                        URLError("private-key"))
        api = Sandbox("private-key", open_request=http)
        for expected in ("Check GROUPON_API_KEY", "unreadable JSON", "could not be reached"):
            with self.subTest(expected=expected), self.assertRaisesRegex(SandboxError, expected) as exc:
                api.today()
            self.assertNotIn("private-key", str(exc.exception))

    def test_mark_read_write_correct_remove_and_validate_payload(self):
        original = {"merchant_id": "M1", "mark": "review", "reason": "Old", "marked_by": "R1"}
        update = {**original, "mark": "verified", "reason": "Checked", "marked_by": "R2"}
        http = HttpMock({"merchant_id": "M1", "mark": original}, update,
                        {"merchant_id": "M1", "removed": True}, {"merchant_id": "M1", "mark": None})
        api = Sandbox("test-only", open_request=http)
        self.assertEqual(api.merchant_mark("M1"), original)
        with self.assertRaises(ValueError):
            api.set_mark("M1", "x" * 65, "reason", "R2")
        self.assertEqual(api.set_mark("M1", " verified ", " Checked ", "R2"), update)
        api.remove_mark("M1")
        self.assertIsNone(api.merchant_mark("M1"))
        self.assertEqual([r.get_method() for r in http.requests], ["GET", "PUT", "DELETE", "GET"])
        self.assertTrue(http.requests[1].full_url.endswith("/merchants/M1/mark"))
        self.assertEqual(json.loads(http.requests[1].data),
                         {"mark": "verified", "reason": "Checked", "marked_by": "R2"})

    def test_malformed_mark_confirmation_and_already_removed_mark(self):
        http = HttpMock({"merchant_id": "M1"}, {"merchant_id": "M2", "removed": True},
                        HTTPError("", 404, "", {}, io.BytesIO(b"")))
        api = Sandbox("test-only", open_request=http)
        with self.assertRaises(SandboxError):
            api.merchant_mark("M1")
        with self.assertRaises(SandboxError):
            api.remove_mark("M1")
        api.remove_mark("M1")


class RepTest(unittest.TestCase):
    def test_incomplete_rows_are_skipped_or_get_manual_review(self):
        events, skipped = live_events([None, {"merchant_id": "M1"},
                                        {"merchant_id": "M1", "deal_id": "D1", "rep_name": None,
                                         "restock_task_id": "T1", "sold_out_at": "invalid"}])
        self.assertEqual(skipped, 2)
        event, = events
        self.assertEqual(event.event_id, "T1")
        self.assertEqual(assess_event(event, {}, live=True)["action"], "manual_review")

    def test_failed_context_feed_is_reported_and_can_prompt_review(self):
        payload = {"data": [note("N1", "Please raise the cap.", deal_id="D1"),
                            {**note("N2", "At capacity."), "merchant_id": "M2"}]}
        http = HttpMock(payload, URLError("offline"), *[{"data": []}] * 5)
        tables, missing = live_context(Sandbox("test-only", open_request=http), EVENT, 90)
        self.assertEqual(missing, ["deal_alert_replies"])
        history_request = urlsplit(http.requests[-1].full_url)
        self.assertTrue(history_request.path.endswith("/soldouts"))
        self.assertEqual(parse_qs(history_request.query)["to"], ["2026-10-19"])
        self.assertEqual(len(tables["rep_notes"]), 1)
        self.assertEqual(assess_event(EVENT, tables, live=True, incomplete=True)["action"], "manual_review")

    def test_call_economics_only_for_call_even_with_usable_history(self):
        history = [{"merchant_id": "M1", "month": "2026-09", "gp_gbp": "100", "vouchers_sold": "10"}]
        schedule = {"merchant_id": "M1", "deal_id": "D1", "schedule_id": "A1",
                    "start_date": "2026-10-01", "end_date": None}
        cases = [
            ("call", {"rep_notes": [note("N1", "Please raise the cap.", deal_id="D1")]}),
            ("ops_followup", {"rep_notes": [note("N1", "Double booked.", deal_id="D1")]}),
            ("no_call", {"rep_notes": [note("N1", "At capacity.", deal_id="D1")]}),
            ("check_automation", {"schedules": [schedule]}),
            ("manual_review", {"schedules": [{k: v for k, v in schedule.items() if k != "end_date"}]}),
        ]
        for action, tables in cases:
            with self.subTest(action=action):
                report = assess_event(EVENT, {**tables, "history": history})
                self.assertEqual(report["action"], action)
                if action == "call":
                    self.assertEqual(report["finance"]["status"], "scenario_estimate")
                else:
                    self.assertIsNone(report["finance"])

    def test_replay_uses_prior_text_and_merchant_context_cannot_override_deal(self):
        rows = [note("N1", "Please raise the cap.", deal_id="D1"),
                note("N2", "At capacity."),
                note("N3", "At capacity.", deal_id="D1", date="2026-10-20T07:00:01Z"),
                note("N4", "At capacity.", deal_id="D1", date="2026-10-20")]
        report = assess_event(EVENT, {"rep_notes": rows, "history": [
            {"merchant_id": "M1", "month": "2026-09", "gp_gbp": None, "vouchers_sold": None}
        ]})
        self.assertEqual(report["action"], "call")
        self.assertEqual(report["deal_evidence"][0]["source_ids"], ("N1",))
        self.assertEqual(report["merchant_context"][0]["source_ids"], ("N2",))
        self.assertEqual(report["finance"]["status"], "insufficient_data")
        self.assertEqual([r["source_id"] for r in select_records(EVENT, {"rep_notes": rows}, 90)],
                         ["N1", "N2"])


def finding(id="N1", quote="Please raise the cap.", category="restock_willingness", action="call"):
    return {"source": "rep_notes", "source_id": id, "category": category, "proposed_action": action,
            "quote": quote, "rationale": "The merchant requested a change.", "limitation": "Confirm with rep."}


def response(*findings):
    return {"stop_reason": "end_turn", "content": [{"type": "text",
             "text": json.dumps({"findings": findings, "uncertainty": "Check whether capacity changed."})}]}


class LlmTest(unittest.TestCase):
    def setUp(self):
        self.records = select_records(EVENT, {"rep_notes": [
            note("N1", "Please raise the cap.", deal_id="D1"),
            note("N0", "Please raise the cap.", deal_id="D1"),
            note("N2", "At capacity.", deal_id="D1", date="2026-10-19T11:00:00")
        ]}, 90)

    def test_grounded_contradictions_preserve_provenance_and_request_is_bounded(self):
        http = HttpMock(response(finding("N0"), finding("N2", "At capacity.", "capacity_refusal", "no_call")))
        result = interpret(Sandbox("test-only", open_request=http), self.records)
        self.assertEqual(result["status"], "interpreted")
        self.assertEqual([f["proposed_action"] for f in result["findings"]], ["call", "no_call"])
        self.assertEqual(result["findings"][0]["source_ids"], ("N0", "N1"))
        self.assertEqual(result["findings"][0]["timestamp"], "2026-10-19T10:30:00")
        request, = http.requests
        body = json.loads(request.data)
        self.assertEqual(body["model"], DEFAULT_MODEL)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertTrue(request.full_url.endswith("/llm/v1/messages"))

    def test_invalid_llm_responses_fall_back_instead_of_showing_partial_results(self):
        invalid = [response(finding("N0"), finding("N2", "Invented quote")),
                   response(finding("N0", "Please Raise the cap.")), response(finding("no-such-id")),
                   response({**finding("N0"), "source": "deal_alert_replies"}),
                   {**response(finding("N0")), "stop_reason": "max_tokens"},
                   {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{bad"}]},
                   {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"findings": []}'}]},
                   {"stop_reason": "end_turn", "content": []},
                   response({**finding("N0"), "rationale": "\ud800"}),
                   {"stop_reason": "end_turn", "content": [{"type": "tool_use"}]},
                   b'{"content": [', IncompleteRead(b"partial"),
                   b"[" * 2000 + b"]" * 2000]
        for data in invalid:
            with self.subTest(data=data):
                result = interpret(Sandbox("test-only", open_request=HttpMock(data)), self.records)
                self.assertEqual(result["status"], "fallback")
                self.assertNotIn("findings", result)
                self.assertEqual(result["reason"], "AI interpretation was unavailable or invalid.")

    def test_diagnostics_distinguish_wire_format_citation_and_scope(self):
        records = select_records(EVENT, {"rep_notes": [note("N1", "At capacity.")]}, 90)
        valid = finding("N1", "At capacity.", "capacity_refusal", "manual_review")
        fenced = response(valid)
        fenced["content"][0]["text"] = "```json\n" + fenced["content"][0]["text"] + "\n```"
        wrong_format = {**valid, "source_ids": ["N1"]}
        del wrong_format["source_id"]
        cases = (
            (fenced, "markdown_fenced_json"),
            ({**response(valid), "content": []}, "unexpected_content_shape"),
            (response(wrong_format), "invalid_finding_field:source_id"),
            (response({**valid, "source_id": "unexpected-value"}), "unknown_source_reference"),
            (response({**valid, "quote": "Invented quote"}), "quote_mismatch"),
            (response({**valid, "category": "refusal"}), "unsupported_category"),
            (response({**valid, "proposed_action": "no_call"}), "action_outside_scope"),
        )
        for payload, code in cases:
            with self.subTest(code=code):
                result = interpret(Sandbox("test-only", open_request=HttpMock(payload)), records)
                self.assertEqual(result, {"status": "fallback",
                                         "reason": "AI interpretation was unavailable or invalid.",
                                         "diagnostic_code": code})
        result = interpret(Sandbox("test-only", open_request=HttpMock(response(valid))), records)
        self.assertEqual(result["status"], "interpreted")
        self.assertEqual(result["findings"][0]["quote"], "At capacity.")
        self.assertEqual(result["findings"][0]["proposed_action"], "manual_review")

    def test_merchant_scoped_llm_action_and_transport_failure(self):
        records = select_records(EVENT, {"rep_notes": [note("N1", "Please raise the cap.")]}, 90)
        for payload in (response(finding()), URLError("offline"),
                        HTTPError("", 429, "", {}, io.BytesIO())):
            with self.subTest(payload=payload):
                http = HttpMock(payload)
                self.assertEqual(interpret(Sandbox("test-only", open_request=http), records)["status"], "fallback")
                self.assertEqual(len(http.requests), 1)
        http = HttpMock()
        self.assertEqual(interpret(Sandbox("test-only", open_request=http), [])["status"], "fallback")
        self.assertFalse(http.requests)

    def test_all_four_record_sources_are_available_when_relevant(self):
        record = {"merchant_id": "M1", "deal_id": "D1", "created_at": "2026-10-19",
                  "text": "Valid", "reason_text": "Valid", "received_at": "2026-10-19"}
        tables = {"rep_notes": [note("N1", "Valid")], "deal_alert_replies": [dict(record, reply_id="A1", deal_id="")],
                  "reviews": [dict(record, review_id="V1")], "refunds": [dict(record, refund_id="F1")]}
        self.assertEqual([r["source_id"] for r in select_records(EVENT, tables, 90)], ["N1", "A1", "V1", "F1"])


try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None


@unittest.skipUnless(AppTest, "Install requirements.txt to run the Streamlit checks")
class AppTestCase(unittest.TestCase):
    def test_live_app_shows_action_keeps_mark_separate_and_falls_back_on_ai_error(self):
        mark = {"merchant_id": "M1", "mark": "review", "reason": "Check booking slots.", "marked_by": "R2"}
        today = {"data": [{"merchant_id": "M1", "merchant_name": "Merchant One", "deal_id": "D1",
                           "sold_out_id": "S1", "sold_out_at": EVENT.sold_out_at,
                           "rep_id": "R1", "do_not_contact": False}], "date": "2026-10-20"}
        notes = {"data": [note("N1", "Please raise the cap.", deal_id="D1")]}
        http = HttpMock(today, notes, *[{"data": []}] * 6, {"merchant_id": "M1", "mark": None},
                        response({**finding(), "rationale": "\ud800"}),
                        mark, {"merchant_id": "M1", "removed": True})
        api = Sandbox("test-only", open_request=http)
        script = Path(__file__).resolve().parents[1] / "src" / "soldouts" / "rep_app.py"
        with patch.dict(os.environ, {"GROUPON_API_KEY": "mock-key", "GROUPON_ENABLE_LLM": "1",
                                     "GROUPON_EVIDENCE_DAYS": "90"}), patch("soldouts.sandbox.Sandbox", return_value=api):
            app = AppTest.from_file(str(script)).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.subheader[0].value, "Call the merchant")

            next(b for b in app.button if b.label.startswith("Analyse evidence")).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.warning[-1].value,
                             "Using deterministic evidence. AI interpretation was unavailable or invalid.")
            self.assertEqual(app.subheader[0].value, "Call the merchant")

            app.text_input[0].set_value("review")
            app.text_area[0].set_value("Check booking slots.")
            app.text_input[1].set_value("R2")
            next(b for b in app.button if b.label == "Save mark").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.success[0].value, "Mark saved.")
            self.assertIn("review · R2", [element.value for element in app.text])
            self.assertEqual(app.subheader[0].value, "Call the merchant")

            next(b for b in app.button if b.label == "Remove mark").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.success[0].value, "Mark removed.")
            self.assertEqual([r.get_method() for r in http.requests[-3:]], ["POST", "PUT", "DELETE"])
            self.assertEqual(json.loads(http.requests[-2].data), {k: mark[k] for k in ("mark", "reason", "marked_by")})


if __name__ == "__main__":
    unittest.main()
