import asyncio
import time
import uuid

import docker
import httpx
from sqlalchemy import select

from .config import settings
from .connector import Connector, probe
from .db import Database, Incident, Job, Preference, record
from .domain import proposal_hash, validate_proposal, Proposal
from .intelligence import collect_evidence, diagnose
from .logging import configure, event


class Worker:
    def __init__(self, config):
        self.config, self.db = config, Database(config.database_url)
        self.connector = Connector(config)
        self.logger = configure("worker", config.log_dir)
        self.identity = uuid.uuid4().hex
        self.owns_lease = False

    async def heartbeat(self):
        while True:
            try:
                if await self.db.ready():
                    async with self.db.sessions() as session, session.begin():
                        row = await session.get(Preference, "worker", with_for_update=True)
                        now = time.time()
                        if row and row.value.get("owner") != self.identity and row.value.get("lease_until", 0) > now:
                            self.owns_lease = False
                        else:
                            value = {"owner": self.identity, "timestamp": now, "lease_until": now + 20}
                            if row:
                                row.value = value
                            else:
                                session.add(Preference(key="worker", value=value))
                            self.owns_lease = True
                else:
                    self.owns_lease = False
            except Exception:
                self.owns_lease = False
            await asyncio.sleep(4)

    async def assert_lease(self):
        async with self.db.sessions() as session:
            row = await session.get(Preference, "worker")
            if not row or row.value.get("owner") != self.identity or row.value.get("lease_until", 0) <= time.time():
                raise ValueError("Worker lost its execution lease; no new action is authorised")

    async def update(self, incident_id, status, message, **payload):
        async with self.db.sessions() as session, session.begin():
            item = await session.get(Incident, incident_id)
            item.status = status
            record(session, status, message, incident_id, **payload)

    async def detect(self):
        observation = await probe(self.config)
        if observation["healthy"]:
            return
        async with self.db.sessions() as session:
            if await session.scalar(select(Incident).where(Incident.active_key == "campus")):
                return
        await asyncio.sleep(1)
        if (await probe(self.config))["healthy"]:
            return
        snapshot = await self.connector.snapshot()
        async with self.db.sessions() as session, session.begin():
            item = Incident(title="Campus portal request workflow interrupted", snapshot=snapshot)
            session.add(item)
            await session.flush()
            session.add(Job(kind="diagnose", payload={"incident_id": item.id, "probe": observation}))
            record(session, "detected", "Failed request and repeat probe confirmed a service incident", item.id,
                   status_code=observation["status_code"])
            event(self.logger, "error", "Campus portal request failed; incident opened", incident_id=item.id,
                  error_code="PORTAL_UNAVAILABLE")

    async def diagnosis(self, job):
        incident_id = job.payload["incident_id"]
        await self.update(incident_id, "diagnosing", "Collecting Loki logs, metric context, and registered configuration")
        async with self.db.sessions() as session:
            item = await session.get(Incident, incident_id)
            snapshot = item.snapshot
        await asyncio.sleep(3)  # Allow file collection to reach Loki, rather than substituting local logs.
        evidence = await collect_evidence(self.config, snapshot, job.payload["probe"])
        async with self.db.sessions() as session, session.begin():
            item = await session.get(Incident, incident_id)
            item.evidence = evidence
            record(session, "evidence", "Evidence bundle captured from live observability sources", incident_id,
                   loki_lines=sum(x["source"] == "loki" for x in evidence))
        proposal = await diagnose(self.config, evidence)
        checks = validate_proposal(proposal, evidence, snapshot)
        if proposal.action == "restore_upstream":
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(f"{self.config.portal_url}/health")
                response.raise_for_status()
            checks.append({"name": "Registered backend readiness probe", "passed": True})
        digest = proposal_hash(proposal.model_dump(), snapshot)
        async with self.db.sessions() as session, session.begin():
            item = await session.get(Incident, incident_id)
            item.proposal, item.proposal_hash = proposal.model_dump(), digest
            item.checks, item.model = checks, self.config.openrouter_model
            preference = await session.get(Preference, "autopilot")
            automatic = preference.value["enabled"] if preference else self.config.auto_approve_safe
            record(session, "proposed", "OpenRouter returned a structured, evidence-linked recovery proposal", incident_id,
                   model=self.config.openrouter_model, proposal_hash=digest)
            record(session, "validated", "Independent action policy and dry-run checks passed", incident_id, checks=checks)
            item.status = "queued" if automatic else "awaiting_approval"
            if automatic:
                session.add(Job(kind="execute", payload={"incident_id": incident_id}, action_key=digest))
                record(session, "approved", "Pre-authorised demo action passed autonomous recovery policy", incident_id,
                       actor="policy", proposal_hash=digest)
            else:
                record(session, "awaiting_approval", "Waiting for operator approval of this exact proposal", incident_id)

    async def execution(self, job):
        incident_id = job.payload["incident_id"]
        async with self.db.sessions() as session:
            item = await session.get(Incident, incident_id)
            if item.status not in ("queued", "executing"):
                raise ValueError("Incident no longer authorises this action")
            proposal = Proposal.model_validate(item.proposal)
            original, evidence, digest = item.snapshot, item.evidence, item.proposal_hash
        current = await self.connector.snapshot()
        if current != original or proposal_hash(proposal.model_dump(), current) != digest:
            raise ValueError("Configuration changed after diagnosis; stale proposal blocked")
        validate_proposal(proposal, evidence, current)
        await self.assert_lease()
        await self.update(incident_id, "executing", "Executing the validated action on the registered target", action=proposal.action)
        await self.connector.execute(proposal)
        await self.update(incident_id, "verifying", "Replaying correct-content probes while the load test continues")
        await asyncio.sleep(5)  # Let post-repair metric samples replace the fault window.
        observations = []
        for _ in range(self.config.verification_samples):
            await asyncio.sleep(self.config.verification_interval)
            sample = await probe(self.config)
            observations.append(sample)
            if not sample["healthy"] or sample["latency_ms"] > 1500:
                raise ValueError("Post-repair request failed the recovery observation window")
        load = await asyncio.to_thread(self.load_status)
        measurements = {}
        if load["running"]:
            async with httpx.AsyncClient(timeout=5) as client:
                for key, expression in {
                    "requests": 'sum(increase(zenkai_portal_requests_total[10s]))',
                    "errors": 'sum(increase(zenkai_portal_requests_total{outcome!="success"}[10s]))',
                }.items():
                    response = await client.get(f"{self.config.prometheus_url}/api/v1/query", params={"query": expression})
                    response.raise_for_status()
                    values = response.json()["data"]["result"]
                    measurements[key] = float(values[0]["value"][1]) if values else 0
            if measurements["requests"] < 20 or measurements["errors"] / max(measurements["requests"], 1) > .05:
                raise ValueError("Sustained-load verification failed: insufficient measured traffic or excessive errors")
        async with self.db.sessions() as session, session.begin():
            item = await session.get(Incident, incident_id)
            item.status, item.active_key, item.recovered_at = "recovered", None, time.time()
            record(session, "recovered", "Correct portal responses sustained across the verification window", incident_id,
                   samples=observations, under_load=load["running"], measurements=measurements,
                   duration_seconds=round(item.recovered_at - item.created_at, 2))
        event(self.logger, "info", "Incident recovered and verified", incident_id=incident_id, action=proposal.action)

    def load_status(self):
        client = docker.from_env()
        try:
            container = client.containers.get("zenkai-load")
            if container.labels.get("zenkai.role") != "load":
                raise ValueError("Load container is outside the lab")
            return {"running": container.status == "running", "status": container.status}
        except docker.errors.NotFound:
            return {"running": False, "status": "not_started"}

    def control_load(self, start):
        client = docker.from_env()
        try:
            container = client.containers.get("zenkai-load")
            if container.labels.get("zenkai.role") != "load":
                raise ValueError("Unregistered load container")
        except docker.errors.NotFound:
            if not start:
                return
            # The image is prepared by the setup command, not chosen by model output.
            container = client.containers.create("grafana/k6:1.3.0", name="zenkai-load", user="0",
                command=["run", "-o", "experimental-prometheus-rw", "/scripts/traffic.js"],
                network="zenkai_default", labels={"zenkai.role": "load"},
                environment={"TARGET_URL": "http://gateway", "DURATION": "3m", "RATE": "25",
                             "K6_PROMETHEUS_RW_SERVER_URL": "http://prometheus:9090/api/v1/write",
                             "K6_PROMETHEUS_RW_TREND_STATS": "p(95),avg"},
                volumes={"zenkai_load-scripts": {"bind": "/scripts", "mode": "ro"},
                         "zenkai_load-results": {"bind": "/results", "mode": "rw"}})
        if start and container.status != "running":
            container.start()
        elif not start and container.status == "running":
            container.stop(timeout=3)

    async def process(self, job):
        try:
            await self.assert_lease()
            if job.kind == "diagnose":
                await self.diagnosis(job)
            elif job.kind == "execute":
                await self.execution(job)
            elif job.kind == "fault":
                event(self.logger, "error", "Operator injected a controlled service fault", fault=job.payload["fault"], injected=True)
                await self.connector.inject(job.payload["fault"])
            elif job.kind == "reset":
                await self.connector.reset()
                async with self.db.sessions() as session, session.begin():
                    items = (await session.scalars(select(Incident).where(Incident.active_key == "campus"))).all()
                    for item in items:
                        item.active_key, item.status = None, "reset"
                        record(session, "reset", "Operator reset the lab; this is not an AI recovery", item.id)
            elif job.kind in ("load_start", "load_stop"):
                await asyncio.to_thread(self.control_load, job.kind == "load_start")
            async with self.db.sessions() as session, session.begin():
                current = await session.get(Job, job.id)
                current.status = "done"
                record(session, "operation", f"{job.kind.replace('_', ' ')} completed", job.payload.get("incident_id"))
        except Exception as error:
            # Never persist arbitrary HTTP response bodies or connection-string exceptions.
            reason = str(error)[:300] if isinstance(error, ValueError) else f"{type(error).__name__}: operation failed; inspect service logs"
            event(self.logger, "error", "Recovery operation stopped", job_id=job.id, reason=reason)
            async with self.db.sessions() as session, session.begin():
                current = await session.get(Job, job.id)
                current.status, current.error = "failed", reason
                incident_id = job.payload.get("incident_id")
                if incident_id:
                    item = await session.get(Incident, incident_id)
                    item.status, item.error = "escalated", reason
                record(session, "escalated", reason, incident_id)

    async def reconcile_expired(self):
        async with self.db.sessions() as session, session.begin():
            rows = (await session.scalars(select(Job).where(Job.status == "running", Job.lease_until < time.time())
                                          .with_for_update(skip_locked=True))).all()
            for job in rows:
                job.status, job.error = "uncertain", "Worker interrupted; reconcile target before an explicit lab reset"
                if job.payload.get("incident_id"):
                    item = await session.get(Incident, job.payload["incident_id"])
                    item.status, item.error = "escalated", job.error
                record(session, "escalated", job.error, job.payload.get("incident_id"))

    async def run(self):
        heartbeat = asyncio.create_task(self.heartbeat())
        event(self.logger, "info", "Recovery worker started; waiting for configured Neon state")
        try:
            while True:
                try:
                    if self.owns_lease and await self.db.ready():
                        await self.reconcile_expired()
                        job = await self.db.claim()
                        if job:
                            await self.process(job)
                        else:
                            await self.detect()
                        load = await asyncio.to_thread(self.load_status)
                        async with self.db.sessions() as session, session.begin():
                            row = await session.get(Preference, "load")
                            if row:
                                row.value = load
                            else:
                                session.add(Preference(key="load", value=load))
                except Exception as error:
                    event(self.logger, "error", "Worker cycle paused", error_code=type(error).__name__)
                await asyncio.sleep(self.config.poll_seconds)
        finally:
            heartbeat.cancel()
            if self.db.engine:
                await self.db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(Worker(settings()).run())
