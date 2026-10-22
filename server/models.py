"""Shared data models, fixed vocabulary, and validation helpers.

This module is the single source of truth for the values that must match the
existing Google Sheet exactly (account names, task types, statuses).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Fixed vocabulary (must match the Sheet) ---------------------------------

ACCOUNTS: tuple[str, ...] = (
    "Excel Executive Cars",
    "Platinum Exetuive Car",  # intentional typo: matches the existing sheet
    "Reading Chauffers",
    "First Class Cars",
)

TASK_TYPES: tuple[str, ...] = (
    "Graphic Design - Static Post/Carousel",
    "Video/Reel Editing",
    "Branding/Other",
)

STATUSES: tuple[str, ...] = ("Not Started", "In Progress", "Completed", "Blocked")

DEFAULT_STATUS = "Not Started"

ALLOWED_CANVA_HOSTS: frozenset[str] = frozenset({"canva.com", "www.canva.com", "canva.link"})

_DATE_RE = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def is_valid_sheet_date(value: str) -> bool:
    """Return True for dates written as ``D-MMM-YYYY`` (e.g. ``21-Oct-2026``)."""
    match = _DATE_RE.match(value.strip())
    if not match:
        return False
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return False
    try:
        date(int(year), month, int(day))
    except ValueError:
        return False
    return True


def is_valid_canva_link(value: str) -> bool:
    """https link on an allowed Canva host (security rule 4)."""
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in ALLOWED_CANVA_HOSTS


def normalize_sheet_id(value: str) -> str:
    """Accept the raw ID from a Google Sheets/Drive URL; empty means default."""
    value = value.strip()
    if not value:
        return ""
    if any(char.isspace() or char in "/?#&" for char in value):
        raise ValueError(
            "must be the raw Google file/folder ID from the URL "
            "(the part between /d/ or /folders/ and the next slash) — not the full URL"
        )
    return value


# --- Domain objects (interfaces from Section 5) ------------------------------


@dataclass
class RowInput:
    row_id: str
    account: str
    task_type: str
    task_description: str
    status: str
    canva_link: str


@dataclass
class DownloadedFile:
    path: Path
    name: str  # original file name suggested by Canva (folder name for extracted zips)
    size: int
    is_directory: bool = False  # True when a .zip carousel was extracted into a folder


@dataclass
class UploadResult:
    file_id: str
    web_view_link: str
    folder_path: str  # e.g. "Excel Executive Cars/Posts"
    size: int
    warnings: list[str] = field(default_factory=list)


class AutomationError(Exception):
    """Expected, user-reportable failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


# --- API request models ------------------------------------------------------


class RowIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    row_id: str = Field(min_length=1, max_length=64)
    account: str
    task_type: str
    task_description: str = Field(min_length=1, max_length=500)
    status: str = DEFAULT_STATUS
    canva_link: str = ""

    @field_validator("account")
    @classmethod
    def _check_account(cls, value: str) -> str:
        if value not in ACCOUNTS:
            raise ValueError(f"account must be one of: {', '.join(ACCOUNTS)}")
        return value

    @field_validator("task_type")
    @classmethod
    def _check_task_type(cls, value: str) -> str:
        if value not in TASK_TYPES:
            raise ValueError(f"task_type must be one of: {', '.join(TASK_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        return value

    @field_validator("task_description")
    @classmethod
    def _check_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_description must not be empty")
        return value.strip()

    @field_validator("canva_link")
    @classmethod
    def _check_canva_link(cls, value: str) -> str:
        if not value:
            return ""
        if not is_valid_canva_link(value):
            raise ValueError(
                "canva_link must be an https link on canva.com, www.canva.com or canva.link"
            )
        return value.strip()

    def to_row_input(self) -> RowInput:
        return RowInput(
            row_id=self.row_id,
            account=self.account,
            task_type=self.task_type,
            task_description=self.task_description,
            status=self.status,
            canva_link=self.canva_link,
        )


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_type: Literal["CANVA_AUTOMATION"]
    date: str
    rows: list[RowIn] = Field(min_length=1)
    google_access_token: str = Field(min_length=10)
    sheet_id: str = ""
    drive_folder_id: str = ""
    requested_by: str | None = None

    _check_sheet_id = field_validator("sheet_id")(normalize_sheet_id)
    _check_drive_folder_id = field_validator("drive_folder_id")(normalize_sheet_id)

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str) -> str:
        if not is_valid_sheet_date(value):
            raise ValueError("date must use the D-MMM-YYYY format, e.g. 21-Oct-2026")
        return value.strip()

    @model_validator(mode="after")
    def _check_unique_row_ids(self) -> "JobRequest":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for row in self.rows:
            if row.row_id in seen:
                duplicates.add(row.row_id)
            seen.add(row.row_id)
        if duplicates:
            raise ValueError(f"row_id must be unique; repeated: {', '.join(sorted(duplicates))}")
        return self


# --- Sheet row management models ----------------------------------------------


class SheetRow(BaseModel):
    row_number: int
    date: str
    account: str
    task_type: str
    task_description: str
    status: str
    post_link: str


class RowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    row_number: int = Field(ge=2)
    account: str | None = None
    task_type: str | None = None
    task_description: str | None = None
    status: str | None = None
    date: str | None = None

    @field_validator("account")
    @classmethod
    def _check_account(cls, value: str | None) -> str | None:
        if value is not None and value not in ACCOUNTS:
            raise ValueError(f"account must be one of: {', '.join(ACCOUNTS)}")
        return value

    @field_validator("task_type")
    @classmethod
    def _check_task_type(cls, value: str | None) -> str | None:
        if value is not None and value not in TASK_TYPES:
            raise ValueError(f"task_type must be one of: {', '.join(TASK_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if value is not None and value not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        return value


class CreateRowsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    google_access_token: str = Field(min_length=10)
    date: str
    rows: list[RowIn] = Field(min_length=1)
    sheet_id: str = ""

    _check_sheet_id = field_validator("sheet_id")(normalize_sheet_id)

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str) -> str:
        if not is_valid_sheet_date(value):
            raise ValueError("date must use the D-MMM-YYYY format, e.g. 21-Oct-2026")
        return value.strip()


class UpdateRowsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    google_access_token: str = Field(min_length=10)
    updates: list[RowUpdate] = Field(min_length=1)
    sheet_id: str = ""

    _check_sheet_id = field_validator("sheet_id")(normalize_sheet_id)


class DownloadItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    row_number: int = Field(ge=2)
    account: str
    task_type: str
    canva_link: str

    @field_validator("account")
    @classmethod
    def _check_account(cls, value: str) -> str:
        if value not in ACCOUNTS:
            raise ValueError(f"account must be one of: {', '.join(ACCOUNTS)}")
        return value

    @field_validator("task_type")
    @classmethod
    def _check_task_type(cls, value: str) -> str:
        if value not in TASK_TYPES:
            raise ValueError(f"task_type must be one of: {', '.join(TASK_TYPES)}")
        return value

    @field_validator("canva_link")
    @classmethod
    def _check_canva_link(cls, value: str) -> str:
        if not is_valid_canva_link(value):
            raise ValueError(
                "canva_link must be an https link on canva.com, www.canva.com or canva.link"
            )
        return value.strip()


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    google_access_token: str = Field(min_length=10)
    date: str
    rows: list[DownloadItem] = Field(min_length=1)
    sheet_id: str = ""
    drive_folder_id: str = ""

    _check_sheet_id = field_validator("sheet_id")(normalize_sheet_id)
    _check_drive_folder_id = field_validator("drive_folder_id")(normalize_sheet_id)

    @field_validator("date")
    @classmethod
    def _check_date(cls, value: str) -> str:
        if not is_valid_sheet_date(value):
            raise ValueError("date must use the D-MMM-YYYY format, e.g. 21-Oct-2026")
        return value.strip()


class UserSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)
    sheet_id: str = ""
    drive_folder_id: str = ""

    _check_sheet_id = field_validator("sheet_id")(normalize_sheet_id)
    _check_drive_folder_id = field_validator("drive_folder_id")(normalize_sheet_id)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("email must be a valid email address")
        return value
