"""Safe cleanup of verified uploads and old failed jobs."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from config import get_settings

log = logging.getLogger(__name__)


def _root() -> Path:
    return get_settings().download_dir.resolve()


def _safe(path: Path, *, allow_directory: bool = True) -> Path:
    if path.is_symlink():
        raise ValueError(f"refusing to touch symlink: {path}")
    resolved = path.resolve()
    root = _root()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"refusing to touch path outside DOWNLOAD_DIR: {resolved}")
    if not allow_directory and resolved.is_dir():
        raise ValueError(f"refusing to delete directory as a file: {resolved}")
    return resolved


def cleanup_job_dir(job_dir: Path, uploaded_files: list[Path]) -> None:
    for path in uploaded_files:
        try:
            target = _safe(Path(path))
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except (ValueError, OSError):
            log.exception("could not remove verified upload %s", path)
    for path in (job_dir, job_dir.parent):
        try:
            _safe(Path(path)).rmdir()
        except (ValueError, OSError):
            pass
    if job_dir.exists():
        for remaining in job_dir.rglob("*"):
            if remaining.is_file():
                log.warning("keeping unuploaded file %s", remaining)


def sweep_old_leftovers() -> None:
    settings = get_settings()
    root = settings.download_dir
    cutoff = time.time() - settings.keep_failed_days * 86400
    if not root.exists():
        return
    for date_dir in root.iterdir():
        if not date_dir.is_dir() or date_dir.is_symlink():
            continue
        for job_dir in date_dir.iterdir():
            if not job_dir.is_dir() or job_dir.is_symlink():
                continue
            files = [p for p in job_dir.rglob("*") if p.is_file()]
            if files and max(p.stat().st_mtime for p in files) < cutoff:
                try:
                    shutil.rmtree(_safe(job_dir))
                except (ValueError, OSError):
                    log.exception("could not sweep %s", job_dir)
        try:
            _safe(date_dir).rmdir()
        except (ValueError, OSError):
            pass
