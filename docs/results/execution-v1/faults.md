# Offline Agent fault matrix

synthetic fault regression; passing is not research quality or production availability

Planned: 17 · Outcome: passed

| Case | Assertion | Test | Runtime observation |
|---|---|---|---|
| requirements | Cannot delete requirements; reading is not verification | passed | No runtime: contract assertion |
| explicit_request | User criteria reach the model and persist with the run | passed | completed / stop=coverage_complete_review_pending / gaps=0 / tokens=500 (estimated=False) |
| missing_evidence | Reject evidence not read in this run | passed | No runtime: contract assertion |
| foreign_evidence | Reject another paper's evidence | passed | No runtime: contract assertion |
| stale_review | Invalidate old review after evidence changes | passed | No runtime: contract assertion |
| malformed_review | Invalid spans reject the entire review batch | passed | No runtime: contract assertion |
| repair | Fill gap while preserving original answers and accounting review cost | passed | completed / stop=coverage_complete_review_pending / gaps=0 / tokens=500 (estimated=False) |
| no_progress | Stop without progress; keep unresolved field | passed | completed / stop=no_progress / gaps=1 / tokens=500 (estimated=False) |
| review_failure | Keep report and cost, reject invalid review verdict | passed | completed / stop=coverage_complete_review_pending / gaps=0 / tokens=500 (estimated=False) |
| finalization_feedback | Preserve report validation feedback when rebuilding final context | passed | completed / stop=None / gaps=None / tokens=400 (estimated=False) |
| step_budget | Stop at step cap with explicit unresolved field | passed | completed / stop=max_steps / gaps=1 / tokens=300 (estimated=False) |
| unknown_usage | Do not spend more on review when prior usage is unknown | passed | completed / stop=unresolved_fields / gaps=1 / tokens=14405 (estimated=True) |
| token_overrun | Returned usage above cap fails the run and remains recorded | passed | failed / stop=token_budget_exceeded / gaps=0 / tokens=51400 (estimated=False) |
| service_failure | Service error preserves checkpoint and explicit gap | passed | completed / stop=repair_failed / gaps=1 / tokens=300 (estimated=True) |
| provider_timeout | Provider timeout preserves checkpoint; not a wall-clock load test | passed | completed / stop=repair_failed / gaps=1 / tokens=300 (estimated=True) |
| cancellation | Cancellation stays cancelled even with a valid checkpoint | passed | cancelled / stop=None / gaps=1 / tokens=300 (estimated=False) |
| workspace_isolation | Foreign workspace requirement rejected before any model call | passed | No runtime: contract assertion |

Missing or skipped cases are not passes. Synthetic reviewer output is not a human label.
Provider-timeout injection does not measure wall-clock scheduling or load.
