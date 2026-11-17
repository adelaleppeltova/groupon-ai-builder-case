# Phase 1 decisions

- Use four standard-library modules under `src/soldouts`: data adapters, evidence and advice, finance, and CLI. The future UI can call `evidence.assess` directly.
- Read the archive without extracting into the workspace. Keep the supplied archives out of version control. Leave the routing code untouched; the extracted baseline passed all 11 tests.
- A note or reply without a deal ID can prompt rep review, but cannot decide a deal's action. Keep exact raw text and replay IDs for validation. The supplied pack therefore gives few opportunities for text-only call or no-call recommendations. Rep validation is still outstanding.
- Use a standalone financial scenario with a merchant history baseline. Do not substitute an observational call comparison for incremental GP. Defer reproduction of the observational comparisons and estimation of pilot effects.
- Before Phase 2, review the evidence rules and 90-day window with the candidate, especially which merchant-level signals to show to a rep and which examples should demonstrate call economics.

## Phase 2 decisions

- The rep app uses separate merchant and deal evidence panels. Deal advice comes from the existing deterministic rules, using deal evidence only, and applies the live contact restriction when present. The Phase 1 CLI retains its existing behaviour.
- Figures show the hypothetical economics of a cap raise wherever the merchant has usable voucher history. The chosen action can still be to review, check automation or follow up with operations.
- Use server-side bearer authentication from the process environment. Cache reads inside each live browser session and refresh on request. Marks belong to the merchant and the sandbox key that created them.
- Model findings are advisory and shown with the source scope. Requests are opt-in, bounded and validated. Keep the rules and finance usable through gateway failures.
