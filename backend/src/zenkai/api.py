import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .connector import probe
from .db import Database, Incident, Job, Preference, Timeline, incident_dict, record
from .domain import Proposal, validate_proposal


def create_app(config=None, database=None):
    config = config or settings()
    db = database or Database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if db.engine:
            await db.engine.dispose()

    app = FastAPI(title="Zenkai control plane", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret or secrets.token_hex(32),
                       same_site="strict", https_only=False)
    app.state.db = db

    def operator(request: Request):
        if not request.session.get("operator"):
            raise HTTPException(401, "Sign in to control the recovery lab")
        if request.method != "GET" and not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""), request.session.get("csrf", "invalid")):
            raise HTTPException(403, "CSRF validation failed")

    async def require_database():
        if not await db.ready():
            raise HTTPException(503, "Configure Neon and run the database migration before using recovery controls")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "zenkai-api"}

    @app.get("/api/session")
    async def session(request: Request):
        return {"authenticated": bool(request.session.get("operator")), "csrf": request.session.get("csrf")}

    class Login(BaseModel):
        password: str

    @app.post("/api/login")
    async def login(body: Login, request: Request):
        if not config.operator_password or not secrets.compare_digest(body.password, config.operator_password):
            raise HTTPException(401, "Incorrect operator password")
        request.session.clear()
        request.session.update(operator=True, csrf=secrets.token_urlsafe(24))
        return {"authenticated": True, "csrf": request.session["csrf"]}

    @app.post("/api/logout", dependencies=[Depends(operator)])
    async def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    async def query_metrics():
        queries = {
            "rps": 'sum(rate(zenkai_portal_requests_total[1m]))',
            "error_rate": 'sum(rate(zenkai_portal_requests_total{outcome!="success"}[1m])) / clamp_min(sum(rate(zenkai_portal_requests_total[1m])), 0.001)',
            "latency": 'histogram_quantile(0.95, sum(rate(zenkai_portal_request_seconds_bucket[1m])) by (le)) * 1000',
        }
        result = {}
        async with httpx.AsyncClient(timeout=3) as client:
            for key, query in queries.items():
                try:
                    response = await client.get(f"{config.prometheus_url}/api/v1/query", params={"query": query})
                    rows = response.json()["data"]["result"]
                    value = float(rows[0]["value"][1]) if rows else 0
                    result[key] = value if value == value and abs(value) != float("inf") else 0
                except Exception:
                    result[key] = None
        return result

    @app.get("/api/state")
    async def state(request: Request):
        portal, database_ready, metrics = await asyncio.gather(probe(config), db.ready(), query_metrics())
        incidents, timeline, worker, load, autopilot = [], [], {}, {}, config.auto_approve_safe
        if database_ready:
            async with db.sessions() as session:
                items = (await session.scalars(select(Incident).order_by(Incident.created_at.desc()).limit(20))).all()
                for item in items:
                    value = incident_dict(item)
                    if not request.session.get("operator"):
                        value["evidence"] = []
                    incidents.append(value)
                rows = (await session.scalars(select(Timeline).order_by(Timeline.id.desc()).limit(40))).all()
                timeline = [{"id": x.id, "incident_id": x.incident_id, "created_at": x.created_at,
                             "kind": x.kind, "message": x.message, "payload": x.payload} for x in reversed(rows)]
                heartbeat = await session.get(Preference, "worker")
                worker = heartbeat.value if heartbeat else {}
                load_row = await session.get(Preference, "load")
                load = load_row.value if load_row else {}
                preference = await session.get(Preference, "autopilot")
                autopilot = preference.value["enabled"] if preference else autopilot
        return {"portal": portal, "metrics": metrics, "incidents": incidents, "timeline": timeline,
                "worker": worker, "load": load, "autopilot": autopilot, "now": time.time(),
                "setup": {"database": database_ready, "model": bool(config.openrouter_api_key),
                          "missing": config.missing(), "model_name": config.openrouter_model}}

    @app.get("/api/events", dependencies=[Depends(operator)])
    async def events(request: Request):
        try:
            cursor = int(request.headers.get("Last-Event-ID", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                if await db.ready():
                    async with db.sessions() as session:
                        rows = (await session.scalars(select(Timeline).where(Timeline.id > cursor)
                                                      .order_by(Timeline.id).limit(50))).all()
                        for row in rows:
                            cursor = row.id
                            yield f"id: {row.id}\ndata: {json.dumps({'kind': row.kind})}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    class Control(BaseModel):
        kind: str

    @app.post("/api/lab", dependencies=[Depends(operator), Depends(require_database)])
    async def control(body: Control):
        allowed = {"upstream", "schema", "overload", "crash", "reset", "load_start", "load_stop"}
        if body.kind not in allowed:
            raise HTTPException(422, "Unknown lab control")
        async with db.sessions() as session, session.begin():
            # Serialise lab commands across API requests, using transaction-level locking.
            from sqlalchemy import text
            await session.execute(text("SELECT pg_advisory_xact_lock(928174)"))
            pending = await session.scalar(select(Job).where(Job.status.in_(["queued", "running"])).limit(1))
            if pending:
                raise HTTPException(409, "An operation is already in progress")
            active = await session.scalar(select(Incident).where(Incident.active_key == "campus"))
            if active and body.kind in {"upstream", "schema", "overload", "crash"}:
                raise HTTPException(409, "Reset or resolve the current incident before injecting another fault")
            kind = "fault" if body.kind in {"upstream", "schema", "overload", "crash"} else body.kind
            job = Job(kind=kind, payload={"fault": body.kind})
            session.add(job)
            record(session, "control", f"Operator queued {body.kind.replace('_', ' ')}", actor="operator")
        return {"queued": True}

    class Approval(BaseModel):
        proposal_hash: str
        approve: bool

    @app.post("/api/incidents/{incident_id}/approval", dependencies=[Depends(operator), Depends(require_database)])
    async def approve(incident_id: str, body: Approval):
        async with db.sessions() as session, session.begin():
            item = await session.get(Incident, incident_id, with_for_update=True)
            if not item or item.status != "awaiting_approval" or item.proposal_hash != body.proposal_hash:
                raise HTTPException(409, "Proposal is stale or has already been decided")
            if body.approve:
                item.status = "queued"
                session.add(Job(kind="execute", payload={"incident_id": item.id}, action_key=item.proposal_hash))
            else:
                item.status = "blocked"
            record(session, "approved" if body.approve else "rejected", "Operator approved the exact proposal" if body.approve else "Operator rejected the repair", item.id,
                   proposal_hash=body.proposal_hash, actor="operator")
        return {"ok": True}

    class Autopilot(BaseModel):
        enabled: bool

    @app.post("/api/autopilot", dependencies=[Depends(operator), Depends(require_database)])
    async def autopilot(body: Autopilot):
        async with db.sessions() as session, session.begin():
            preference = await session.get(Preference, "autopilot")
            if preference:
                preference.value = {"enabled": body.enabled}
            else:
                session.add(Preference(key="autopilot", value={"enabled": body.enabled}))
            record(session, "policy", "Autonomous recovery enabled" if body.enabled else "Manual approval required for new proposals")
        return {"ok": True}

    @app.post("/api/unsafe-proposal", dependencies=[Depends(operator), Depends(require_database)])
    async def unsafe_proposal():
        proposal = Proposal(root_cause="Injected unsafe proposal for policy demonstration", evidence_ids=["test"],
                            action="restore_upstream", target="gateway", upstream_port=22, schema_field=None,
                            capacity=None, verification_plan="This injected unsafe proposal must never execute")
        try:
            validate_proposal(proposal, [{"id": "test"}], {"gateway": {"upstream_port": 8999}})
        except ValueError as error:
            async with db.sessions() as session, session.begin():
                record(session, "blocked", "Injected unsafe proposal blocked; no infrastructure action executed", reason=str(error), injected=True)
            return {"blocked": True, "reason": str(error)}
        raise HTTPException(500, "Unsafe proposal unexpectedly passed")

    return app


app = create_app()
