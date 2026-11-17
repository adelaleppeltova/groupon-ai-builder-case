# January change request for Greg Lindqvist

**Decision requested:** Review the proposed January routing change. Deployment and activation after the peak freeze require separate written approval. The switch ships OFF and the pilot has not started.

**Change.** With the switch on, omit an otherwise eligible restock task only if a current, unambiguous CSV entry matches both the merchant and deal. Sales and Ops must verify the owning rep’s confirmation and record the date and reason. The planner checks the entry’s format, IDs and age. It cannot authenticate rep approval. The proposed freshness limit is seven UK calendar days, inclusive. Merchant-wide entries, merchant marks and AI outputs cannot suppress tasks. Existing priorities, time budgets and legacy exclusions continue to apply.

**Risk and control.** Incorrect or stale entries could hide restock opportunities. Missing or invalid entries fail safe. The new mechanism protects promised callbacks and suppresses only tasks explicitly marked as non-callbacks. The existing legacy exclusions can still omit callbacks. Confirm that the upstream callback field is complete before starting.

**Proposed pilot.** Freeze a rep-confirmed merchant roster and assign 80% to treatment and 20% to holdout before launch. Keep every sold-out for a merchant in its assigned arm. Apply the new mechanism only to authorised treatment entries. Confirm who restricts the input list to treatment and how that gate will be rehearsed. The offline assignment tool does not enforce the gate in routing. Measure actual suppressions separately from assignment and compare 30-day GP by original group.

**Proposed stop rules.** Stop immediately after one missed promised callback attributable to the new mechanism. Stop and investigate if at least two of the first 20 suppressed sold-outs require unplanned rep recovery within seven days. Hana has agreed to monitor technical runs. The logs identify removals and protection of input-flagged callbacks. Sales must confirm actual missed calls and recovery cases. Dan still needs to name the Sales owner. Preserve daily audit logs and source list versions outside the 14-day retention window.

**Rollback and reconciliation.** An authorised operator sets the effective flag to OFF, including any environment override, verifies the next run against baseline, and reviews previously omitted tasks with Sales for reinstatement or follow-up. Rollback does not recreate those tasks automatically.

**Before activation.** Confirm Greg’s written approval, Hana’s diff review, the Sales owner, input and callback checks, the rep-verification and treatment-only list workflow, and an export rehearsal. Review the freshness window and stop rules as proposals. The first cohort’s earliest readout is approved start plus 30 days and an explicit processing allowance.

[Technical review and audit examples](hana-routing-review.md)
