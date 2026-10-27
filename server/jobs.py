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
    job_id           TEXT PRIMARY KEY,
    date             TEXT NOT NULL,
    requested_by     TEXT,
    sheet_id         TEXT NOT NULL DEFAULT '',
    drive_folder_id  TEXT NOT NULL DEFAULT '',
    notify_email     TEXT NOT NULL DEFAULT '',
    state            TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
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
    folder_path      TEXT,
    error            TEXT,
    error_code       TEXT,
    warnings         TEXT NOT NULL DEFAULT '[]',
    replace          INTEGER NOT NULL DEFAULT 0,
    existing_drive_url TEXT NOT NULL DEFAULT '',
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
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(jobs)")}
            if "sheet_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN sheet_id TEXT NOT NULL DEFAULT ''"
                )
            if "drive_folder_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN drive_folder_id TEXT NOT NULL DEFAULT ''"
                )
            if "notify_email" not in columns:
                self._conn.execute(
                    "ALTER TABLE jobs ADD COLUMN notify_email TEXT NOT NULL DEFAULT ''"
                )
            row_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(job_rows)")}
            if "replace" not in row_columns:
                self._conn.execute(
                    "ALTER TABLE job_rows ADD COLUMN replace INTEGER NOT NULL DEFAULT 0"
                )
            if "existing_drive_url" not in row_columns:
                self._conn.execute(
                    "ALTER TABLE job_rows ADD COLUMN existing_drive_url TEXT NOT NULL DEFAULT ''"
                )
            if "folder_path" not in row_columns:
                self._conn.execute(
                    "ALTER TABLE job_rows ADD COLUMN folder_path TEXT"
                )
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
        sheet_id: str = "",
        drive_folder_id: str = "",
        notify_email: str = "",
    ) -> None:
        now = utcnow_iso()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO jobs (job_id, date, requested_by, sheet_id, drive_folder_id, notify_email, state,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, sheet_date, requested_by, sheet_id, drive_folder_id, notify_email, now, now),
            )
            for position, row in enumerate(rows):
                self._conn.execute(
                    "INSERT INTO job_rows (job_id, row_id, position, account, task_type,"
                    " task_description, sheet_status, canva_link, sheet_row, state,"
                    " replace, existing_drive_url)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
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
                        1 if row.replace else 0,
                        row.existing_drive_url,
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
        folder_path: str | None = None,
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
            ("folder_path", folder_path),
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

    def delete_jobs_older_than(self, hours: int = 720) -> int:
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

    def find_prior_drive_url(
        self, sheet_id: str, sheet_date: str, sheet_row: int, exclude_job_id: str
    ) -> str:
        """Most recent Drive URL this app delivered for a given Sheet row.

        Used by replace mode when the Sheet cell holds a smart chip (the API
        only reads "@" back) so the original file ID is still recoverable.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT r.drive_url FROM job_rows r JOIN jobs j ON j.job_id = r.job_id"
                " WHERE r.sheet_row = ? AND r.drive_url LIKE 'http%'"
                " AND j.sheet_id = ? AND j.date = ? AND r.job_id != ?"
                " ORDER BY r.rowid DESC LIMIT 1",
                (sheet_row, sheet_id, sheet_date, exclude_job_id),
            ).fetchone()
        return row["drive_url"] if row else ""

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
        "sheet_id": job["sheet_id"],
        "drive_folder_id": job["drive_folder_id"],
        "notify_email": job["notify_email"] or "",
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
                "folder_path": row["folder_path"] or "",
                "replace": bool(row["replace"]),
                "existing_drive_url": row["existing_drive_url"] or "",
            }
            for row in rows
        ],
    }
