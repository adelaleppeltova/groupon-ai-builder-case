# Groupon UK Local sold-outs MVP

Phase 1: an offline tool that helps a rep triage a historical sold-out using traceable evidence and, when a call is recommended, a GBP scenario estimate. Recommendations are advisory. No service calls, credentials or package installation are needed.

Use Python 3.13 and run from the repo root:

```sh
PYTHONPATH=src python3 -m soldouts.cli replay data-pack.zip --sold-out-id SO-000006
PYTHONPATH=src python3 -m unittest discover -s tests -v
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

The Streamlit app, sandbox and LLM adapters, routing proposal, holdout and evaluation, observational study reproduction, and submission pack are later phases. No gateway calls, live writes, or routing changes were made. The proposed January pilot still requires Greg's written approval, Hana's technical review and a confirmed Sales owner.
