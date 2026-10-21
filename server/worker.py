"""Single background worker: one job at a time, plus daily maintenance."""

from __future__ import annotations

import asyncio
import logging

import cleanup
import pipeline
from config import Settings
from jobs import JobStore, pop_token

log = logging.getLogger(__name__)

_MAINTENANCE_INTERVAL_SECONDS = 24 * 60 * 60


class Worker:
    def __init__(self, store: JobStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()

        self.current_job_id: str | None = None
        self.current_row = 0
        self.current_total = 0

        self._worker_task: asyncio.Task | None = None
        self._maintenance_task: asyncio.Task | None = None

    # -- info used by the status page --------------------------------------

    @property
    def state(self) -> str:
        return "running" if self.current_job_id else "idle"

    @property
    def queue_length(self) -> int:
        return self.queue.qsize()

    def enqueue(self, job_id: str) -> None:
        self.queue.put_nowait(job_id)

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._run(), name="job-worker")
        self._maintenance_task = asyncio.create_task(
            self._maintenance_loop(), name="daily-maintenance"
        )

    async def stop(self) -> None:
        for task in (self._worker_task, self._maintenance_task):
            if task is not None:
                task.cancel()
        tasks = [t for t in (self._worker_task, self._maintenance_task) if t is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- loops --------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s crashed", job_id)
                try:
                    self.store.set_job_state(job_id, "failed")
                except Exception:
                    log.exception("could not mark job %s failed", job_id)
            finally:
                self.current_job_id = None
                self.current_row = 0
                self.current_total = 0
                self.queue.task_done()

    async def _process(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        token = pop_token(job_id)

        if job is None:
            log.warning("queued job %s no longer exists", job_id)
            return

        if not token:
            for row in job["rows"]:
                if row["state"] in {"done", "failed"}:
                    continue
                self.store.set_row_state(
                    job_id,
                    row["row_id"],
                    "failed",
                    error="No Google access token was provided for this job.",
                    error_code="TOKEN_EXPIRED",
                )
            self.store.set_job_state(job_id, "failed")
            return

        self.store.set_job_state(job_id, "running")
        self.current_job_id = job_id
        self.current_total = len(job["rows"])

        try:
            await pipeline.run_job(
                job,
                token,
                self.settings,
                self.store,
                on_row=self._on_row_progress,
            )
        except Exception:
            log.exception("pipeline failed for job %s", job_id)
            self.store.set_job_state(job_id, "failed")

    def _on_row_progress(self, position: int, total: int) -> None:
        self.current_row = position
        self.current_total = total

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(_MAINTENANCE_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(cleanup.sweep_old_leftovers)
            except Exception:
                log.exception("leftover sweep failed")
            try:
                deleted = self.store.delete_jobs_older_than(24)
                if deleted:
                    log.info("deleted %s job(s) older than 24h", deleted)
            except Exception:
                log.exception("old job cleanup failed")
