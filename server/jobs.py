"""Job persistence (SQLite) and the in-memory Google token registry.

The Google access token is deliberately **never** persisted: it lives in the
``_TOKENS`` dict keyed by job id and is popped by the worker when the job
starts.  Everything else about a job is stored in SQLite so the status page and
``GET /api/jobs/{id}`` survive a restart.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from models import RowInput

log = logging.getLogger(__name__)

JOB_STATES = ("queued", "running", "done", "done_with_errors", "failed")
ROW_STATES = (
    "pending",
    "downloading",
    "uploading",
    "updating_sheet",
    "done",
    "failed",
)

_TOKENS: dict[str, str] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    date          TEXT NOT NULL,
    requested_by  TEXT,
    state         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_rows (
    job_id           TEXT NOT NULL,
    row_id           TEXT NOT NULL,
    position         INTEGER NOT NULL,
    account          TEXT NOT NULL,
    task_type        TEXT NOT NULL,
    task_description TEXT NOT NULL,
    sheet_status     TEXT NOT NULL,
    canva_link       TEXT NOT NULL,
    sheet_row        INTEGER,
    state            TEXT NOT NULL,
    file_name        TEXT,
    file_path        TEXT,
    drive_url        TEXT,
    error            TEXT,
    error_code       TEXT,
    warnings         TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (job_id, row_id),
    FOREIGN KEY (job_id) REFERENCES jobs (job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_job_rows_job ON job_rows (job_id, position);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (created_at);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_job_id() -> str:
    return "job_" + secrets.token_hex(8)


# --- token registry (memory only) -------------------------------------------


def remember_token(job_id: str, token: str) -> None:
    _TOKENS[job_id] = token


def pop_token(job_id: str) -> str | None:
    return _TOKENS.pop(job_id, None)


def forget_all_tokens() -> None:
    _TOKENS.clear()


# --- store -------------------------------------------------------------------


class JobStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes ---------------------------------------------------------------

    def create_job(
        self,
        job_id: str,
        sheet_date: str,
        requested_by: str | None,
        rows: Iterable[RowInput],
        sheet_rows: dict[str, int],
    ) -> None:
        now = utcnow_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (job_id, date, requested_by, state, created_at, updated_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?)",
                (job_id, sheet_date, requested_by, now, now),
            )
            for position, row in enumerate(rows):
                self._conn.execute(
                    "INSERT INTO job_rows (job_id, row_id, position, account, task_type,"
                    " task_description, sheet_status, canva_link, sheet_row, state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                    (
                        job_id,
                        row.row_id,
                        position,
                        row.account,
                        row.task_type,
                        row.task_description,
                        row.status,
                        row.canva_link,
                        sheet_rows.get(row.row_id),
                    ),
                )

    def set_job_state(self, job_id: str, state: str) -> None:
        if state not in JOB_STATES:
            raise ValueError(f"unknown job state: {state}")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (state, utcnow_iso(), job_id),
            )

    def set_row_state(
        self,
        job_id: str,
        row_id: str,
        state: str,
        *,
        file_name: str | None = None,
        file_path: str | None = None,
        drive_url: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        if state not in ROW_STATES:
            raise ValueError(f"unknown row state: {state}")

        fields = ["state = ?"]
        values: list[object] = [state]
        for column, value in (
            ("file_name", file_name),
            ("file_path", file_path),
            ("drive_url", drive_url),
            ("error", error),
            ("error_code", error_code),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if warnings is not None:
            fields.append("warnings = ?")
            values.append(json.dumps(warnings))

        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE job_rows SET {', '.join(fields)} WHERE job_id = ? AND row_id = ?",
                (*values, job_id, row_id),
            )
            self._conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ?", (utcnow_iso(), job_id)
            )

    def fail_unfinished_jobs(self, reason: str) -> int:
        """Mark queued/running jobs (and their pending rows) as failed."""
        now = utcnow_iso()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "SELECT job_id FROM jobs WHERE state IN ('queued', 'running')"
            )
            job_ids = [row["job_id"] for row in cursor.fetchall()]
            for job_id in job_ids:
                self._conn.execute(
                    "UPDATE jobs SET state = 'failed', updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                self._conn.execute(
                    "UPDATE job_rows SET state = 'failed', error = ?, error_code = 'SERVER_RESTARTED'"
                    " WHERE job_id = ? AND state NOT IN ('done', 'failed')",
                    (reason, job_id),
                )
            return len(job_ids)

    def delete_jobs_older_than(self, hours: int = 24) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
            self._conn.execute(
                "DELETE FROM job_rows WHERE job_id NOT IN (SELECT job_id FROM jobs)"
            )
            return cursor.rowcount

    # -- reads ----------------------------------------------------------------

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                return None
            rows = self._conn.execute(
                "SELECT * FROM job_rows WHERE job_id = ? ORDER BY position", (job_id,)
            ).fetchall()
        return _job_to_dict(job, rows)

    def last_successful_job_at(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(updated_at) AS finished_at FROM jobs WHERE state = 'done'"
            ).fetchone()
        return row["finished_at"] if row else None

    def count_jobs(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS total FROM jobs").fetchone()
        return int(row["total"])


def _job_to_dict(job: sqlite3.Row, rows: Iterable[sqlite3.Row]) -> dict:
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "date": job["date"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "rows": [
            {
                "row_id": row["row_id"],
                "sheet_row": row["sheet_row"],
                "state": row["state"],
                "file": row["file_name"],
                "drive_url": row["drive_url"],
                "error": row["error"],
                "error_code": row["error_code"],
                "warnings": json.loads(row["warnings"] or "[]"),
                # Internal fields used by the pipeline (not exposed by the API).
                "account": row["account"],
                "task_type": row["task_type"],
                "task_description": row["task_description"],
                "sheet_status": row["sheet_status"],
                "canva_link": row["canva_link"],
                "file_path": row["file_path"],
            }
            for row in rows
        ],
    }
