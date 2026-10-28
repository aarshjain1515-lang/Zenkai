"""Run only against the explicitly configured, disposable local PostgreSQL test DB."""
import asyncio
import os
import time

import httpx
import pytest
from sqlalchemy import delete, select

from zenkai.api import create_app
from zenkai.config import Settings
from zenkai.db import Database, Incident, Job, Preference, Timeline

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="Set TEST_DATABASE_URL for disposable PostgreSQL integration tests")


@pytest.fixture
async def db():
    assert "127.0.0.1" in URL and URL.endswith("/zenkai_test"), "Use only the disposable local zenkai_test database"
    database = Database(URL)
    assert await database.ready(), "Run Alembic against the disposable database first"
    async with database.sessions() as session, session.begin():
        for table in (Job, Timeline, Incident, Preference):
            await session.execute(delete(table))
    yield database
    await database.engine.dispose()


async def test_job_claims_are_unique_across_concurrent_consumers(db):
    async with db.sessions() as session, session.begin():
        for _ in range(3):
            session.add(Job(kind="test", payload={}))
    jobs = await asyncio.gather(db.claim(), db.claim(), db.claim())
    assert len({job.id for job in jobs}) == 3
    assert all(job.status == "running" and job.lease_token for job in jobs)
    assert await db.claim() is None


async def test_approval_is_bound_and_cannot_be_replayed(db):
    async with db.sessions() as session, session.begin():
        item = Incident(title="Fixture incident", status="awaiting_approval", proposal_hash="exact-fixture-hash")
        session.add(item)
        await session.flush()
        incident_id = item.id
    config = Settings(_env_file=None, database_url=URL, operator_password="test-password", session_secret="test-session")
    app = create_app(config, db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        csrf = (await client.post("/api/login", json={"password": "test-password"})).json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        endpoint = f"/api/incidents/{incident_id}/approval"
        assert (await client.post(endpoint, json={"proposal_hash": "wrong-hash", "approve": True}, headers=headers)).status_code == 409
        body = {"proposal_hash": "exact-fixture-hash", "approve": True}
        assert (await client.post(endpoint, json=body, headers=headers)).status_code == 200
        assert (await client.post(endpoint, json=body, headers=headers)).status_code == 409
    async with db.sessions() as session:
        jobs = (await session.scalars(select(Job))).all()
        assert len(jobs) == 1 and jobs[0].action_key == "exact-fixture-hash"


async def test_interrupted_external_action_is_not_blindly_retried(db):
    from zenkai.worker import Worker
    config = Settings(_env_file=None, database_url=URL)
    worker = Worker(config)
    async with db.sessions() as session, session.begin():
        item = Incident(title="Interrupted fixture", status="executing")
        session.add(item)
        await session.flush()
        session.add(Job(kind="execute", payload={"incident_id": item.id}, status="running", lease_until=time.time() - 20))
    await worker.reconcile_expired()
    async with db.sessions() as session:
        assert (await session.scalar(select(Job))).status == "uncertain"
        assert (await session.scalar(select(Incident))).status == "escalated"
    await worker.db.engine.dispose()
