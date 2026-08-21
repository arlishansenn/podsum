# #30 Pixie Brief and Review Eval Criteria

## Brief use cases

1. `BriefRunnable` starts the real five-contract daemon with tracing disabled, ingresses public official evidence, and captures only final Evidence/Brief/cost outputs.
2. A `refutes` scenario resolves both subjects from final Evidence VIA `email_entry_id` values and posts a real relation action; a `redaction` scenario posts an owner redaction and retains its real retention path.
3. After an action, the runnable reads current Evidence and uses a bounded GET-based wait for a candidate Brief whose `source.revision` and `source.material_fingerprint` equal that Evidence ledger. It never fabricates publication or blindly sleeps through a terminal failure.

| # | Criterion | Data to capture |
| --- | --- | --- |
| 1 | Candidate Brief source, stable claims, coverage, safety state, and citations match final current Evidence. | `evidence_via`, `brief_via` |
| 2 | Optional action receipt has at least `performed`, `kind`, `action_id`, `response`, and the real latest-source Brief ledger receipt. | `update_receipt` |
| 3 | Current Brief is grounded in official excerpts and reflects refuted/redacted evidence correctly. | `evidence_via`, `brief_via` |
| 4 | Real Reactor receipt/disposition/chain/cost is captured. | `receipt_cost` |

## Review use cases

1. `AppRunnable` remains the Review-only runnable over a real Agent Brief and dynamically submits the opposite human verdict.
2. The Review Agent findings must be concrete and grounded in the final Brief/Evidence VIA values.
3. An Agent confirmation attempt is rejected by the real Gateway without changing candidate Brief authority.

| # | Criterion | Data to capture |
| --- | --- | --- |
| 1 | Review collection schema binds current Brief fingerprint; Agent findings are non-empty and specific. | `brief_via`, `review_via` |
| 2 | Agent findings are grounded in current public excerpts/URLs; verdict is consistent. | `evidence_via`, `brief_via`, `review_via` |
| 3 | Dynamic opposite human verdict remains an active conflict. | `review_action`, `review_via` |
| 4 | Agent confirm is Gateway-400 rejected and does not change Brief. | `authority_rejection`, `brief_via` |
| 5 | Real Review receipt, chain/status, and cost are captured. | `review_receipts`, `receipt_cost` |

Datasets contain only new public official URL metadata; no fixtures/examples or private content.
