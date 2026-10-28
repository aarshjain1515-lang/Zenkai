# V1 live verification

Verified on October 28, 2026 against the isolated demo target.

| Check | Result |
| --- | --- |
| Persistence | Live Neon PostgreSQL |
| Diagnosis | Live OpenRouter request to `nvidia/nemotron-3.5-super-vl:free` |
| Incident | `37bd614f1a91` |
| Injected fault | Incorrect gateway upstream port |
| Evidence | 60 actual Loki log entries plus configuration and metric context |
| Proposed/executed action | `restore_upstream` |
| Policy | Evidence references, target/value allowlist, configuration revision/hash, and backend readiness validated |
| Result | Recovered with correct-content requests under continuing k6 traffic |
| Detection-to-verification duration | 63.02 seconds |
| Measured target requests in recovery window | 258.0 |
| Measured target errors in recovery window | 0.0 |

This run used the live provider, not the fixture-model integration harness. It verifies one live upstream-repair case; the other three break cases were verified separately with the explicitly labelled model fixture. It is not a production-capacity or broad model-quality benchmark.

Credentials remain in the ignored local `.env`. No credentials or raw log contents are included in this report.
