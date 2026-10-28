"""Explicit integration test: real Docker/Loki/Prometheus/k6, fixture model, disposable PG.

Run inside the worker container with TEST_DATABASE_URL pointing only to the disposable
zenkai-test-db/zenkai_test database. It never substitutes a model in the running app.
"""
import asyncio
import os
from unittest.mock import patch

from sqlalchemy import delete, select

from zenkai.config import Settings
from zenkai.connector import probe
from zenkai.db import Incident, Job, Preference, Timeline
from zenkai.domain import Proposal
from zenkai.worker import Worker


async def fixture_model(config, evidence):
    assert any(item["source"] == "loki" for item in evidence), "Real Loki evidence is required in this test"
    snapshot = next(item["data"] for item in evidence if item["id"] == "configuration")
    proposal = {"root_cause": "Explicit integration-test fixture; not a live model diagnosis",
                "evidence_ids": [next(item["id"] for item in evidence if item["source"] == "loki")],
                "target": "portal", "upstream_port": None, "schema_field": None, "capacity": None,
                "verification_plan": "Require correct responses and healthy measured traffic during the recovery window"}
    if snapshot["gateway"]["upstream_port"] != 8000:
        proposal.update(action="restore_upstream", target="gateway", upstream_port=8000)
    elif not snapshot["portal"]["running"]:
        proposal.update(action="restart_service")
    elif snapshot["portal"]["provider_schema"] == "v2":
        proposal.update(action="patch_schema_mapping", schema_field="offerings")
    else:
        proposal.update(action="set_capacity", capacity=24)
    return Proposal(**proposal)


async def run():
    url = os.environ.get("TEST_DATABASE_URL", "")
    assert "@zenkai-test-db:5432/zenkai_test" in url, "Use the disposable local test DB, never Neon"
    config = Settings(database_url=url, openrouter_api_key="fixture-not-a-real-key",
                      openrouter_model="fixture/test-only", auto_approve_safe=True)
    worker = Worker(config)
    assert await worker.db.ready()
    async with worker.db.sessions() as session, session.begin():
        for table in (Job, Timeline, Incident, Preference):
            await session.execute(delete(table))
    heartbeat = asyncio.create_task(worker.heartbeat())
    try:
        for _ in range(10):
            if worker.owns_lease:
                break
            await asyncio.sleep(1)
        assert worker.owns_lease
        await worker.connector.reset()
        await asyncio.to_thread(worker.control_load, True)
        print("TEST MODE: fixture model + disposable PostgreSQL; all target/telemetry/load operations are real", flush=True)
        for fault in ("upstream", "schema", "crash", "overload"):
            await worker.connector.reset()
            await asyncio.sleep(3)
            assert (await probe(config))["healthy"]
            await worker.assert_lease()
            await worker.connector.inject(fault)
            for _ in range(12):
                await worker.detect()
                async with worker.db.sessions() as session:
                    item = await session.scalar(select(Incident).where(Incident.active_key == "campus"))
                    if item:
                        break
                await asyncio.sleep(1)
            assert item, f"Fault was not detected: {fault}"
            with patch("zenkai.worker.diagnose", fixture_model):
                for _ in range(2):
                    job = await worker.db.claim()
                    assert job, "Expected a durable diagnosis/execution job"
                    await worker.process(job)
            async with worker.db.sessions() as session:
                result = await session.get(Incident, item.id)
                assert result.status == "recovered", (fault, result.status, result.error)
                recovery = await session.scalar(select(Timeline).where(Timeline.incident_id == item.id,
                                                                         Timeline.kind == "recovered"))
                assert recovery.payload["under_load"], "Traffic stopped before recovery verification"
                print(f"PASS {fault}: {recovery.payload['duration_seconds']}s; "
                      f"{recovery.payload['measurements']['requests']:.0f} measured requests in verification window", flush=True)
    finally:
        await worker.connector.reset()
        await asyncio.to_thread(worker.control_load, False)
        heartbeat.cancel()
        await worker.db.engine.dispose()


asyncio.run(run())
