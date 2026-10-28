# Zenkai

**University systems that recover.**

Zenkai is an AI-powered monitoring and self-healing system for university servers, portals, and the services behind them. It detects outages and abnormal behaviour, analyses logs and metrics to identify the likely cause, proposes a corrective action or configuration patch, and safely executes an approved recovery workflow.

The goal is to restore service when a university website crashes, a backend becomes unreachable, a deployment breaks connectivity, or heavy traffic overwhelms an application. Zenkai automates the investigation and operational recovery tasks that would otherwise require manual intervention by IT teams.

Built for **CODE E FIESTA 2026 — Challenge 4: The Self-Healing AI Automation System**, a six-hour AI hackathon organised by Medicaps University with SlowBros Labs.

## Project status

**Repository and observability foundation.** Prometheus, Grafana, Loki, and Alloy are configured in Docker Compose with a provisioned dashboard. Their initial setup was verified with service readiness checks, metrics scraping, log ingestion, and Grafana data-source checks.

The application stack below is **selected, not yet implemented**. The frontend, FastAPI API, Neon database schema, AI diagnosis engine, recovery worker, demo university service, failure injector, and k6 load-test scripts are **planned**. No Neon project or database connection has been provisioned or verified by this repository yet. The repository does not yet demonstrate autonomous recovery or include a runnable k6 test.

## The problem

University services face bursts of demand during results, admissions, examinations, and other high-traffic periods. A crashed application, a wrong upstream address, or an overloaded service can leave the portal unavailable while IT staff search through logs and manually try fixes.

Zenkai brings detection, diagnosis, and recovery into one incident workflow. The central outcome is restored service health and successful requests under the continuing test workload. Fee processing and course-registration logic are outside the core MVP; a university portal is the service being protected.

The system is intended for different universities through configured telemetry sources and authorised infrastructure connectors. It does not claim an existing integration with POD.ai or any specific university's production systems.

## Selected application stack

The MVP uses a Vite/React operations console, a FastAPI control API, a separate Python recovery worker, and **Neon PostgreSQL** for durable state. The existing observability stack supplies the evidence. k6 exercises the protected demo service throughout the failure and recovery.

| Layer | Decision | Purpose |
| --- | --- | --- |
| Frontend | [Vite](https://vite.dev/guide/) + React + TypeScript | Service overview, live incident timeline, log evidence, patch diff, and approval controls |
| UI and charts | Tailwind CSS + shadcn/ui + Lucide React + Recharts | Consistent dashboard components, icons, and lightweight metric charts |
| Frontend data | TanStack Query + native browser EventSource | API state and cache invalidation, with Server-Sent Events (SSE) for live incident updates |
| API | [FastAPI](https://fastapi.tiangolo.com/) + Python + Uvicorn | Typed HTTP endpoints for services, incidents, evidence, proposals, approvals, and events |
| Validation and settings | Pydantic v2 + pydantic-settings | Request contracts, strict AI response schemas, action validation, and server-side configuration |
| Database | [Neon PostgreSQL](https://neon.com/docs/guides/sqlalchemy) | Durable incidents, evidence snapshots, proposals, approvals, action attempts, jobs, and timeline events |
| Database access | SQLAlchemy 2.x async + Psycopg 3 + Alembic | Typed persistence, asynchronous PostgreSQL access, and versioned schema migrations |
| Background execution | Separate Python asyncio worker + PostgreSQL-backed job queue | Detection polling, diagnosis, retries, approval waits, execution, and recovery verification that survive an API restart |
| AI | OpenRouter + Claude Sonnet 5.5, model ID `anthropic/claude-sonnet-5.5` | Evidence-based diagnosis and structured recovery proposals |
| HTTP integrations | HTTPX async client | Query Loki and Prometheus, call OpenRouter, and run bounded service probes |
| Recovery connector | Docker SDK for Python + constrained configuration handlers | Restart allowlisted demo containers and validate/apply approved gateway configuration changes |
| Demo target | Small FastAPI university service behind Nginx | A real request path with controllable process, upstream-configuration, and overload faults |
| Application telemetry | Python logging with JSON formatting + prometheus-client | Structured incident/action logs and application metrics for Alloy, Loki, and Prometheus |
| Runtime | Docker Compose | Keep the control API, worker, observability services, and protected demo target separate |
| Development tools | pnpm for frontend; uv for Python | Reproducible environments and committed lockfiles when application scaffolding is added |
| Verification | pytest + HTTPX, Vitest + React Testing Library, Playwright, and k6 | Backend recovery-policy checks, important UI interactions, one end-to-end repair flow, and sustained load verification |

Exact application dependency versions will be pinned in manifests and lockfiles during scaffolding. Existing observability images are already pinned in `compose.yaml`. A plain Python state machine and PostgreSQL job table are sufficient for the first recovery loop; Redis, Celery, an agent framework, embeddings, and a vector database are outside the MVP stack.

### How the components connect

```text
React operations console
    ↕ HTTP commands + SSE incident events
FastAPI control API ────────────── Neon PostgreSQL
                                      ↕ durable jobs and incident state
                              Python recovery worker
                                ├─ Loki HTTP API: relevant log window
                                ├─ Prometheus HTTP API: metric context
                                ├─ OpenRouter: structured diagnosis/proposal
                                └─ Policy validator → registered recovery connector
                                                           ↓
k6 continuous traffic ──────────────────────── Nginx → Demo university service
                                                           ↓
                                              JSON logs + application metrics
                                                           ↓
                                              Alloy/Loki + Prometheus → Grafana
```

The React console owns the recovery experience. Grafana remains the deeper observability view. Both use actual backend and telemetry data; charts and timeline transitions must not be fabricated by the frontend.

### OpenRouter and Loki diagnosis

**Default model: [Claude Sonnet 5.5 through OpenRouter](https://openrouter.ai/anthropic/claude-sonnet-5.5).** The model ID and advertised structured-output support were checked against the [OpenRouter model catalogue](https://openrouter.ai/api/v1/models) on October 28, 2026. This is a model selection, not a completed evaluation of Zenkai incident-diagnosis quality or latency.

The worker, rather than the model itself, queries Loki. For each deduplicated incident it builds a bounded evidence bundle: a service-scoped LogQL time window, representative error lines, recent metric summaries, probe results, and the current/known-good configuration diff. Cap the time range, line count, and payload size, and redact secrets before making the model request. Raw logs are untrusted evidence, never instructions to execute.

Call OpenRouter directly through HTTPX using a strict JSON Schema response format and provider routing with `require_parameters: true`. Structured-output support is endpoint-specific, so verify that the selected route supports the required schema settings. See [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs) and [provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).

The Pydantic-validated proposal contains the likely root cause, evidence IDs, registered target/action, optional constrained configuration patch, preconditions, and verification plan. The backend computes the policy decision and approval requirement; a model-supplied risk label cannot grant permission. An output that is valid JSON can still be factually wrong or unsafe and must pass the independent action validator.

Use one diagnosis per incident with bounded model attempts, timeouts, and token budgets. If OpenRouter is unavailable or a proposal remains invalid, retain the incident and escalate without executing a new model-generated repair. Keep the model configurable through `OPENROUTER_MODEL`; changing it requires checking route compatibility and rerunning the repair scenarios. Do not silently replace a live diagnosis with a canned response.

### Neon persistence and recovery jobs

Neon PostgreSQL is the authoritative application store. Loki retains log streams and Prometheus retains metrics; PostgreSQL stores incident evidence snapshots and references, proposal versions, approval decisions, execution attempts, job leases, and ordered timeline events. Use relational constraints for unique action identities and JSONB for bounded evidence and patch documents.

Use a pooled Neon `DATABASE_URL` for the API and worker, and a direct `DATABASE_URL_UNPOOLED` for Alembic migrations. Require TLS, keep application connection pools small, and use driver settings compatible with Neon's transaction pooler. Avoid session-level locks or `LISTEN/NOTIFY` on the pooled endpoint; SSE will read persisted timeline events using a resumable cursor. See [Neon connection pooling](https://neon.com/docs/connect/connection-pooling).

The worker claims eligible jobs with a short transaction using `FOR UPDATE SKIP LOCKED`, records a lease and ownership token, and commits before calling a model or changing infrastructure. PostgreSQL documents this locking option for [queue-like tables](https://www.postgresql.org/docs/current/sql-select.html). Lease renewal and per-target ownership checks prevent competing workers from starting the same repair. Initial deployment uses one recovery worker.

Recovery is an explicit state machine: `detected → diagnosing → proposed → validating → awaiting_approval (when required) → executing → verifying → recovered`, with blocked, retry-scheduled, and escalated outcomes. Approved jobs are enqueued transactionally with their approval record. An API response or an in-process FastAPI background task is not the durable job record.

After a worker interruption, reconcile the target's actual state before retrying an uncertain action. Job processing is at least once; connector idempotency and reconciliation protect against duplicate effects. Never hold a database transaction open during an AI call or infrastructure action. If Neon cannot persist the required approval/action state, do not start a new mutation; reconcile any in-flight attempt after connectivity returns.

### Execution and live UI boundaries

The protected demo application and Nginx gateway run separately from the Zenkai control API and worker, so crashing the target does not intentionally crash its healer. Infrastructure credentials or Docker access belong only to the trusted recovery worker/connector, never to the browser or general API container. The Docker SDK accepts server-side allowlisted targets; model output cannot choose arbitrary container IDs or executable commands.

Use a registered configuration handler to construct the Nginx change, run its syntax check and backend probe, and reload through a fixed operational routine. Store the proposal hash, configuration revision, and approval before execution. Validate recovery under continuing k6 traffic rather than relying on a successful restart command.

Use SSE for the one-way incident timeline, with normal authenticated HTTP requests for approval/rejection and replay controls. Reconnect from persisted event IDs and refresh TanStack Query state as needed. The MVP has one configured operator, server-issued HttpOnly/SameSite session cookies, and CSRF protection for mutating requests. OpenRouter and Neon credentials stay server-side and must never use Vite's browser-exposed `VITE_` environment prefix.

Planned server settings include `DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `LOKI_URL`, `PROMETHEUS_URL`, and operator/session secrets. Application settings, credentials, schemas, workers, and migrations are not wired into the current Compose stack yet. Neon and OpenRouter require network access for the live demo; no local database fallback is selected.

## The recovery loop

```text
University portal / backend / managed server
                  ↓ metrics, logs, health checks
       Detect → Correlate evidence → AI diagnosis
                  ↓
       Propose action or configuration patch
                  ↓
       Policy checks → Dry run → Approval when required
                  ↓
       Execute → Retry failed request → Verify recovery
                  ↓
       Record outcome or stop and escalate
```

Prometheus and health checks supply failure signals. Alloy sends structured logs to Loki. Grafana displays service health, incident evidence, recovery actions, and before/after measurements. Zenkai correlates those signals and manages the recovery workflow; Grafana itself is not the repair executor.

## Hackathon requirements and acceptance criteria

All recovery capabilities below are **planned** until implemented and verified.

| Challenge requirement | Planned implementation | Evidence required in the demo |
| --- | --- | --- |
| Monitor a workflow and detect errors or unexpected outcomes in real time | Monitor the request-serving workflow using health checks, availability, latency, error rates, and resource signals where exposed | A controlled crash or configuration fault produces an automatically detected incident |
| Analyse logs or execution data to identify the likely cause | AI correlates recent errors, metrics, health probes, service topology, and configuration changes | Diagnosis cites actual evidence, distinguishes observations from hypotheses, and identifies the affected component |
| Recommend or generate corrective action or a payload patch | Return a structured recovery plan using allowed actions, including a constrained configuration patch where appropriate | Display the proposed action, supporting evidence, and exact configuration diff |
| Validate the proposed fix | Validate action permissions, patch schema, target scope, resource budget, and a dry run or canary probe | A valid fix passes; an unsafe or out-of-scope proposal is blocked |
| Retry with human approval where necessary | Execute only policy-permitted actions; review higher-impact changes and replay the failed probe or request | Approval is tied to the exact target and proposal; execution and retries are bounded and audited |
| Demonstrate end-to-end failure injection, diagnosis, and successful recovery | Inject a fault into a working demo service and verify recovery under continuing k6 traffic | The portal serves correct responses again, configured recovery criteria hold, and the incident records measured recovery time |

A configuration patch is one supported corrective action. The challenge also permits other corrective actions; not every outage requires a payload rewrite.

## Flagship demo: the university portal goes down and recovers

1. Start a small university-portal demo service and establish a healthy baseline.
2. Run a bounded k6 workload against the demo service. Keep traffic active throughout the incident and recovery observation window.
3. Inject a specific fault, such as changing the gateway's backend port to an invalid value. k6 generates traffic; a separate failure control injects the fault.
4. Health probes and real failed requests trigger an incident. Zenkai retrieves the correlated connection errors, affected-service metrics, and configuration change.
5. AI identifies the likely upstream configuration regression and proposes a patch restoring the backend address from the configured service inventory or known-good configuration.
6. Deterministic checks validate the patch and probe the intended backend. An operator approves the exact patch when policy requires it.
7. The executor applies the validated patch, reloads the affected component, and retries the previously failed request.
8. k6 continues sending traffic. Successful response checks, service health, and latency/error measurements confirm recovery over a configured observation window.
9. Grafana and the incident console show the failure, diagnosis, action, validation results, and measured time to recovery.

A single green health check is insufficient. The service must return correct responses and sustain the configured workload after the repair. Do not remove the fault manually or stop the load and attribute that improvement to Zenkai.

## Three controlled failure scenarios

| Scenario | Evidence for diagnosis | Proposed recovery | Verification |
| --- | --- | --- | --- |
| Application process crashes | Failed probes, request errors, process state, and recent application logs | Restart the managed demo service through a registered connector, subject to restart limits | Process stays healthy and application requests succeed over the observation window |
| Configuration change breaks connectivity | Connection-refused errors, service inventory, and the recent configuration diff | Generate a restricted patch restoring the known-good upstream address, then validate and reload | Backend probe passes and the original request succeeds |
| Traffic overload | k6 traffic measurements, elevated latency/errors, and available resource or queue metrics | Select a configured overload runbook, such as enabling bounded admission control or adding a pre-provisioned worker within a fixed resource budget | Accepted requests recover within configured limits under sustained offered load; rejected requests and dropped iterations remain visible |

The MVP prioritises one complete recovery path before expanding to the other scenarios. For overload, report admitted traffic and rejection rates honestly; a waiting room alone does not prove the underlying service has recovered. A restart that repeatedly fails must stop and escalate rather than loop indefinitely.

A separate **unsafe-proposal injection** tests the validator, for example by proposing an unapproved destination or exceeding the worker budget. Label it as injected test input, not as a genuine live-model response. Rejection must leave the target configuration unchanged.

## What AI automates

AI reviews an incident evidence bundle and returns a structured diagnosis, supporting log/metric references, a registered recovery action or configuration patch, its proposed scope, and the checks needed to establish recovery.

The executor supports a small, explicit action catalogue: restart a managed service, restore an approved configuration, reload an affected component, run a diagnostic probe, or execute a bounded operational runbook. These are planned capabilities, not arbitrary shell access.

Deterministic code handles detection rules, authorisation, validation, approval, action execution, retries, and outcome checks. AI confidence alone never authorises a change. IT staff receive the investigation context and a concrete proposal when the system cannot safely resolve an incident automatically.

## Observability and load-testing stack

| Component | Role | Current status |
| --- | --- | --- |
| [Prometheus](https://prometheus.io/docs/introduction/overview/) | Metrics collection for availability, errors, latency, resource pressure, and recovery outcomes | Configured; currently scrapes observability services, with application targets to follow |
| [Grafana](https://grafana.com/docs/grafana/latest/) | Service-health dashboards, metric/log correlation, and recovery measurements | Configured with Prometheus and Loki data sources and a starter dashboard |
| [Grafana Loki](https://grafana.com/docs/loki/latest/) | Store and query structured application and execution logs | Configured; demo log ingestion verified |
| [Grafana Alloy](https://grafana.com/docs/alloy/latest/) | Collect application logs and forward them to Loki | Configured for local JSON log files |
| [Grafana k6](https://grafana.com/docs/k6/latest/) | Generate controlled traffic and measure behaviour before, during, and after failure | Selected; scripts and integration are planned |

Use Alloy for new log collection; [Promtail reached end of life on March 2, 2026](https://grafana.com/docs/loki/latest/send-data/promtail/).

Planned log fields include `timestamp`, `level`, `service`, `incident_id`, `request_id`, `action_id`, `step`, `outcome`, and `error_code`. Keep per-request and per-incident identifiers in log bodies, not metric or Loki labels. Redact credentials and personal data before collection or model use.

Neon PostgreSQL will record recovery state, proposals, approvals, attempts, and verification results. Telemetry supports the investigation; it is not the sole audit record.

## k6 verification plan

- Define bounded, repeatable baseline, incident, and recovery phases for the authorised demo target.
- Use response checks for both status and expected content; a generic success page must not hide a broken application.
- Configure [thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/) for pass/fail criteria. Checks alone do not make a k6 test fail.
- Keep the offered workload stable through repair and verification. Record achieved throughput, errors, latency, and dropped iterations so a saturated load generator cannot masquerade as successful recovery.
- Report the expected failure interval separately from the recovery window. A whole-run threshold may fail because of the intentionally injected outage; retain that result and evaluate the recovery window explicitly.
- Choose the initial traffic level and recovery limits from local baseline measurements. Report the actual configuration and results, not an unverified claim about supporting a particular number of students.

Scripts, failure-control endpoints, recovery-window evaluation, and any k6-to-Prometheus export will be documented when implemented. No k6 run or server-recovery result has been claimed at this stage.

## Integration and execution boundaries

Each university deployment configures its monitored services, expected topology, telemetry access, infrastructure credentials, allowed actions, approval policy, and known-good configurations. Automatic writes require an authorised operational interface; read-only telemetry access permits diagnosis and recommendations only.

The hackathon uses owned demo services and fictional data. The initial target is an isolated local service environment, with additional server or cloud connectors as future work. Software recovery cannot repair physical hardware faults or promise to resolve every vendor-internal outage.

## Recovery safeguards

- Preserve original evidence and configuration snapshots; audit every proposed and executed action.
- Validate actions against the registered target, supported patch schema, policy, and resource limits.
- Recheck the current incident and configuration revision before writing; reject stale proposals.
- Allow low-risk, pre-authorised actions to run automatically; require approval for higher-impact changes.
- Bind approval to the exact action and target. Forbidden actions remain blocked even with approval.
- Use action identities, per-target execution locks, cooldowns, and bounded retries to prevent duplicate or competing repairs.
- Stop and escalate on ambiguity, exhausted attempts, or unsuccessful verification. Use a supported rollback when appropriate and verify its outcome as well.
- Verify the repaired service and original request under the continuing test workload before marking an incident recovered.
- Keep student records, payment decisions, and academic approvals outside infrastructure-repair actions.

## Six-hour implementation order

1. Build a small university-portal demo service, an operational connector, and deterministic crash/configuration failure controls.
2. Connect its metrics, structured logs, and health probes to the existing observability stack; add bounded k6 baseline traffic.
3. Implement incident detection and evidence collection, then runtime AI diagnosis and a structured recovery proposal.
4. Complete one configuration-repair path with validation, approval, execution, request retry, and sustained recovery checks.
5. Add process-restart recovery and overload handling only after the main path works; verify unsafe-proposal rejection and bounded attempts.
6. Polish the incident timeline and Grafana views, rehearse the full injection-to-recovery demo, and report measured results.

## Development and commit hygiene

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the repository. Coding agents must also follow [AGENTS.md](AGENTS.md).

Complete one coherent change, verify it, commit it with a Conventional Commit message, and push before starting the next change. Preserve other contributors' work and never commit secrets or real student records.

```bash
git clone https://github.com/arnavsx3/Zenkai.git
cd Zenkai
```

Start the current observability foundation using [observability/README.md](observability/README.md). Recovery-engine and k6 run commands will be added with their implementation. Cloning the repository alone does not start a self-healing service.
