# #30 Pixie Evaluator Mapping

## Brief dataset (`BriefRunnable`)

| Evaluator | Covers | Applies to |
| --- | --- | --- |
| `brief_contract` | final Evidence/Brief source freshness, exact claim IDs, coverage partition, citation/redaction/refutation safety, candidate delivery state, and real action/Brief-receipt binding. | all |
| `grounding_quality` | Material Brief assertions are entailed or carefully qualified by current sanitized official evidence. | all |
| `source_coverage_quality` | Distinct current official sources are meaningfully represented or explicitly uncited. | all |
| `refutation_handling_quality` | Refuted scenarios do not present refuted support as settled. | refutation entry |

## Review dataset (`AppRunnable`)

| Evaluator | Covers | Applies to |
| --- | --- | --- |
| `review_contract` | collection/review schema, current Brief fingerprint, final-VIA/Evidence-derived proposal-to-kernel binding, dynamic opposite human review, visible conflict, authority rejection, receipt, and cost. It never requires transient model payload visibility. | all |
| `review_quality` | Agent findings grounding/specificity and verdict consistency; rejects confirmation/lock/send authority language. | all |

Each entry runs its mechanical and semantic evaluators only after a real cycle. Step 6 is run once only after that cycle has completed without a real failure. A terminal failed Brief/Review receipt surfaced during the bounded VIA wait ends that dataset cycle immediately: it has no result ID, evaluator scores, or Step 6 artifact, and no subsequent dataset cycle may start.

2026-07-14 final regression mapping: Brief node `7R6QX8GZ3EW3S7PVJ9KQ6E2D4M` returned terminal `failed` receipt `sha256:b368f58be53c24203a6240051b45d4bb3dfc1acee6a49d34b37f4c7a132dc5f5` (fresh/reused `14502/56576`) during initial VIA wait. Therefore no Brief result ID/scores, Step 6, Review reference capture, or Review cycle exists for this run.
