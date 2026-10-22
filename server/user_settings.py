"""Per-user app settings (Sheet ID, Drive folder ID), persisted in SQLite.

Keyed by the Google account email from the Auth.js session so each signed-in
user gets their own Sheet and Drive folder. Stored in the same database file
as the job store to keep the architecture simple.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from config import get_settings

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    email            TEXT PRIMARY KEY,
    sheet_id         TEXT NOT NULL DEFAULT '',
    drive_folder_id  TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = get_settings().db_path
            path.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(_SCHEMA)
            _conn.commit()
        return _conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load(email: str) -> dict[str, str]:
    """Return the stored settings for ``email`` (empty strings when unset)."""
    key = email.strip().lower()
    with _lock:
        row = _db().execute(
            "SELECT sheet_id, drive_folder_id FROM user_settings WHERE email = ?", (key,)
        ).fetchone()
    if row is None:
        return {"email": key, "sheet_id": "", "drive_folder_id": ""}
    return {
        "email": key,
        "sheet_id": row["sheet_id"],
        "drive_folder_id": row["drive_folder_id"],
    }


def save(email: str, sheet_id: str, drive_folder_id: str) -> dict[str, str]:
    """Upsert the settings for ``email``."""
    key = email.strip().lower()
    now = _utcnow()
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO user_settings (email, sheet_id, drive_folder_id, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(email) DO UPDATE SET"
            " sheet_id = excluded.sheet_id,"
            " drive_folder_id = excluded.drive_folder_id,"
            " updated_at = excluded.updated_at",
            (key, sheet_id.strip(), drive_folder_id.strip(), now),
        )
    return {"email": key, "sheet_id": sheet_id.strip(), "drive_folder_id": drive_folder_id.strip()}


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
