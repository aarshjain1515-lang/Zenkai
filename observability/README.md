# Local observability

This page documents the development observability services used by Zenkai V1. The full Compose application also includes the independent recovery tool and a separate demo target; see the root README for application and credential setup.

## Start

Requirements: Docker Engine and the Docker Compose plugin. Run from the repository root:

```bash
python3 scripts/init_local.py
# Preserve the generated .env; add Neon and OpenRouter credentials there.
bash scripts/start.sh
```

Open [Grafana](http://localhost:3300/d/zenkai-overview), or the [live demo dashboard](https://grafana.synapes-dev.online/d/zenkai-overview). No sign-in or password is needed with the default `DEMO_ACCESS=true`: visitors can view dashboards and explore the provisioned Prometheus and Loki data sources anonymously. Administrative maintenance still uses the generated `admin` credential in `.env`; existing volumes preserve that account's original password.

| Service | Local address | Readiness |
| --- | --- | --- |
| Grafana | http://localhost:3300 | `/api/health` |
| Prometheus | http://localhost:9090 | `/-/ready` |
| Loki | http://localhost:3100 | `/ready` |
| Alloy | http://localhost:12345 | `/-/ready` |

All published ports bind to the local machine only. Loki authentication is disabled for this isolated demo. Do not expose this configuration directly to a public network. Grafana's automatic plugin preinstallation and updates are disabled to avoid extra downloads during demo startup.

## Verify logs and metrics

Append one fictional event (this tests collection, not AI healing):

```bash
printf '%s\n' '{"level":"info","service":"zenkai-demo","event_id":"observability-smoke","step":"telemetry","outcome":"received","message":"Zenkai log pipeline is connected"}' >> logs/demo.log
```

Allow a few seconds for collection. In the provisioned dashboard, verify the event appears and the four Prometheus scrape targets (the demo target is expected to go down during a process-crash test) have value `1`.

In Grafana Explore, useful queries are:

```promql
up
```

```logql
{service_name="zenkai-demo"} | json
```

Filter an incident by a JSON field rather than making its ID a label:

```logql
{service_name="zenkai-demo"} | json | incident_id="incident-demo-001"
```

Inspect service logs and configuration:

```bash
docker compose ps
docker compose logs --tail=50
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec alloy alloy validate /etc/alloy/config.alloy
```

## Application integration

- Write newline-delimited JSON into `logs/*.log`; Alloy mounts this directory read-only and forwards lines to Loki.
- The demo service exposes `/metrics` and is scraped as `university-demo`. Add network-reachable targets for additional registered services. Container `localhost` is the container itself, not the host.
- Use low-cardinality metric labels such as service, action type, and outcome. Do not label by student, event, or incident ID.
- Preserve incident, proposal, approval, and action state in a durable store; telemetry supports the investigation and does not replace the recovery audit trail.
- Add application availability, error rate, latency, resource pressure, action rejection, and verified-recovery panels only when those metrics are actually emitted.

Alloy checkpoints and service data persist in Docker volumes. Prometheus retains three days of metrics. Loki currently has no automatic log-retention policy; use small fictional demo data and clean up deliberately when finished.

## k6 traffic

Use the Zenkai failure lab after connecting Neon and signing in, or start the prepared workload directly:

```bash
docker compose --profile load up -d load
docker compose logs --tail=30 load
```

The bounded workload sends 25 requests/second for three minutes, checks response content, and exports real k6 metrics to Prometheus. Stop it after the demo with `docker compose stop load`. The summary is stored in the `load-results` volume at `/results/latest.json` inside `zenkai-load`.

Intentional outages produce failed response checks. The load-generation threshold requires zero dropped iterations; recovery is evaluated independently by the worker over a post-repair observation window. The explicit integration harness uses a labelled fixture model, never a live-model substitute in the running application.

## Stop

```bash
docker compose down
```

This keeps persisted service data. `docker compose down -v` also deletes the stack's volumes, including Grafana settings, stored telemetry, demo target state, gateway configuration, and load results; use it only when you intend to reset this demo. Host files under `logs/` remain.

## References

- [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Loki configuration](https://grafana.com/docs/loki/latest/configure/)
- [Alloy file collection](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/)
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)
