"""Google Sheets client for the calendar append/update workflow."""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import get_settings
from models import AutomationError, RowInput

log = logging.getLogger(__name__)

_REQUIRED_HEADERS = (
    "Date",
    "Business/Account",
    "Task Type",
    "Task Description",
    "Status",
    "Post Link",
)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_A1_COLUMN_RE = re.compile(r"^[A-Z]+$")
_UPDATED_RANGE_RE = re.compile(r"!([A-Z]+)(\d+)(?::[A-Z]+(\d+))?$")


def _resolve_sheet_id(sheet_id: str | None) -> str:
    """Per-request Sheet ID, falling back to the server default from the env."""
    resolved = (sheet_id or "").strip() or get_settings().sheet_id
    if not resolved:
        raise AutomationError(
            "SHEET_ID_MISSING",
            "No Google Sheet ID provided. Paste a Sheet ID in the app or set SHEET_ID on the server.",
        )
    return resolved


def append_rows(token: str, date: str, rows: list[RowInput], sheet_id: str = "") -> dict[str, int]:
    """Append calendar rows and return their 1-based Sheet row numbers."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if not rows:
        return {}

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    width = max(headers.values()) + 1
    values = []
    for row in rows:
        values.append(
            _row_values(
                width,
                headers,
                {
                    "Date": date,
                    "Business/Account": row.account,
                    "Task Type": row.task_type,
                    "Task Description": row.task_description,
                    "Status": row.status,
                    "Post Link": row.canva_link,
                },
            )
        )

    response = _execute(
        lambda: service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sid,
            range=_tab_range(settings.sheet_tab, "A1"),
            valueInputOption=settings.date_input_mode,
            insertDataOption="INSERT_ROWS",
            includeValuesInResponse=False,
            body={"values": values},
        )
    )
    first_row = _first_row_from_updated_range(response.get("updates", {}).get("updatedRange", ""))
    if first_row is None:
        raise AutomationError(
            "SHEET_APPEND_FAILED",
            "Google Sheets did not return the range of the appended rows.",
        )
    return {row.row_id: first_row + index for index, row in enumerate(rows)}


def read_rows(token: str, date: str, sheet_id: str = "") -> list[dict]:
    """Read all data rows matching ``date`` from the Sheet."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    width = max(headers.values()) + 1

    values = _get_values(service, sid, _tab_range(settings.sheet_tab, f"A2:{_column_letter(width - 1)}"))
    rows: list[dict] = []
    for index, row_values in enumerate(values):
        sheet_row = index + 2
        cells = list(row_values) + [""] * (width - len(row_values))
        row_date = str(cells[headers["Date"]]).strip()
        if row_date != date:
            continue
        rows.append(
            {
                "row_number": sheet_row,
                "date": row_date,
                "account": str(cells[headers["Business/Account"]]).strip(),
                "task_type": str(cells[headers["Task Type"]]).strip(),
                "task_description": str(cells[headers["Task Description"]]).strip(),
                "status": str(cells[headers["Status"]]).strip(),
                "post_link": str(cells[headers["Post Link"]]).strip(),
            }
        )
    return rows


def create_rows(token: str, date: str, rows: list[RowInput], sheet_id: str = "") -> dict[str, int]:
    """Append rows to the Sheet with an empty Post Link column."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if not rows:
        return {}

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    width = max(headers.values()) + 1
    values = []
    for row in rows:
        values.append(
            _row_values(
                width,
                headers,
                {
                    "Date": date,
                    "Business/Account": row.account,
                    "Task Type": row.task_type,
                    "Task Description": row.task_description,
                    "Status": row.status,
                    "Post Link": "",
                },
            )
        )

    response = _execute(
        lambda: service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sid,
            range=_tab_range(settings.sheet_tab, "A1"),
            valueInputOption=settings.date_input_mode,
            insertDataOption="INSERT_ROWS",
            includeValuesInResponse=False,
            body={"values": values},
        )
    )
    first_row = _first_row_from_updated_range(response.get("updates", {}).get("updatedRange", ""))
    if first_row is None:
        raise AutomationError(
            "SHEET_APPEND_FAILED",
            "Google Sheets did not return the range of the appended rows.",
        )
    return {row.row_id: first_row + index for index, row in enumerate(rows)}


def update_rows(token: str, updates: list[dict], sheet_id: str = "") -> None:
    """Batch-update cells in existing rows. Does not touch Post Link."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if not updates:
        return

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    width = max(headers.values()) + 1

    field_to_header = {
        "account": "Business/Account",
        "task_type": "Task Type",
        "task_description": "Task Description",
        "status": "Status",
    }

    for update in updates:
        row_number = update["row_number"]
        if row_number < 2:
            raise AutomationError("SHEET_ROW_NOT_FOUND", f"Invalid row number: {row_number}")

        values_map: dict[str, str] = {"Date": ""}
        for field, header in field_to_header.items():
            value = update.get(field)
            if value is not None:
                values_map[header] = value

        row_vals = [""] * width
        for header, value in values_map.items():
            if header in headers and value:
                row_vals[headers[header]] = value

        _execute(
            lambda rn=row_number, rv=row_vals: service.spreadsheets()
            .values()
            .update(
                spreadsheetId=sid,
                range=_tab_range(settings.sheet_tab, f"A{rn}:{_column_letter(width - 1)}{rn}"),
                valueInputOption="RAW",
                body={"values": [rv]},
            )
        )


def set_drive_link(token: str, row_number: int, drive_url: str, sheet_id: str = "") -> None:
    """Write the Drive share URL to the Post Link column at the given row."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if row_number < 2:
        raise AutomationError("SHEET_ROW_NOT_FOUND", "The Sheet row number is invalid.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    post_column = _column_letter(headers["Post Link"])

    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(settings.sheet_tab, f"{post_column}{row_number}"),
            valueInputOption="RAW",
            body={"values": [[drive_url]]},
        )
    )


def set_post_link(token: str, row_number: int, canva_link: str, drive_url: str, sheet_id: str = "") -> None:
    """Replace only the matching Post Link cell with the Drive URL."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if row_number < 1:
        raise AutomationError("SHEET_ROW_NOT_FOUND", "The Sheet row number is invalid.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    headers = _header_map(service, settings.sheet_tab, sid)
    post_column = _column_letter(headers["Post Link"])
    expected_range = _tab_range(settings.sheet_tab, f"{post_column}{row_number}")
    expected = _get_values(service, sid, expected_range)
    target_row = row_number
    if _cell_value(expected) != canva_link:
        column_values = _get_values(
            service,
            sid,
            _tab_range(settings.sheet_tab, f"{post_column}:{post_column}"),
        )
        matches = [
            index + 1
            for index, values in enumerate(column_values)
            if index >= 1 and _cell_value(values) == canva_link
        ]
        if not matches:
            raise AutomationError(
                "SHEET_ROW_NOT_FOUND",
                "Could not find the Canva link in the Post Link column.",
            )
        target_row = matches[-1]

    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(settings.sheet_tab, f"{post_column}{target_row}"),
            valueInputOption="RAW",
            body={"values": [[drive_url]]},
        )
    )


def _service(token: str):
    credentials = Credentials(token=token)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _header_map(service, spreadsheet_id: str, tab: str) -> dict[str, int]:
    values = _get_values(service, spreadsheet_id, _tab_range(tab, "1:1"))
    raw_headers = values[0] if values else []
    normalized = {str(value).strip().casefold(): index for index, value in enumerate(raw_headers)}
    missing = [header for header in _REQUIRED_HEADERS if header.casefold() not in normalized]
    if missing:
        raise AutomationError(
            "SHEET_HEADERS_MISSING",
            f"Sheet is missing required header(s): {', '.join(missing)}.",
        )
    return {header: normalized[header.casefold()] for header in _REQUIRED_HEADERS}


def _row_values(width: int, headers: dict[str, int], values: dict[str, str]) -> list[str]:
    row = [""] * width
    for header, value in values.items():
        row[headers[header]] = value
    return row


def _get_values(service, spreadsheet_id: str, cell_range: str) -> list[list[object]]:
    response = _execute(
        lambda: service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=cell_range)
    )
    return response.get("values", [])


def _execute(request_factory: Callable[[], object]):
    for attempt in range(4):
        try:
            return request_factory().execute()
        except (RefreshError, HttpError) as exc:
            status = _http_status(exc)
            if status == 401:
                raise AutomationError(
                    "TOKEN_EXPIRED",
                    "Google access token expired. Sign in again and resubmit.",
                ) from exc
            if status == 403:
                raise AutomationError(
                    "SHEET_PERMISSION_DENIED",
                    "The Google account has no edit access to the Sheet.",
                ) from exc
            if status == 404:
                raise AutomationError(
                    "SHEET_NOT_FOUND",
                    "The configured Google Sheet was not found.",
                ) from exc
            if status not in _RETRYABLE_STATUS_CODES or attempt == 3:
                raise AutomationError(
                    "SHEET_API_FAILED",
                    f"Google Sheets request failed{f' with HTTP {status}' if status else ''}.",
                ) from exc
            delay = (2**attempt) + random.uniform(0, 0.25)
            log.warning("transient Google Sheets error; retrying in %.2fs", delay)
            time.sleep(delay)


def _http_status(exc: Exception) -> int | None:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None)
    return 401 if isinstance(exc, RefreshError) else None


def _tab_range(tab: str, a1_range: str) -> str:
    escaped = tab.replace("'", "''")
    return f"'{escaped}'!{a1_range}"


def _cell_value(values: list[list[object]]) -> str:
    if not values or not values[0]:
        return ""
    return str(values[0][0]).strip()


def _column_letter(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _first_row_from_updated_range(updated_range: str) -> int | None:
    match = _UPDATED_RANGE_RE.search(updated_range)
    return int(match.group(2)) if match else None
