# Temporary EC2 deployment

This deployment runs the complete V1 on a dedicated Linux host with Docker
Engine and the Compose plugin. Allow roughly 2 vCPUs, 4 GB RAM, and 20 GB disk.
It is intended for a temporary hackathon demonstration, not an HA service.

## Public addresses

For `ZENKAI_DOMAIN=synapes-dev.online`, create these DNS records using the
instance's public IPv4 address:

| Type | Name | Value |
| --- | --- | --- |
| A | @ | EC2 public IPv4 |
| A | demo | EC2 public IPv4 |
| A | grafana | EC2 public IPv4 |

Remove conflicting address records for those names only. Preserve email and
other unrelated records. Do not add AAAA records without working IPv6 routing.
If the zone returns SERVFAIL, fix domain delegation/DNSSEC with the registrar
before waiting for certificate issuance.

Allow inbound TCP 80 and 443 publicly and SSH 22 from the operator's IP only.
All existing app and telemetry host ports bind to loopback. Caddy terminates
TLS, redirects HTTP, renews certificates, and streams API events directly.
Grafana requires its generated administrator password.

## Install and deploy

Clone this repository to `/opt/zenkai` on the dedicated host, then:

```bash
cd /opt/zenkai
python3 scripts/init_local.py
```

Set these values in the ignored, mode-0600 `.env` on that host:

```dotenv
ZENKAI_DOMAIN=synapes-dev.online
POSTGRES_PASSWORD=<new random URL-safe password>
OPENROUTER_API_KEY=<your server-side API key>
OPENROUTER_MODEL=nvidia/nemotron-3.5-super-vl:free
```

The initializer generates the operator, session, lab, and Grafana secrets.
Keep them separate from the local demo's secrets. The deployment overlay uses
its own PostgreSQL container and persistent volume instead of local Neon
credentials, preventing two workers from controlling different Docker hosts
through the same job queue. Database and AI settings stay on the server;
the only frontend build arguments are public website URLs.

```bash
bash scripts/deploy.sh
```

The command checks configuration, builds the locked frontend and backend,
prepares the k6 image, starts PostgreSQL, applies migrations, starts services,
and checks the API. Domain verification and live recovery checks are separate.
The project name must remain `zenkai` because the connector has registered
container names and the load runner uses that network and those volumes.
Use this deployment only on a dedicated host: its worker mounts Docker's socket.

The `portal` service deliberately has no restart policy. The failure lab's
application-crash scenario must be recovered by the worker, not by Docker.

## Verify the demo

1. Open the console over HTTPS and confirm database, model key, worker heartbeat,
   and telemetry readiness. A configured key alone does not prove inference works.
2. Check the demo link opens `https://demo.synapes-dev.online/campus` and the
   observability link opens `https://grafana.synapes-dev.online/d/zenkai-overview`.
3. Sign in with this host's `OPERATOR_PASSWORD`. Check live event updates.
4. Start bounded k6 traffic in Failure lab, then inject an upstream failure.
5. Confirm evidence collection, a live AI proposal, policy checks, and measured
   successful requests after repair before describing it as recovered.

Useful host commands:

```bash
docker compose -f compose.yaml -f compose.deploy.yaml ps
docker compose -f compose.yaml -f compose.deploy.yaml logs --tail=80 api worker caddy
curl --fail https://synapes-dev.online/api/health
curl --fail https://demo.synapes-dev.online/api/portal
```

## Manual stop and cleanup

The instance keeps running until the owner stops it. No automatic shutdown is
required. When the demonstration is finished, stop the dedicated instance from
the EC2 console or run this on that host:

```bash
sudo poweroff
```

EC2 must use instance-initiated shutdown behavior `stop`. This stops compute;
the disk remains billable and the public IP can change on restart. Export any
needed evidence, then terminate the temporary instance and delete its dedicated
security group and imported public key when finished. Apply this cleanup only
to the dedicated temporary demo resources.

Stopping/recreating the stack preserves its database and telemetry volumes.
Do not use `docker compose down -v` unless intentionally deleting that data.

Sources: [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https),
[Docker Engine for Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[EC2 security groups](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html).
