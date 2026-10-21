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

STATUSES: tuple[str, ...] = ("Done", "In Progress", "Completed")

DEFAULT_STATUS = "Done"

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
    requested_by: str | None = None

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
