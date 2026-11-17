# Phase 1 decisions

- Use four standard-library modules under `src/soldouts`: data adapters, evidence and advice, finance, and CLI. The future UI can call `evidence.assess` directly.
- Read the archive without extracting into the workspace. Keep the supplied archives out of version control. Leave the routing code untouched; the extracted baseline passed all 11 tests.
- A note or reply without a deal ID can prompt rep review, but cannot decide a deal's action. Keep exact raw text and replay IDs for validation. The supplied pack therefore gives few opportunities for text-only call or no-call recommendations. Rep validation is still outstanding.
- Use a standalone financial scenario with a merchant history baseline. Do not substitute an observational call comparison for incremental GP. Defer reproduction of the observational comparisons and estimation of pilot effects.
- Before Phase 2, review the evidence rules and 90-day window with the candidate, especially which merchant-level signals to show to a rep and which examples should demonstrate call economics.
