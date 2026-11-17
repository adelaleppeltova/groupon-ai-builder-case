# Hana’s technical review of the January routing proposal

Greg reviewed the change request and confirmed the 15:00 review on 17 November 2026 as a January item. Separate written approval for deployment and activation is outstanding. Ops owns operation of the routing switch and rollback.

Hana received the routing diff and review brief and responded with technical feedback. She confirmed the small, off-by-default approach and the flag-off comparison with original routing. She confirmed that the environment override is merged last and takes precedence. The current override contains `priors.restock`, with no `routing` section. She requested an explicit rollback check of both the base config and the effective override. The README now records that procedure.

Hana watches technical run logs only. She has not verified production stderr capture and will not own daily exports. Greg must assign that owner.

The Git patch starts at the verbatim routing import `867d783`. Run `git diff 867d783 HEAD -- routing README.md docs/hana-routing-review.md` to see it. The planner adds the eligibility check and run audit in `routing/routing/plan.py`. The CSV validation lives in `routing/routing/suppressions.py`. The new tests are in `routing/tests/test_pilot_suppressions.py`. The routing README documents config, logs and rollback.

- **Before and with the switch off:** The legacy contact, suppression and pause filters run as shipped. The planner gives callbacks priority, ranks the rest by expected GP and keeps the original budget and carryover behaviour.
- **With the switch on:** Only otherwise eligible restock rows explicitly marked as non-callbacks can be removed. A task must match both merchant and deal against a single, current entry with a recognised reason and rep confirmation source. Merchant-wide entries are ignored and diagnosed. The remaining tasks use the same ranking and budget. Legacy suppression can still exclude callbacks as before.
- **Example from the test:** On 2026-11-09 the planner suppresses task `S1` for deal `D1` at merchant `M1`. It cites CSV line 2, `capacity_confirmed`, `rep_confirmed:R01` and confirmation date 2026-11-02. It retains promised callback `C1` for that same deal. The summary records one suppression, one protected callback and zero callbacks removed by the new mechanism.

Verification covered 17 routing tests, including the 11 original tests, and 37 project tests. The shipped input produced the same full today/carried plans and byte-identical CLI output with the switch off. The pilot tests exercised eligibility, missing and malformed input, dates, environment overrides, audit and rollback.

**Monitoring and proposed stop rules**

- One missed promised callback attributable to the new mechanism triggers an immediate stop. The audit can check the count of input-flagged callbacks removed by this planner and trace matched callbacks by task ID. The morning run audit cannot confirm that a rep actually called or that every promised callback was marked correctly upstream.
- At least two cases requiring unplanned rep recovery among the first 20 suppressed sold-outs, assessed within seven days, trigger a stop and investigation. Sales needs to confirm and record recovery cases against suppressed task IDs. Dan has yet to confirm the Sales owner.
- Hana checks technical run logs only. A daily-export owner assigned by Greg must preserve daily audit events and their source list version outside the 14-day routing log retention window. The proposal has no automatic stop or activation.

**Outstanding operational questions**

1. Greg and Ops need to confirm whether the production runner captures stderr and links each run to the exact CSV version. Greg must name the daily-export owner and confirm storage. Hana has not verified capture and will not own exports.
2. Can Ops and Sales support the proposed reason codes, rep ID format and inclusive seven-day window? Who will verify the owning rep’s confirmation, publish the file and handle revocations?
3. Is the callback flag complete in the overnight input? Who reconciles reported callback misses with the upstream records and the existing legacy filters?
4. Ops owns setting the effective flag back to `false`. Confirm the operational handoff for reconciliation with Sales. Check both the base config and last-merged environment override during rollback.

A syntactically correct but false confirmation can still remove a task. The ownership and verification workflow above is the primary control for that risk. Disabling the effective flag restores baseline behaviour on the next run. Legacy suppressions remain in force.
