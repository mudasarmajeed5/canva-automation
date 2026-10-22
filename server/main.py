"""FastAPI app for the Canva automation server.

Modules 1–4 are implemented: API queueing, Canva download, Google Sheets,
Google Drive routing/upload, and safe cleanup.
"""

from __future__ import annotations

import asyncio
import collections
import hmac
import html as html_lib
import logging
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

import canva_bot
import cleanup
import sheets
from config import BASE_DIR, Settings, get_settings
from jobs import JobStore, forget_all_tokens, new_job_id, remember_token
from models import (
    AutomationError,
    CreateRowsRequest,
    DownloadRequest,
    JobRequest,
    RowInput,
    UpdateRowsRequest,
)
from worker import Worker

log = logging.getLogger(__name__)

STATUS_TEMPLATE = BASE_DIR / "templates" / "status.html"

RATE_LIMIT_PER_MINUTE = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0
LEFTOVER_COUNT_LIMIT = 5000


# --- helpers -----------------------------------------------------------------


class RateLimiter:
    """Tiny in-memory sliding-window limiter (one server process)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, collections.deque())
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True


class CanvaSessionCache:
    """Checks the Canva login at most once every ``ttl`` seconds."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._value: bool | None = None
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> bool | None:
        async with self._lock:
            if self._value is not None and time.monotonic() - self._checked_at < self._ttl:
                return self._value
            try:
                self._value = await asyncio.to_thread(canva_bot.check_session)
            except Exception:
                log.exception("Canva session check failed")
                self._value = None
            self._checked_at = time.monotonic()
            return self._value

    def peek(self) -> bool | None:
        """Last known value without launching a browser."""
        return self._value


rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, RATE_LIMIT_WINDOW_SECONDS)


def _settings() -> Settings:
    return get_settings()


async def require_secret(
    x_automation_secret: str | None = Header(default=None, alias="X-Automation-Secret"),
) -> None:
    expected = _settings().automation_secret
    provided = x_automation_secret or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing X-Automation-Secret")


def _disk_free_gb(path: Path) -> float:
    target = path if path.exists() else path.parent
    return shutil.disk_usage(target).free / (1024**3)


def _count_files(root: Path, limit: int = LEFTOVER_COUNT_LIMIT) -> int:
    if not root.exists():
        return 0
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(root):
        total += len(filenames)
        if total >= limit:
            return limit
    return total


def _render_status(context: dict[str, str]) -> str:
    template = STATUS_TEMPLATE.read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace("{{ " + key + " }}", html_lib.escape(str(value)))
    return template


# --- app ---------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    store = JobStore(settings.db_path)
    restarted = store.fail_unfinished_jobs("server restarted")
    if restarted:
        log.warning("marked %s unfinished job(s) as failed (server restarted)", restarted)
    forget_all_tokens()

    worker = Worker(store, settings)
    app.state.store = store
    app.state.worker = worker
    app.state.canva_session = CanvaSessionCache(settings.canva_session_ttl_seconds)

    await worker.start()

    try:
        await asyncio.to_thread(cleanup.sweep_old_leftovers)
        store.delete_jobs_older_than(24)
    except Exception:
        log.exception("startup maintenance failed")

    log.info("automation server ready (download dir: %s)", settings.download_dir)
    try:
        yield
    finally:
        await worker.stop()
        store.close()


app = FastAPI(title="Canva Automation", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        details.append(
            {
                "field": location or "request",
                "message": error.get("msg", "invalid value"),
            }
        )
    return JSONResponse(status_code=400, content={"error": "validation_error", "details": details})


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def status_page(request: Request) -> HTMLResponse:
    settings = _settings()
    store: JobStore = request.app.state.store
    worker: Worker = request.app.state.worker
    canva_cache: CanvaSessionCache = request.app.state.canva_session

    canva_logged_in = (
        canva_cache.peek() if worker.state == "running" else await canva_cache.get()
    )
    if canva_logged_in is True:
        canva_text = "logged in"
    elif canva_logged_in is False:
        canva_text = "not logged in"
    else:
        canva_text = "unknown"

    if worker.state == "running" and worker.current_job_id:
        worker_text = (
            f"running job {worker.current_job_id} — row "
            f"{worker.current_row or 1} of {worker.current_total or 1}"
        )
    else:
        worker_text = "idle"

    leftover = await asyncio.to_thread(_count_files, settings.download_dir)
    free_gb = await asyncio.to_thread(_disk_free_gb, settings.download_dir)
    last_success = store.last_successful_job_at()

    context = {
        "worker": worker_text,
        "queue": str(worker.queue_length),
        "last_success": last_success or "never",
        "canva_session": canva_text,
        "canva_class": "ok" if canva_logged_in else "warn",
        "disk_free": f"{free_gb:.1f} GB",
        "leftover_files": f"{leftover}+" if leftover >= LEFTOVER_COUNT_LIMIT else str(leftover),
        "updated_at": time.strftime("%d-%b-%Y %H:%M:%S"),
    }
    return HTMLResponse(
        content=_render_status(context),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/jobs")
async def create_job(
    payload: JobRequest,
    request: Request,
    _: None = Depends(require_secret),
) -> JSONResponse:
    settings = _settings()
    store: JobStore = request.app.state.store
    worker: Worker = request.app.state.worker

    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "message": "Too many requests; try again in a minute."},
        )

    if len(payload.rows) > settings.max_rows_per_job:
        return JSONResponse(
            status_code=400,
            content={
                "error": "validation_error",
                "details": [
                    {
                        "field": "rows",
                        "message": f"at most {settings.max_rows_per_job} rows per job",
                    }
                ],
            },
        )

    rows = [row.to_row_input() for row in payload.rows]

    try:
        sheet_rows = await asyncio.to_thread(
            sheets.append_rows, payload.google_access_token, payload.date, rows, payload.sheet_id
        )
    except AutomationError as exc:
        log.error("sheet append failed (%s)", exc.code)
        return JSONResponse(
            status_code=502,
            content={"error": "sheet_write_failed", "message": exc.message},
        )
    except Exception:
        log.exception("sheet append failed unexpectedly")
        return JSONResponse(
            status_code=502,
            content={
                "error": "sheet_write_failed",
                "message": "Could not write the rows to Google Sheets.",
            },
        )

    job_id = new_job_id()
    store.create_job(job_id, payload.date, payload.requested_by, rows, sheet_rows, payload.sheet_id)
    remember_token(job_id, payload.google_access_token)
    worker.enqueue(job_id)

    log.info("queued job %s with %s row(s)", job_id, len(rows))
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "state": "queued", "rows_written": len(rows)},
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request, _: None = Depends(require_secret)) -> JSONResponse:
    store: JobStore = request.app.state.store
    job = store.get_job(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "job_not_found"})
    return JSONResponse(
        status_code=200,
        content={
            "job_id": job["job_id"],
            "state": job["state"],
            "created_at": job["created_at"],
            "rows": [
                {
                    "row_id": row["row_id"],
                    "sheet_row": row["sheet_row"],
                    "state": row["state"],
                    "file": row["file"],
                    "drive_url": row["drive_url"],
                    "error": row["error"],
                    "error_code": row["error_code"],
                    "warnings": row["warnings"],
                }
                for row in job["rows"]
            ],
        },
    )


# --- Sheet row management endpoints -------------------------------------------


@app.get("/api/sheet-rows")
async def get_sheet_rows(
    date: str,
    token: str,
    sheet_id: str = "",
    _: None = Depends(require_secret),
) -> JSONResponse:
    try:
        rows = await asyncio.to_thread(sheets.read_rows, token, date, sheet_id)
    except AutomationError as exc:
        return JSONResponse(status_code=502, content={"error": exc.code, "message": exc.message})
    except Exception:
        log.exception("sheet read failed")
        return JSONResponse(
            status_code=502,
            content={"error": "SHEET_API_FAILED", "message": "Could not read the Sheet."},
        )
    return JSONResponse(status_code=200, content={"rows": rows})


@app.post("/api/sheet-rows")
async def create_sheet_rows(
    payload: CreateRowsRequest,
    _: None = Depends(require_secret),
) -> JSONResponse:
    rows = [row.to_row_input() for row in payload.rows]
    try:
        sheet_rows = await asyncio.to_thread(
            sheets.create_rows, payload.google_access_token, payload.date, rows, payload.sheet_id
        )
    except AutomationError as exc:
        return JSONResponse(status_code=502, content={"error": exc.code, "message": exc.message})
    except Exception:
        log.exception("sheet create rows failed")
        return JSONResponse(
            status_code=502,
            content={"error": "SHEET_APPEND_FAILED", "message": "Could not write the rows to Sheets."},
        )
    return JSONResponse(
        status_code=201,
        content={"rows_written": len(rows), "row_numbers": sheet_rows},
    )


@app.patch("/api/sheet-rows")
async def patch_sheet_rows(
    payload: UpdateRowsRequest,
    _: None = Depends(require_secret),
) -> JSONResponse:
    updates = [u.model_dump(exclude_none=True) for u in payload.updates]
    try:
        await asyncio.to_thread(
            sheets.update_rows, payload.google_access_token, updates, payload.sheet_id
        )
    except AutomationError as exc:
        return JSONResponse(status_code=502, content={"error": exc.code, "message": exc.message})
    except Exception:
        log.exception("sheet update rows failed")
        return JSONResponse(
            status_code=502,
            content={"error": "SHEET_API_FAILED", "message": "Could not update the Sheet."},
        )
    return JSONResponse(status_code=200, content={"updated": len(updates)})


@app.post("/api/download")
async def queue_download(
    payload: DownloadRequest,
    request: Request,
    _: None = Depends(require_secret),
) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "message": "Too many requests; try again in a minute."},
        )

    store: JobStore = request.app.state.store
    worker: Worker = request.app.state.worker

    rows: list[RowInput] = []
    sheet_rows: dict[str, int] = {}
    for index, item in enumerate(payload.rows):
        row_id = f"dl_{index}_{item.row_number}"
        rows.append(
            RowInput(
                row_id=row_id,
                account=item.account,
                task_type=item.task_type,
                task_description="",
                status="Done",
                canva_link=item.canva_link,
            )
        )
        sheet_rows[row_id] = item.row_number

    job_id = new_job_id()
    store.create_job(job_id, payload.date, None, rows, sheet_rows, payload.sheet_id)
    remember_token(job_id, payload.google_access_token)
    worker.enqueue(job_id)

    log.info("queued download job %s with %s row(s)", job_id, len(rows))
    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "state": "queued", "rows_queued": len(rows)},
    )
