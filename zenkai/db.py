"""Durable SQLite persistence for Zenkai recovery engine.
Persists workflows, incidents, proposals, approvals, timeline events, and active adapters.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "zenkai.db")


def canonical_json(data: Any) -> str:
    """Produces deterministic, sorted, whitespace-trimmed JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class ZenkaiDatabase:
    """Thread-safe SQLite storage for Zenkai with atomic transactions."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    student_id TEXT NOT NULL,
                    student_name TEXT NOT NULL,
                    student_email TEXT NOT NULL,
                    course_code TEXT NOT NULL,
                    course_title TEXT NOT NULL,
                    term_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_scenario TEXT NOT NULL,
                    incident_id TEXT,
                    steps_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    failed_step TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    proposal_hash TEXT,
                    proposal_json TEXT,
                    validation_json TEXT,
                    recovery_seconds REAL,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    schema_version TEXT DEFAULT 'v2.4',
                    created_at TEXT NOT NULL,
                    recovered_at TEXT,
                    FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    proposal_hash TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    executed INTEGER DEFAULT 0,
                    FOREIGN KEY(incident_id) REFERENCES incidents(incident_id)
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    service TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    workflow_id TEXT,
                    incident_id TEXT,
                    reason TEXT,
                    details_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS adapters (
                    src_field TEXT PRIMARY KEY,
                    dst_field TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.commit()

    # --- Workflows ---
    def save_workflow(self, wf: Dict[str, Any]):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO workflows (
                    workflow_id, student_id, student_name, student_email,
                    course_code, course_title, term_id, status, active_scenario,
                    incident_id, steps_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    status=excluded.status,
                    incident_id=excluded.incident_id,
                    steps_json=excluded.steps_json,
                    updated_at=excluded.updated_at
            """, (
                wf["workflow_id"],
                wf["student"]["student_id"],
                wf["student"]["name"],
                wf["student"]["email"],
                wf["course"]["course_code"],
                wf["course"]["title"],
                wf["term_id"],
                wf["status"],
                wf["active_scenario"],
                wf.get("incident_id"),
                canonical_json(wf["steps"]),
                wf["created_at"],
                wf["updated_at"],
            ))
            conn.commit()

    def get_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if not row:
                return None
            return self._row_to_workflow(row)

    def list_workflows(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM workflows ORDER BY created_at DESC").fetchall()
            return [self._row_to_workflow(r) for r in rows]

    def _row_to_workflow(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "workflow_id": row["workflow_id"],
            "student": {
                "student_id": row["student_id"],
                "name": row["student_name"],
                "email": row["student_email"],
            },
            "course": {
                "course_code": row["course_code"],
                "title": row["course_title"],
            },
            "term_id": row["term_id"],
            "status": row["status"],
            "active_scenario": row["active_scenario"],
            "incident_id": row["incident_id"],
            "steps": json.loads(row["steps_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # --- Incidents ---
    def save_incident(self, inc: Dict[str, Any]):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO incidents (
                    incident_id, workflow_id, failed_step, error_code,
                    error_message, raw_payload_json, status, proposal_hash,
                    proposal_json, validation_json, recovery_seconds,
                    retry_count, max_retries, schema_version, created_at, recovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status=excluded.status,
                    proposal_hash=excluded.proposal_hash,
                    proposal_json=excluded.proposal_json,
                    validation_json=excluded.validation_json,
                    recovery_seconds=excluded.recovery_seconds,
                    retry_count=excluded.retry_count,
                    recovered_at=excluded.recovered_at
            """, (
                inc["incident_id"],
                inc["workflow_id"],
                inc["failed_step"],
                inc["error_code"],
                inc["error_message"],
                canonical_json(inc["raw_payload"]),
                inc["status"],
                inc.get("proposal_hash"),
                canonical_json(inc["patch_proposal"]) if inc.get("patch_proposal") else None,
                canonical_json(inc["validation_result"]) if inc.get("validation_result") else None,
                inc.get("recovery_seconds"),
                inc.get("retry_count", 0),
                inc.get("max_retries", 3),
                inc.get("schema_version", "v2.4"),
                inc["created_at"],
                inc.get("recovered_at"),
            ))
            conn.commit()

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            if not row:
                return None
            return self._row_to_incident(row)

    def list_incidents(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()
            return [self._row_to_incident(r) for r in rows]

    def _row_to_incident(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "incident_id": row["incident_id"],
            "workflow_id": row["workflow_id"],
            "failed_step": row["failed_step"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "raw_payload": json.loads(row["raw_payload_json"]),
            "status": row["status"],
            "proposal_hash": row["proposal_hash"],
            "patch_proposal": json.loads(row["proposal_json"]) if row["proposal_json"] else None,
            "validation_result": json.loads(row["validation_json"]) if row["validation_json"] else None,
            "recovery_seconds": row["recovery_seconds"],
            "recovery_duration_ms": round(row["recovery_seconds"] * 1000, 2) if row["recovery_seconds"] is not None else None,
            "retry_count": row["retry_count"],
            "max_retries": row["max_retries"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
            "recovered_at": row["recovered_at"],
        }

    # --- Approvals & Hash Binding ---
    def record_approval(self, approval_id: str, incident_id: str, proposal_hash: str, approved_by: str, approved_at: str) -> bool:
        """Records an approval. Returns False if already recorded."""
        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO approvals (approval_id, incident_id, proposal_hash, approved_by, approved_at, executed)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (approval_id, incident_id, proposal_hash, approved_by, approved_at))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_approval_executed(self, approval_id: str) -> bool:
        """Atomically marks approval as executed. Returns False if already executed."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE approvals SET executed = 1 WHERE approval_id = ? AND executed = 0
            """, (approval_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_approval_by_hash(self, proposal_hash: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE proposal_hash = ?", (proposal_hash,)).fetchone()
            if not row:
                return None
            return dict(row)

    # --- Timeline Events ---
    def record_timeline_event(self, service: str, event_type: str, details: Dict[str, Any], timestamp: str,
                              workflow_id: Optional[str] = None, incident_id: Optional[str] = None, reason: Optional[str] = None):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO timeline_events (timestamp, service, event_type, workflow_id, incident_id, reason, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, service, event_type, workflow_id, incident_id, reason, canonical_json(details)))
            conn.commit()

    def list_timeline_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM timeline_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            events = []
            for r in rows:
                events.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "service": r["service"],
                    "event_type": r["event_type"],
                    "workflow_id": r["workflow_id"],
                    "incident_id": r["incident_id"],
                    "reason": r["reason"],
                    "details": json.loads(r["details_json"]),
                })
            return events

    # --- Adapters ---
    def save_adapter(self, src_field: str, dst_field: str, schema_version: str, created_at: str):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO adapters (src_field, dst_field, schema_version, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(src_field) DO UPDATE SET dst_field=excluded.dst_field, schema_version=excluded.schema_version
            """, (src_field, dst_field, schema_version, created_at))
            conn.commit()

    def delete_adapter(self, src_field: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM adapters WHERE src_field = ?", (src_field,))
            conn.commit()

    def list_adapters(self) -> Dict[str, str]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT src_field, dst_field FROM adapters").fetchall()
            return {r["src_field"]: r["dst_field"] for r in rows}

    def clear_all(self):
        with self._get_connection() as conn:
            conn.executescript("""
                DELETE FROM approvals;
                DELETE FROM timeline_events;
                DELETE FROM incidents;
                DELETE FROM workflows;
                DELETE FROM adapters;
            """)
            conn.commit()
