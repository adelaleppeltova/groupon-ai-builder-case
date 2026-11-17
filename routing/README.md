# routing

Builds each rep's morning task list from yesterday's sold-outs. Owned by Merchant Ops (Hana day to day). Runs overnight; lists are ready by about 07:00.

```
python3 -m routing.plan input/todays_soldouts.csv
python3 -m unittest discover -s tests     # or: python3 -m pytest
```

## What it does

1. Takes the sold-outs for one rep (`input/todays_soldouts.csv` is yesterday's file for one rep).
2. Creates one restock task per sold-out. It works from that file only. It has no view of anything else that may be happening to the deal.
3. Drops do-not-contact merchants and anything in `suppressed_deal_ids`. Pauses merchants after too many failed attempts.
4. Promised callbacks go first, whatever they are worth. Someone told the merchant we would ring.
5. The rest are ranked by expected GP: `(called - left_alone) x value_30d_gbp`, using the restock priors.
6. Fills the rep's day. Whatever doesn't fit carries over to tomorrow and shows as overdue.

## Config

| File | What's in it |
|---|---|
| `config/routing.json` | Minutes per day, minutes per task, pause threshold, `suppressed_deal_ids`, `suppress_soldout_tasks`, `suppression_file`, `suppression_max_age_days` |
| `priors.json` | Restock priors used for ranking; also the priors for other task types |
| `config/env_overrides.json` | Environment values. Loaded last, on top of the above, and takes precedence (`priors` over `priors.json`, `routing` over `config/routing.json`) |
| `input/suppressions.csv` | Sold-out suppression list: `deal_id`, `merchant_id`, `reason`, `source`, `added_on`. Ships empty |

Notes for anyone changing things:

- All values are read at start of run. There is no hot reload.
- `suppressed_deal_ids` is a plain list of deal ids. A suppressed deal gets no task. Ops use it for deals in dispute or under a manual arrangement.
- Value per task comes from the input file; routing does not compute it.
- The input file's column order matters to the importer, so add columns at the end.

## Proposed January sold-out suppression

The flag ships as `false`. In that state the planner uses its original filters, ranks and time budget. When the effective flag is `true`, the planner checks the CSV before ranking the remaining eligible restock tasks. The lookup requires both the merchant and deal ID. It protects promised callbacks from this new mechanism and applies the existing contact restrictions, pause threshold and legacy `suppressed_deal_ids` as before.

The proposed CSV contract requires one row per merchant and deal. Use a reason code of `capacity_confirmed`, `restock_declined` or `already_restocked`. Use `rep_confirmed:R01` with the actual owning rep ID, and set `added_on` to the date of confirmation in `YYYY-MM-DD` format. Sales and Ops need to agree who verifies ownership, stores the supporting confirmation, updates the list and removes entries when circumstances change. The planner checks formatting and dates. It has no rep ownership field or access to those confirmations.

The proposed `suppression_max_age_days` default is seven UK calendar days, inclusive. The evaluation date defaults to the current date in Europe/London. To replay with a specific evaluation date, use the CLI option below. An override under `routing` in `config/env_overrides.json` wins over base config for the switch, file path and freshness limit. Invalid, stale or future entries and merchant-wide rows are ignored. Duplicate eligible entries for the same merchant and deal are ambiguous and neither is used. A missing or unreadable file yields no pilot suppressions. The shipped CSV is empty.

```
python3 -m routing.plan input/todays_soldouts.csv --as-of 2026-11-09
```

Each enabled run emits JSON lines on stderr with a run ID and evaluation date. Decisions identify the task, merchant and deal, plus the reason code, rep source, confirmation date and line of the matched entry. A summary gives the counts suppressed, callbacks protected, callbacks removed and diagnostics. Rejected rows are logged by status and line without their raw contents. The runner needs to capture stderr and preserve pilot records separately because routing logs expire after 14 days.

Rollback is to set the **effective** `suppress_soldout_tasks` value to `false`, including any environment override, before the next run. A list already delivered needs separate reconciliation. See [Hana’s review brief](../docs/hana-routing-review.md) for the proposed stop rules and responsibilities.

## Ops

- Changes go to Wednesday change review, then Merchant Ops deploys. Config changes count as changes.
- Peak change freeze: nothing touches routing or its config until 7 January.
- Version: 2.15.0 (2.15.0 added the suppression switch and file, unused)
