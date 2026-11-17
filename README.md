# Groupon UK Local sold-outs MVP

The rep tool uses verifiable source text and GBP scenarios to help reps triage sold-outs and record their judgement.

## Rep tool

Use Python 3.13. Set up the app once from the repo root.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Start the app with one command.

```sh
PYTHONPATH=src .venv/bin/python -m streamlit run src/soldouts/rep_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

With no `GROUPON_API_KEY`, the app shows historical examples from `data-pack.zip`. Use `GROUPON_DATA_PACK` for a different ZIP path or extracted CSV directory. Select `SO-043678` for a deal with quoted evidence and merchant context for an operations follow-up. Select `SO-000006` for a schedule that needs verification. Demo mark edits last for the browser session.

For live mode, set `GROUPON_API_KEY` in the process environment before startup. Use the team date shown in the app and **Refresh sandbox** to fetch a new snapshot. The signed-in key reads its own merchant marks. Saving overwrites the existing mark. Removal deletes the mark for that key. Enter your own rep ID when recording a mark. Mark edits affect the sandbox merchant record only.

The published [sandbox OpenAPI schema](https://desk-production-6adc.up.railway.app/api/sandbox/v1/openapi.json) specifies the paginated sold-out list, bearer authentication and merchant mark operations. The schedule and monthly history endpoints describe generic rows. The app maps them using the corresponding data pack column names. Unusable history yields no estimate, and incomplete schedule rows are ignored. The app’s authenticated request handling, mark operations and LLM response handling were exercised with mocks. The public schema was fetched from the sandbox. No authenticated requests, mark mutations or real model inference were performed.

The app uses the sold-out timestamp as the evidence cutoff. `GROUPON_EVIDENCE_DAYS` controls freshness, with 90 days as an MVP default. Only deal-linked text and matching schedules can support a deal action. Merchant context is shown in its own panel for the rep to verify. Live contact restrictions take precedence. A missing evidence feed can prompt manual review, and a missing timestamp prevents evidence assessment.

The app shows potential GBP value for call recommendations with usable prior voucher history. The scenarios illustrate what pursuing a cap raise could be worth and still need Priya's review. They do not establish incremental profit.

## Optional model interpretation

The model is disabled by default. After approval for a gateway test, set `GROUPON_ENABLE_LLM=1` alongside the sandbox key and use **Analyse evidence** on the selected sold-out. The default model is `claude-haiku-5-5`. Set `GROUPON_LLM_MODEL` to a supported model to override it. The Anthropic-compatible Messages endpoint is `/llm/v1/messages` on the same sandbox base URL.

Each click sends up to two eligible records per source (eight total) with thinking disabled. The app allows up to three attempts per browser session and makes one request per analysis with no automatic retries. The gateway applies its own USD 20 cap across sessions. Validation requires a completed message, structured JSON, supported categories and actions, and exact quotes from the selected record IDs. The UI displays validated findings with their original provenance, uncertainty and any conflicting classifications. Quotation matching verifies what the source said. The rep still judges whether the interpretation follows from it. Missing records, an unavailable gateway or invalid output leave the deterministic report available. The model does not supply calculations or modify sandbox records.

## Offline CLI and tests

The existing CLI uses the Python standard library and is independent of the Streamlit app. Run from the repo root.

```sh
PYTHONPATH=src python3 -m soldouts.cli replay data-pack.zip --sold-out-id SO-000006
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Pass the actual ZIP path if it lives elsewhere. An extracted directory containing the CSVs (or containing a `data/` subdirectory) also works. `replay` defaults to the start of the sold-out date in UK time. An explicit `--as-of` timestamp lets you inspect a later historical decision; it must be on or after the event date.

To run the detector against an unseen file:

```sh
PYTHONPATH=src python3 -m soldouts.cli detect /path/to/replies.csv \
  --source deal_alert_replies --merchant-id M10726 --as-of 2025-08-13
```

Use `--deal-id` to include rows for that exact deal, where the file provides that ID. Without it, the detector shows only merchant-level text. CSV headers:

| Source | Required columns | Optional columns |
| --- | --- | --- |
| `rep_notes` | `note_id,merchant_id,created_at,text` | `rep_id,deal_id` |
| `deal_alert_replies` | `reply_id,merchant_id,received_at,text` | `deal_id`, only when supplied explicitly |
| `reviews` | `review_id,merchant_id,created_at,text` | `deal_id` |
| `refunds` | `refund_id,merchant_id,created_at,reason_text` | `deal_id` |

The JSON is labelled `deterministic_patterns`. It includes the recommendation, reasoning, exact stored text, each source ID, original timestamp and scope. Matching repairs common mojibake without changing quotations. Exact duplicate payloads retain every source ID; malformed rows, missing identifiers and possible 500-character truncation limit what the detector can infer. Missing refund reasons are missing evidence. A 90-day review window is an MVP default (`--max-age-days`), subject to rep validation. Conflicting, stale, merchant-only or weak text goes to `manual_review` when no other deal evidence supports an action. A matching schedule supports `check_automation`; its date interval cannot establish that a top-up ran. No join guesses a deal ID from an alert reply.

Naive timestamps are read in UK time. Date-only evidence is treated as available at end of day to avoid same-day leakage at a date-only decision. The replay does not use follow-up restock fields, outcome GP, current contact flags or subsequent activities. The pack offers effective schedule dates and monthly aggregates but no version history or publication timestamps; this replay cannot establish when retroactive edits or aggregates became available. The text rules are deliberately narrow and do not understand context reliably. The quote validator checks verbatim support against eligible source text; it cannot establish that a quote proves a proposed interpretation.

For a recommended call, the forecast multiplies added capacity by the assumed incremental fill rate and the merchant's net GP per voucher over up to six completed prior calendar months. It subtracts rep time and only **excess** refund-related GP risk above that history. Without usable prior voucher history it returns `insufficient_data`. GBP decimals in JSON are strings.

| Assumption | Low | Base | High |
| --- | ---: | ---: | ---: |
| Added capacity | 5 | 10 | 15 |
| Incremental fill | 25% | 50% | 75% |
| Rep time / hourly cost | 12 min / £25 | 12 min / £25 | 12 min / £25 |
| Excess refund GP loss per event | £5 | £2 | £0 |

These are illustrative scenarios, not estimates of treatment effect or confidence bounds. Finance does not drive the action, and the live recommendation would still need rep validation before use.

The proposed January routing patch is ready for [Hana’s review](docs/hana-routing-review.md). Holdout and evaluation, observational study reproduction and the submission pack are later phases.
