"""The per-row pipeline: download → upload → update the Sheet cell.

Runs one row at a time; a failing row never stops the rest of the batch.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Callable

import canva_bot
import cleanup
import drive
import sheets
from config import Settings
from jobs import JobStore
from models import AutomationError

log = logging.getLogger(__name__)

RowProgress = Callable[[int, int], None]


async def run_job(
    job: dict,
    token: str,
    settings: Settings,
    store: JobStore,
    on_row: RowProgress | None = None,
) -> str:
    """Process every pending row of ``job``. Returns the final job state."""
    job_id: str = job["job_id"]
    rows: list[dict] = job["rows"]
    total = len(rows)
    sheet_id: str = job.get("sheet_id") or ""
    run_date = datetime.now().strftime("%Y-%m-%d")
    job_dir = settings.download_dir / run_date / job_id
    uploaded_files: list[Path] = []

    job_dir.mkdir(parents=True, exist_ok=True)
    drive.clear_folder_cache()

    try:
        for position, row in enumerate(rows, start=1):
            if on_row is not None:
                on_row(position, total)

            if row["state"] in {"done", "failed"}:
                continue

            try:
                await _process_row(job_id, row, token, job_dir, store, uploaded_files, sheet_id)
            except AutomationError as exc:
                _mark_failed(store, job_id, row["row_id"], exc.code, exc.message)
                if exc.code == "TOKEN_EXPIRED":
                    _fail_remaining(store, job_id, rows, position)
                    break
            except Exception:
                log.exception("unexpected error on row %s of job %s", position, job_id)
                _mark_failed(
                    store,
                    job_id,
                    row["row_id"],
                    "INTERNAL_ERROR",
                    "Unexpected error while processing this row.",
                )

            if position < total:
                await asyncio.sleep(random.uniform(1, 3))
    finally:
        try:
            await asyncio.to_thread(cleanup.cleanup_job_dir, job_dir, uploaded_files)
        except Exception:
            log.exception("cleanup failed for job %s", job_id)

    final_job = store.get_job(job_id) or {"rows": []}
    has_failed = any(row["state"] == "failed" for row in final_job["rows"])
    final_state = "done_with_errors" if has_failed else "done"
    store.set_job_state(job_id, final_state)
    log.info("job %s finished: %s", job_id, final_state)
    return final_state


async def _process_row(
    job_id: str,
    row: dict,
    token: str,
    job_dir: Path,
    store: JobStore,
    uploaded_files: list[Path],
    sheet_id: str = "",
) -> None:
    row_id = row["row_id"]

    # Skip rows that already have a Drive link in Post Link.
    existing_link = row.get("post_link") or row.get("drive_url") or ""
    if existing_link.strip():
        store.set_row_state(job_id, row_id, "done", drive_url=existing_link.strip())
        log.info("row %s already has a Post Link; skipping download", row_id)
        return

    store.set_row_state(job_id, row_id, "downloading")
    downloaded = await asyncio.to_thread(
        canva_bot.download_design, row["canva_link"], job_dir
    )

    store.set_row_state(
        job_id,
        row_id,
        "uploading",
        file_name=downloaded.name,
        file_path=str(downloaded.path),
    )
    upload = await asyncio.to_thread(
        drive.upload_and_share, token, row["account"], row["task_type"], downloaded
    )

    # Uploaded + verified on Drive: this file may be deleted at the end of the job.
    uploaded_files.append(downloaded.path)

    store.set_row_state(
        job_id,
        row_id,
        "updating_sheet",
        drive_url=upload.web_view_link,
        warnings=upload.warnings,
    )

    if row.get("sheet_row") is None:
        raise AutomationError(
            "SHEET_ROW_NOT_FOUND", "The Sheet row number for this item is unknown."
        )

    await asyncio.to_thread(
        sheets.set_drive_link,
        token,
        int(row["sheet_row"]),
        upload.web_view_link,
        sheet_id,
    )

    store.set_row_state(
        job_id,
        row_id,
        "done",
        drive_url=upload.web_view_link,
        warnings=upload.warnings,
    )


def _mark_failed(store: JobStore, job_id: str, row_id: str, code: str, message: str) -> None:
    store.set_row_state(
        job_id,
        row_id,
        "failed",
        error=message,
        error_code=code,
    )


def _fail_remaining(
    store: JobStore, job_id: str, rows: list[dict], current_position: int
) -> None:
    for row in rows[current_position:]:
        if row["state"] in {"done", "failed"}:
            continue
        _mark_failed(
            store,
            job_id,
            row["row_id"],
            "TOKEN_EXPIRED",
            "Google access token expired. Sign in again and resubmit this row.",
        )
