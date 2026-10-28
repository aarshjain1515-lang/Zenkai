"""An intentionally faultable university service. No production university data."""
import asyncio
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .logging import configure, event

app = FastAPI(title="Northbridge University · Demonstration service")
config = settings()
logger = configure("portal", config.log_dir)
requests = Counter("zenkai_portal_requests_total", "Portal requests", ["outcome"])
latency = Histogram("zenkai_portal_request_seconds", "Portal latency", buckets=(.05, .1, .2, .5, 1, 2, 5))
state_path = Path(os.environ.get("PORTAL_STATE_FILE", "runtime/portal.json"))
inflight = 0
DEFAULTS = {"revision": 1, "provider_schema": "v1", "adapter_field": "services", "capacity": 24}
SERVICES = [
    {"name": "Student records", "category": "Academic", "description": "Your academic profile, securely connected.", "icon": "graduation"},
    {"name": "Campus library", "category": "Resources", "description": "Books, journals, and spaces to think.", "icon": "book"},
    {"name": "Examinations", "category": "Academic", "description": "Schedules, hall tickets, and published results.", "icon": "calendar"},
    {"name": "Campus network", "category": "Infrastructure", "description": "Network access and connected campus services.", "icon": "wifi"},
]


def read_state():
    return json.loads(state_path.read_text()) if state_path.exists() else dict(DEFAULTS)


def write_state(value):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(state_path)


def authorised(token):
    if not config.lab_token or not token or not secrets.compare_digest(token, config.lab_token):
        raise HTTPException(403, "Lab control requires the configured internal token")


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    provider_schema: str | None = None
    adapter_field: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=24)
    reset: bool = False


@app.get("/health")
async def health():
    return {"status": "ok", "service": "portal", "demo": True}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/internal/state")
async def internal_state(x_lab_token: str | None = Header(default=None)):
    authorised(x_lab_token)
    return read_state()


@app.post("/internal/config")
async def mutate(body: Mutation, x_lab_token: str | None = Header(default=None)):
    authorised(x_lab_token)
    state = read_state()
    if body.revision != state["revision"]:
        raise HTTPException(409, "Configuration revision changed")
    changes = body.model_dump(exclude_none=True, exclude={"revision", "reset"})
    if changes.get("provider_schema", "v1") not in ("v1", "v2") or changes.get("adapter_field", "services") not in ("services", "offerings"):
        raise HTTPException(422, "Unsupported demo contract")
    state = (dict(DEFAULTS) if body.reset else state | changes) | {"revision": state["revision"] + 1}
    write_state(state)
    event(logger, "info", "Service configuration updated", revision=state["revision"], configuration=state)
    return state


@app.get("/api/portal")
async def portal():
    global inflight
    started = time.monotonic()
    state = read_state()
    if inflight >= state["capacity"]:
        requests.labels("capacity_rejected").inc()
        event(logger, "error", "Worker capacity exhausted; request rejected", error_code="CAPACITY_EXHAUSTED",
              capacity=state["capacity"], inflight=inflight)
        raise HTTPException(503, "Portal capacity exhausted")
    inflight += 1
    try:
        await asyncio.sleep(.15)
        provider_payload = {"offerings" if state["provider_schema"] == "v2" else "services": SERVICES}
        if state["adapter_field"] not in provider_payload:
            requests.labels("schema_error").inc()
            event(logger, "error", "Directory integration rejected unexpected provider data", error_code="SCHEMA_MISMATCH",
                  expected_field=state["adapter_field"], observed_fields=list(provider_payload), revision=state["revision"])
            raise HTTPException(502, "Directory integration received an unexpected data format")
        requests.labels("success").inc()
        event(logger, "info", "Portal request completed", outcome="success", step="directory_sync")
        return {"signature": "northbridge-portal-v1", "status": "operational", "university": "Northbridge University",
                "services": provider_payload[state["adapter_field"]], "demo": True,
                "notices": ["Autumn examinations: timetable published", "Innovation Week begins November 02",
                            "Library hours extended during examination week"]}
    finally:
        inflight -= 1
        latency.observe(time.monotonic() - started)
