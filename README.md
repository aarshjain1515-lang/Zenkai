# Zenkai

**Detect the break. Understand the cause. Verify the comeback.**

Zenkai is an independent AI recovery tool for university portals and the services behind them. Connect a target's telemetry and a registered operational connector; Zenkai detects failed requests, reads the relevant logs from Loki, asks an OpenRouter model for a structured diagnosis and repair proposal, validates it, and executes an authorised action.

The included fictional university page is a **separate, deliberately breakable test target**. Zenkai is not an ERP, fee-processing system, or student-information product.

Built for **CODE E FIESTA 2026 — Challenge 4: The Self-Healing AI Automation System**.

## The problem

Modern organisations depend on automated workflows and software integrations. A failed API, unexpected response format, crashed process, or broken connection can stop a whole workflow. Finding the responsible component and applying a safe fix often requires engineers to inspect several tools manually.

Zenkai brings that investigation and recovery into one traceable incident workflow. University services provide the demo context; the recovery engine is separate from the application being monitored.

## V1 status

Implemented:

- A responsive React recovery console with live health, measured traffic, incidents, evidence, proposals, approval controls, and setup readiness.
- A separate Nginx/FastAPI demonstration target with four controlled break cases.
- Loki log collection through Alloy, Prometheus metrics, Grafana dashboards, and k6 traffic with Prometheus remote write.
- A FastAPI control API with operator sessions, CSRF checks, and resumable incident events.
- Neon-compatible PostgreSQL persistence, Alembic migrations, durable jobs, worker leases, and interrupted-action escalation.
- An OpenRouter client using a strict response schema, evidence references, independent policy validation, and constrained recovery handlers.
- Correct-content recovery probes and measured traffic/error checks while k6 is running.

**Live model diagnosis requires your OpenRouter key.** The model client is implemented and contract-tested; the container integration harness uses an explicitly labelled fixture model and disposable local PostgreSQL. It never substitutes a canned model response in the running app. PostgreSQL credentials, live connections, and model keys are local configuration, not repository content.

## Run locally

Requirements: Docker Engine with Compose, Node.js compatible with Vite, pnpm, uv, and Python 3.12 managed by uv.

```bash
git clone https://github.com/arnavsx3/Zenkai.git
cd Zenkai
python3 scripts/init_local.py
```

The initializer creates an ignored `.env`, preserves existing values, and generates local operator/session/lab secrets without printing them. Use [.env.example](.env.example) as the reference. Fill in:

```dotenv
DATABASE_URL=<Neon pooled PostgreSQL URL, with TLS>
DATABASE_URL_UNPOOLED=<Neon direct PostgreSQL URL, with TLS>
OPENROUTER_API_KEY=<your OpenRouter key>
OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free
```

Build and start the application and observability services:

```bash
bash scripts/start.sh
```

After adding Neon credentials, apply the schema and reload the application environment:

```bash
docker compose run --rm api alembic upgrade head
docker compose up -d --force-recreate api worker
```

| View | Address |
| --- | --- |
| Zenkai recovery console | http://localhost:5173 |
| Separate demo target | http://localhost:8080/campus |
| Grafana service-recovery dashboard | http://localhost:3300/d/zenkai-overview |
| Prometheus | http://localhost:9090 |
| Control API documentation | http://localhost:8000/docs |

Sign in to the console using `OPERATOR_PASSWORD` from your local `.env`. Grafana uses username `admin` and `GRAFANA_ADMIN_PASSWORD`. Keep these values out of GitHub and the frontend. Ports bind to the local machine only.

Without the external credentials, the target and observability can still run. The console shows the missing setup and disables controls that require durable state; it does not fabricate incidents, AI responses, or successful recoveries.

## Demo flow

1. Connect Neon and OpenRouter, run migrations, and sign in.
2. In **Failure lab**, start the bounded k6 workload: 25 requests/second for three minutes.
3. Inject one break case. The target must genuinely return errors or stop responding.
4. Zenkai confirms the request failure, opens an incident, and collects a bounded Loki log window, metric context, probe results, and registered configuration.
5. OpenRouter returns an evidence-linked diagnosis and structured action proposal.
6. Independent policy checks validate the target, proposed values, evidence references, and configuration revision. Disable autonomous mode to require explicit approval of the exact proposal hash.
7. The worker applies the allowed repair and replays correct-content requests.
8. Verification observes the repaired target and, while k6 is active, checks measured traffic and errors before recording recovery.
9. Inspect the incident timeline and Grafana's real request, latency, target-health, and log panels.

Reset the lab between scenarios. **Manual reset is recorded as a reset, not AI recovery.** An unsafe-proposal control explicitly injects an invalid candidate into the validator; it must be blocked without infrastructure changes.

## Included break cases

| Break case | Actual failure | Registered corrective action |
| --- | --- | --- |
| Broken upstream connection | Gateway points to an invalid backend port; requests fail | Restore the registered port, validate Nginx configuration, and reload |
| Unexpected API data format | Directory provider renames `services` to `offerings`; the request workflow fails validation | Update the constrained adapter field mapping to the observed provider contract |
| Application crash | Stop the demo container process; service requests fail | Start the registered target service and verify its responses |
| Capacity exhaustion | Lower the demo concurrency budget; k6 requests are rejected | Restore the approved budget of 24, within the validator's fixed limit |

Capacity rejection is an intentionally controlled demo condition, not a claim that k6 exhausted a production university server. A successful restart or green health check alone is insufficient: the request must return the expected content and pass the recovery observation window.

## Challenge alignment

| Requirement | V1 implementation |
| --- | --- |
| Monitor and detect errors or unexpected outcomes | Repeated correct-content HTTP probes over the request-serving workflow; metrics and logs provide supporting signals |
| Analyse logs or execution data for likely cause | Worker queries Loki and Prometheus and sends a bounded evidence bundle to OpenRouter |
| Recommend or generate corrective action | Strictly typed action proposals and constrained upstream/schema/capacity patches |
| Validate proposed fixes | Evidence-ID checks, target allowlists, fixed approved values, revision/hash checks, backend readiness, and Nginx syntax validation |
| Human approval where necessary | Operator review bound to the exact proposal; pre-authorised demo actions may run automatically |
| Retry and prove successful recovery | Replay correct-content probes, verify sustained requests and metrics, then persist the outcome |
| End-to-end failure injection | Separate breakable target, real k6 traffic, incident workflow, and registered execution connector |

## Technology stack

| Layer | Implementation |
| --- | --- |
| Recovery console | Vite, React, TypeScript, TanStack Query, Recharts, Lucide, responsive CSS |
| Control API | FastAPI, Uvicorn, Pydantic v2, HTTPX |
| Persistence | Neon PostgreSQL, SQLAlchemy async, Psycopg 3, Alembic |
| Recovery orchestration | Separate Python asyncio worker and PostgreSQL job table |
| AI | OpenRouter; default `nvidia/nemotron-3.5-super-vl:free` |
| Logs | JSON application logs, Grafana Alloy, Loki |
| Metrics and dashboards | prometheus-client, Prometheus, Grafana |
| Target and execution | Nginx, separate FastAPI service, Docker SDK, registered configuration handlers |
| Traffic | Grafana k6, constant-arrival-rate scenario, remote-write metrics |
| Reproducibility | Committed pnpm/uv lockfiles, pinned container images, Docker Compose |

```text
Connected target → logs → Alloy → Loki ────────────┐
                 → metrics → Prometheus ──────────┤
                                                 ↓
React console ↔ FastAPI ↔ Neon PostgreSQL ↔ Recovery worker
                                                 ↓
                                      OpenRouter diagnosis
                                                 ↓
                                 Policy → approval → connector
                                                 ↓
k6 keeps sending traffic ───────────────→ protected target
                                                 ↓
                                  verified requests + Grafana
```

## AI and execution policy

The worker fetches logs; the model does not receive unrestricted Loki, Docker, shell, or database access. Logs are untrusted evidence, not instructions. Secrets are redacted before model requests. OpenRouter uses JSON Schema output and provider routing with `require_parameters: true`; endpoint support and free-model availability can change.

The current free default is listed in the [OpenRouter model catalogue](https://openrouter.ai/api/v1/models) and [model page](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free). Its selection is not a completed quality or latency benchmark for Zenkai. Free endpoints are rate limited; a provider failure or invalid proposal escalates the incident rather than executing an invented fix. Changing the model requires checking structured-output compatibility and rerunning the break cases.

Only registered targets and actions are executable. Approval cannot bypass a forbidden mutation. Configuration snapshots and proposal hashes protect against stale decisions. A single worker lease and transactionally claimed jobs coordinate execution; interrupted running actions become uncertain and require review, rather than being blindly replayed. No database transaction stays open during an AI call or infrastructure operation.

Neon stores incident state, evidence snapshots/references, proposals, approval events, job leases, action outcomes, and ordered timeline events. The API/worker use the pooled URL; migrations use the direct URL. Require TLS and small connection pools. Without durable state, the worker must not start new mutations.

## Configuring another target

V1 ships one isolated Docker demo connector in the `zenkai` Compose project. `TARGET_NAME` changes its display name and `LOKI_SELECTOR` selects its configured log stream. Telemetry endpoints and registered topology are backend configuration.

Connecting a real university portal requires authorised log/metric access, expected response checks, and an action connector scoped to that infrastructure. Renaming the demo or pointing a log selector at another service does not automatically grant recovery capabilities. Vendor-specific adapters and multi-target administration are future work. Keep the trusted Docker worker local; the Docker socket grants significant host control and is never mounted into the browser or general API service.

## Verification

```bash
uv sync --frozen --python 3.12
uv run pytest -q
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
docker compose config --quiet
```

The PostgreSQL integration tests require `TEST_DATABASE_URL` pointing only to a disposable local database named `zenkai_test`; they never use Neon. Their URL guard prevents accidentally cleaning an application database. Without that test configuration, the PostgreSQL-specific cases are skipped.

The explicit container harness, [scripts/verify_lab.py](scripts/verify_lab.py), tests all four recovery paths using real Docker operations, Loki, Prometheus, and k6 with a labelled fixture model. It must use the isolated `zenkai-test-db/zenkai_test` database. Stop the regular worker during this harness so the two workers do not operate on the same target; restart it afterward. The harness requires zero dropped k6 iterations and records measured requests during each recovery window. These are integration-test results, not a live OpenRouter demonstration.

See [observability/README.md](observability/README.md) for telemetry details. Follow [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md): finish one coherent change, verify it, commit it, and push before the next milestone. Never commit credentials, local data, or generated logs.
