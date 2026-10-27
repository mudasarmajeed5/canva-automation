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
import emailer
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
    drive_folder_id: str = job.get("drive_folder_id") or ""
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
                await _process_row(
                    job_id,
                    row,
                    token,
                    job_dir,
                    store,
                    uploaded_files,
                    sheet_id,
                    drive_folder_id,
                    job.get("date") or "",
                )
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

    try:
        await asyncio.to_thread(_send_job_email, final_job, settings, final_state)
    except Exception:
        log.exception("job %s: upload report email failed", job_id)

    return final_state


def _send_job_email(final_job: dict, settings: Settings, final_state: str) -> None:
    """Email a per-file upload report after the job finishes. Never raises."""
    rows = final_job.get("rows") or []
    if not rows:
        return
    entries: list[dict] = []
    for row in rows:
        state = row.get("state") or ""
        file_name = row.get("file") or row.get("canva_link") or "file"
        folder = (row.get("folder_path") or "").strip()
        url = (row.get("drive_url") or "").strip()
        replaced = bool(row.get("replace"))
        entry = {
            "file": file_name,
            "path": folder,
            "url": url,
            "account": row.get("account") or "",
            "task_type": row.get("task_type") or "",
        }
        if state == "done":
            entry["kind"] = "replaced" if replaced else "uploaded"
            if replaced:
                entry["detail"] = "Previous version replaced in place — same Drive link."
        elif state == "failed":
            entry["kind"] = "failed"
            entry["detail"] = row.get("error") or "unknown error"
        else:
            entry["kind"] = state or "info"
        entries.append(entry)

    date = final_job.get("date") or ""
    uploaded = sum(1 for r in rows if r.get("state") == "done")
    suffix = " with errors" if final_state == "done_with_errors" else ""
    subject = f"Canva upload report — {date} ({uploaded} file(s)){suffix}"
    to = (final_job.get("notify_email") or "").strip() or settings.email_fallback_to
    emailer.send_upload_report(
        to, subject, entries, date=date, final_state=final_state
    )


async def _process_row(
    job_id: str,
    row: dict,
    token: str,
    job_dir: Path,
    store: JobStore,
    uploaded_files: list[Path],
    sheet_id: str = "",
    drive_folder_id: str = "",
    job_date: str = "",
) -> None:
    row_id = row["row_id"]
    replace = bool(row.get("replace"))

    # Skip rows that already have a Drive link in Post Link — unless the
    # caller explicitly asked to re-download and replace the delivered file.
    existing_link = (row.get("post_link") or row.get("drive_url") or "").strip()
    if existing_link and not replace:
        store.set_row_state(job_id, row_id, "done", drive_url=existing_link)
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

    prior_url = _replace_target_url(store, job_id, row, sheet_id, job_date)
    if replace and prior_url:
        log.info("row %s: replacing Drive file in place", row_id)
        upload = await asyncio.to_thread(
            drive.replace_file,
            token,
            row["account"],
            row["task_type"],
            downloaded,
            prior_url,
            drive_folder_id,
        )
    else:
        upload = await asyncio.to_thread(
            drive.upload_and_share,
            token,
            row["account"],
            row["task_type"],
            downloaded,
            drive_folder_id,
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

    # An in-place replace keeps the same URL: leave the Sheet cell (and its
    # smart chip) untouched. Only write when the link is new or changed.
    if not prior_url or upload.web_view_link != prior_url:
        await asyncio.to_thread(
            sheets.set_drive_link,
            token,
            int(row["sheet_row"]),
            upload.web_view_link,
            sheet_id,
        )

    # File is on Drive: mark the Sheet row Done. Failure here must not fail
    # the row — the upload already succeeded.
    try:
        await asyncio.to_thread(
            sheets.set_status,
            token,
            int(row["sheet_row"]),
            "Done",
            sheet_id,
        )
    except Exception:
        log.exception("could not set Status=Done for row %s", row.get("sheet_row"))

    store.set_row_state(
        job_id,
        row_id,
        "done",
        drive_url=upload.web_view_link,
        warnings=upload.warnings,
        folder_path=upload.folder_path,
    )


def _replace_target_url(
    store: JobStore, job_id: str, row: dict, sheet_id: str, job_date: str
) -> str:
    """Resolve the existing Drive URL to replace, if one can be found.

    Order: URL sent by the app, this job's stored URL, then delivery history
    for the same Sheet row (covers Sheet cells that hold a smart chip, which
    the API reads back only as "@").
    """
    if not row.get("replace"):
        return ""
    for candidate in (row.get("existing_drive_url") or "", row.get("drive_url") or ""):
        if drive.extract_drive_file_id(candidate):
            return candidate.strip()
    sheet_row = row.get("sheet_row")
    if sheet_row is not None and sheet_id:
        try:
            return store.find_prior_drive_url(sheet_id, job_date, int(sheet_row), job_id)
        except Exception:
            log.exception("could not look up a prior Drive URL for row %s", row.get("row_id"))
    return ""


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
