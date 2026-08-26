from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .schemas import JobRecord


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS render_jobs (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, progress INTEGER NOT NULL,
                    request_json TEXT NOT NULL, artifacts_json TEXT NOT NULL,
                    error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )

    def create(self, request: dict) -> JobRecord:
        job_id = uuid4().hex
        timestamp = now_iso()
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO render_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, "queued", 0, json.dumps(request), "[]", None, 0, timestamp, timestamp),
            )
        return self.get(job_id)

    def update(self, job_id: str, **changes: object) -> JobRecord:
        allowed = {"status", "progress", "error", "cancel_requested"}
        fields = {key: value for key, value in changes.items() if key in allowed}
        if "artifacts" in changes:
            fields["artifacts_json"] = json.dumps(changes["artifacts"])
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.lock, self._connect() as connection:
            connection.execute(
                f"UPDATE render_jobs SET {assignments} WHERE id = ?",
                (*fields.values(), job_id),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return JobRecord(
            id=row["id"], status=row["status"], progress=row["progress"],
            request=json.loads(row["request_json"]), artifacts=json.loads(row["artifacts_json"]),
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def cancel(self, job_id: str) -> JobRecord:
        current = self.get(job_id)
        if current.status in {"complete", "failed", "cancelled"}:
            return current
        return self.update(job_id, cancel_requested=1, status="cancelled")

    def cancellation_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT cancel_requested FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row[0])

    def recover_interrupted(self) -> int:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE render_jobs SET status = 'failed', error = 'Studio restarted during render', updated_at = ? WHERE status = 'running'",
                (now_iso(),),
            )
            return cursor.rowcount
