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

The published [sandbox OpenAPI schema](https://desk-production-6adc.up.railway.app/api/sandbox/v1/openapi.json) specifies the paginated sold-out list, bearer authentication and merchant mark operations. The schedule and monthly history endpoints describe generic rows. The app maps them using the corresponding data pack column names. Unusable history yields no estimate, and incomplete schedule rows are ignored. The app’s authenticated request handling, mark operations and LLM response handling were exercised with mocks. The public schema was fetched from the sandbox. The initial live UI smoke loaded sold-outs and some schedules. Authenticated endpoint-by-endpoint checks, mark writes and real model inference remain unverified.

The app uses the sold-out timestamp as the evidence cutoff. `GROUPON_EVIDENCE_DAYS` controls freshness, with 90 days as an MVP default. Deal actions use deal-linked text, matching schedules and, for the fallback below, exact-deal cap history. Merchant context is shown in its own panel for the rep to verify. Live contact restrictions take precedence. A missing evidence feed can prompt manual review, and a missing timestamp prevents evidence assessment.

When the rules would otherwise give an inconclusive review, the live rep tool may suggest **Call to verify capacity**. It requires explicit contact clearance, successful evidence and schedule reads, and a single latest prior sold-out for the exact merchant and deal. That record must document a numeric cap increase, with the restock date no earlier than the prior sold-out and strictly before the current sold-out day. The proposed window is `GROUPON_EVIDENCE_DAYS` UK calendar days, inclusive at its older boundary. The live app reads paginated merchant history from `/soldouts` through the previous UK day and checks dates locally as well.

Current automation, fresh deal signals, ambiguous or incomplete history and overlapping historical automation take priority over the fallback. Older same-day records are also excluded because the historical endpoint supplies dates only. The previous increase identifies a reason to ask about capacity. It cannot identify who raised the cap or establish current willingness. A successful read can only rule out schedule overlaps present in the source. Promised callbacks continue through existing routing, and the Phase 1 CLI keeps its original policy.

Evidence panels distinguish zero returned text records, records excluded from the panel's scope or time window, eligible text that matched no rule, and unavailable feeds. These counts describe the queried window.

The 91-event offline replay for sandbox team day 10 November 2026 reproduced the original 67 reviews and 24 automation checks. With the fallback, 29 reviews became verification calls, leaving 38 reviews and 24 automation checks. The replay joined the captured live event IDs, deals and timestamps to the supplied snapshot, including snapshot contact and schedule data. Live history and current permissions still need verification.

The app shows potential GBP value for call recommendations with usable prior voucher history. The scenarios illustrate what pursuing a cap raise could be worth and still need Priya's review. They do not establish incremental profit.

## Optional model interpretation

The model is disabled by default. After approval for a gateway test, set `GROUPON_ENABLE_LLM=1` alongside the sandbox key and use **Analyse evidence** on the selected sold-out. The default model is `claude-haiku-5-5`. Set `GROUPON_LLM_MODEL` to a supported model to override it. The Anthropic-compatible Messages endpoint is `/llm/v1/messages` on the same sandbox base URL.

Each click sends up to two eligible records per source (eight total) with thinking disabled. The app allows up to three attempts per browser session and makes one request per analysis with no automatic retries. The gateway applies its own USD 20 cap across sessions. Validation requires a completed message, structured JSON, supported categories and actions, and exact quotes from the selected record IDs. The UI displays validated findings with their original provenance, uncertainty and any conflicting classifications. Quotation matching verifies what the source said. The rep still judges whether the interpretation follows from it. Missing records, an unavailable gateway, incomplete responses or invalid output leave the deterministic report available. Text with invalid Unicode also falls back safely. The model does not supply calculations or modify sandbox records.

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

Naive timestamps are read in UK time. Date-only evidence is treated as available at end of day to avoid same-day leakage at a date-only decision. The replay does not use follow-up restock fields, outcome GP, current contact flags or subsequent activities. The pack offers effective schedule dates and monthly aggregates but no version history or publication timestamps; this replay cannot establish when retroactive edits or aggregates became available. The text rules are deliberately narrow. Explicit refusal overrides an apparent request in the same record. Unanswered questions, negated willingness and conflicting signals prompt review. The quote validator checks verbatim support against eligible source text; it cannot establish that a quote proves a proposed interpretation.

For a recommended call, the forecast multiplies added capacity by the assumed incremental fill rate and the merchant's net GP per voucher over up to six completed prior calendar months. It subtracts rep time and only **excess** refund-related GP risk above that history. Without usable prior voucher history it returns `insufficient_data`. GBP decimals in JSON are strings.

| Assumption | Low | Base | High |
| --- | ---: | ---: | ---: |
| Added capacity | 5 | 10 | 15 |
| Incremental fill | 25% | 50% | 75% |
| Rep time / hourly cost | 12 min / £25 | 12 min / £25 | 12 min / £25 |
| Excess refund GP loss per event | £5 | £2 | £0 |

These are illustrative scenarios, not estimates of treatment effect or confidence bounds. Finance does not drive the action, and the live recommendation would still need rep validation before use.

The proposed January routing patch is ready for [Hana’s review](docs/hana-routing-review.md). The holdout tools below run offline. Observational study reproduction and submission preparation are outside this phase.

## January pilot measurement

**Experiment status: NOT STARTED.** The January proposal and its 20% merchant holdout still need Greg’s approval. These commands only read and write local CSVs.

Run from the repo root. The assignment command creates a new file and refuses to overwrite it.

```sh
PYTHONPATH=src python3 -m soldouts.pilot assign tests/fixtures/synthetic_eligible_merchants.csv /tmp/groupon-demo-assignments.csv
PYTHONPATH=src python3 -m soldouts.pilot evaluate tests/fixtures/synthetic_assignments.csv tests/fixtures/synthetic_outcomes.csv --start 2027-01-11 --as-of 2027-02-13 --minutes-per-task 12 --synthetic
```

The fixture dates and outcomes are synthetic. The roster requires `merchant_id,rep_confirmed`. Only rows confirmed by a rep (`1`) enter the draw. The CLI deduplicates merchants, sorts IDs, and samples the proposed holdout with seed 42 by default. `--seed` and `--holdout` are configurable. The target count is rounded to the nearest whole merchant with both arms represented. Freeze the approved roster and the output (`merchant_id,experiment_group`) before launch. Reuse that output for every sold-out. A changed roster can change assignments when sampled again.

The evaluator requires that original assignment CSV and a pilot export with one row per eligible sold-out, including tasks that were never suppressed. The row's group must agree with the saved assignment. Duplicate event IDs and inconsistent groups fail validation. All headers are required. Leave unavailable values blank instead of entering zero.

| Outcome CSV column | Meaning |
| --- | --- |
| `merchant_id`, `experiment_group`, `sold_out_id` | Original assigned arm (`treatment` or `holdout`) and unique sold-out ID |
| `eligibility_date`, `sold_out_date` | ISO dates on the team clock. Eligibility must be on or before the sold-out. |
| `window_complete`, `gp_30d_gbp` | Confirmed 30-day outcome coverage (`1`/ `0` / blank) and net GP in GBP for that fixed window. The script also checks that the 30 days have elapsed by `--as-of`. |
| `suppressed`, `suppressed_at` | Actual new-mechanism suppression (`1` / `0` / blank) and ISO timestamp with UTC offset for ordering. |
| `rep_minutes`, `refunds_30d_gbp` | Actual rep minutes spent and refunds within the same 30 days, if reported. Record a known zero explicitly. |
| `recovery_within_7d` | Sales-confirmed unplanned rep recovery case within seven days after suppression. Use `1` for a confirmed case, `0` for a case reviewed with no incident, and blank for pending or unknown. |
| `missed_promised_callback` | Confirmed missed promised callback attributable to the new mechanism (`1` / `0` / blank). |

The comparison uses original assignment regardless of whether a task was suppressed. It averages complete, available sold-out GP within each merchant and then gives each reporting merchant equal weight. Missing outcomes and merchants without events are counted and excluded from the mean. The resulting available-case difference can be biased if missingness or event coverage differs by arm. The script omits uncertainty intervals rather than treating repeated sold-outs as independent. A future causal readout needs verified randomisation, enough merchants, comparable outcome coverage and a review of contamination. Holdout suppression is flagged. Rep time spent is observed where recorded. With `--minutes-per-task`, estimated gross time avoided equals confirmed suppressions times the assumed minutes. The export does not observe the minutes that would otherwise have been spent.

The first cohort’s earliest readout is **start + 30 days + processing allowance**. `--processing-days` defaults to an illustrative three days. With the synthetic 11 January 2027 start, the first cohort’s window ends 10 February and could be read out on 13 February if the data are available. Later cohorts mature separately. Refunds can arrive after 30 days. Priya’s reported £11.08 is a historical association and stays out of the calculations. The illustrative gross time range is £50–£150 per 100 eligible tasks if 10–30 are safely avoided at 12 minutes and £25/hour. A provisional net GP range is unavailable without assumptions about foregone GP and recovery.

The proposed stop thresholds are one attributable missed promised callback, or at least two suppressed sold-outs requiring unplanned rep recovery within seven days among the first 20 actual suppressions. Incomplete timing, a timestamp tie across position 20, or incomplete incident coverage can make the status indeterminate. Hana can monitor technical routing logs and preserve task-level events separately from their 14-day retention. Those logs can count removals and input-flagged callbacks. Confirming actual missed calls and recovery requires external follow-up, with a Sales owner still to be agreed. The evaluator reports status for review. Operators retain control of stopping or activating the pilot.

## Submission and known limits

Start the app locally using the command above. The supplied data archive is required for the offline demo and historical ZIP replay. Public hosting, dashboards, live paid inference validation and expanded historical analysis were deliberately deferred. The sandbox has had an initial read-only UI smoke, and the newer authenticated history and mark workflow still need controlled verification. The optional model has only been exercised with mocks. The final offline comparison on the captured 91 events matched the pre-fix actions for every event. The synthetic refusal from QA now gives a deal-specific no-call, and malformed model text leaves the deterministic recommendation visible.

- [January change request for Greg](docs/GREG_CHANGE_REQUEST.md)
- [Three lines for Dan](docs/DAN_THREE_LINES.md)
- [QA report and manual tests](docs/FINAL_QA_REPORT.md)
- [AI session export checklist](docs/AI_SESSION_EXPORT_CHECKLIST.md)

Verify the Desk submission fields and attach the manual exports before submitting. The remaining live checks and pilot dependencies are recorded in the QA report.
