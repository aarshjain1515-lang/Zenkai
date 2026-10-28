"""FastAPI Application exposing REST endpoints, incident telemetry, and demo controls."""

import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from zenkai.engine import ZenkaiEngine
from zenkai.models import Course, FailureScenario, Student


app = FastAPI(
    title="Zenkai - Self-Healing AI Recovery Layer",
    description="Autonomous error detection, root-cause diagnosis, and automated patch execution for mission-critical university workflows.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ZenkaiEngine()


class RunWorkflowRequest(BaseModel):
    student_id: str = "STU-8821"
    student_name: str = "Aarav Sharma"
    course_code: str = "CS-499"
    course_title: str = "Senior Capstone Project"
    term_id: str = "SEM-2026-FALL"
    scenario: FailureScenario = FailureScenario.SCHEMA_DRIFT
    auto_approve: bool = True


class ApproveIncidentRequest(BaseModel):
    proposal_hash: str
    approved_by: str = "security_admin"


@app.get("/api/status")
def get_system_status():
    return {
        "status": "ONLINE",
        "system": "Zenkai Autonomous Recovery Mesh",
        "active_adapters": engine.db.list_adapters(),
        "metrics": engine.metrics,
    }


@app.post("/api/workflows/run")
def run_workflow(req: RunWorkflowRequest):
    student = Student(student_id=req.student_id, name=req.student_name, email=f"{req.student_id.lower()}@campus.edu")
    course = Course(course_code=req.course_code, title=req.course_title)

    wf = engine.create_registration_journey(
        student=student,
        course=course,
        term_id=req.term_id,
        scenario=req.scenario,
    )

    executed_wf = engine.execute_journey(wf.workflow_id, auto_approve_safe_patches=req.auto_approve)

    incident_data = None
    if executed_wf.incident_id:
        incident_data = engine.get_incident(executed_wf.incident_id)

    return {
        "workflow": executed_wf,
        "incident": incident_data,
        "metrics": engine.metrics,
    }


@app.get("/api/workflows")
def list_workflows():
    return engine.db.list_workflows()


@app.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: str):
    wf = engine.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


@app.get("/api/incidents")
def list_incidents():
    return engine.db.list_incidents()


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    inc = engine.get_incident(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


@app.post("/api/incidents/{incident_id}/approve")
def approve_incident(incident_id: str, req: ApproveIncidentRequest):
    try:
        wf = engine.approve_incident_and_resume(incident_id, proposal_hash=req.proposal_hash, approved_by=req.approved_by)
        inc = engine.get_incident(incident_id)
        return {"workflow": wf, "incident": inc, "metrics": engine.metrics}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/events")
def list_events(limit: int = 50):
    return engine.db.list_timeline_events(limit=limit)


@app.get("/api/sustained-check")
def run_sustained_check(count: int = 5):
    return engine.run_sustained_check(count_per_phase=count)


@app.post("/api/reset")
def reset_system():
    engine.reset_state()
    return {"message": "State reset successfully", "metrics": engine.metrics}


@app.get("/")
def serve_index():
    index_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>Zenkai Recovery Engine API is running.</h1><p>Visit /docs for OpenAPI specifications.</p>")
