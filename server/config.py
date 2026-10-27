"""Configuration for the Canva automation server.

All values come from environment variables (or a local ``.env`` file next to
this module).  See ``.env.example`` for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

VALID_DATE_INPUT_MODES = {"RAW", "USER_ENTERED"}


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value  # type: ignore[return-value]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    automation_secret: str
    root_folder_id: str
    sheet_id: str
    sheet_tab: str
    date_input_mode: str
    download_dir: Path
    chrome_profile_dir: Path
    keep_failed_days: int
    create_missing_folders: bool
    max_rows_per_job: int
    db_path: Path
    log_level: str
    canva_session_ttl_seconds: int = 300
    # Email (Google SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_app_password: str = ""
    smtp_from: str = ""
    email_fallback_to: str = "hsarosh569@gmail.com"
    # AI folder routing
    groq_api_key: str = ""
    gemini_api_key: str = ""
    routing_min_confidence: float = 0.75
    routing_max_depth: int = 3
    routing_max_nodes: int = 300
    routing_timeout_seconds: int = 20
    routing_model_groq: str = "openai/gpt-oss-120b"
    routing_model_gemini: str = "gemini-3.8-flash"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    date_input_mode = _env("DATE_INPUT_MODE", "RAW").strip().upper()
    if date_input_mode not in VALID_DATE_INPUT_MODES:
        raise RuntimeError(
            f"DATE_INPUT_MODE must be one of {sorted(VALID_DATE_INPUT_MODES)}, got {date_input_mode!r}"
        )

    smtp_user = _env("SMTP_USER", "hsarosh569@gmail.com")
    return Settings(
        automation_secret=_env("AUTOMATION_SECRET", required=True),
        root_folder_id=_env("ROOT_FOLDER_ID", ""),
        sheet_id=_env("SHEET_ID", ""),
        sheet_tab=_env("SHEET_TAB", "Sheet1"),
        date_input_mode=date_input_mode,
        download_dir=Path(_env("DOWNLOAD_DIR", str(BASE_DIR / "downloads"))).expanduser(),
        chrome_profile_dir=Path(
            _env("CHROME_PROFILE_DIR", str(BASE_DIR / "chrome-profile"))
        ).expanduser(),
        keep_failed_days=_env_int("KEEP_FAILED_DAYS", 7),
        create_missing_folders=_env_bool("CREATE_MISSING_FOLDERS", False),
        max_rows_per_job=_env_int("MAX_ROWS_PER_JOB", 30),
        db_path=Path(_env("DB_PATH", str(BASE_DIR / "data" / "jobs.db"))).expanduser(),
        log_level=_env("LOG_LEVEL", "INFO").strip().upper(),
        smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=smtp_user,
        smtp_app_password=_env("SMTP_APP_PASSWORD", ""),
        smtp_from=_env("SMTP_FROM", smtp_user),
        email_fallback_to=_env("EMAIL_FALLBACK_TO", "hsarosh569@gmail.com"),
        groq_api_key=_env("GROQ_API_KEY", ""),
        gemini_api_key=_env("GEMINI_API_KEY", ""),
        routing_min_confidence=_env_float("ROUTING_MIN_CONFIDENCE", 0.75),
        routing_max_depth=_env_int("ROUTING_MAX_DEPTH", 3),
        routing_max_nodes=_env_int("ROUTING_MAX_NODES", 300),
        routing_timeout_seconds=_env_int("ROUTING_TIMEOUT_SECONDS", 20),
        routing_model_groq=_env("ROUTING_MODEL_GROQ", "openai/gpt-oss-120b"),
        routing_model_gemini=_env("ROUTING_MODEL_GEMINI", "gemini-3.8-flash"),
    )
