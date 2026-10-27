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

#: Continuation marker written in the Date column when a date block continues.
CONTINUATION = "||"


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
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    width = max(headers.values()) + 1
    values = []
    for row in rows:
        values.append(
            _row_values(
                width,
                headers,
                {
                    "Date": "",
                    "Business/Account": row.account,
                    "Task Type": row.task_type,
                    "Task Description": row.task_description,
                    "Status": row.status,
                    "Post Link": row.canva_link,
                },
            )
        )
    return _append_block(service, sid, settings, tab, headers, width, date, rows, values)


def read_rows(token: str, date: str, sheet_id: str = "") -> list[dict]:
    """Read the date block for ``date`` from the Sheet.

    A block starts at the first row whose Date cell equals ``date`` and runs
    until the next row that carries a different date.
    """
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    width = max(headers.values()) + 1

    cells = _data_cells(service, sid, tab, width)
    rows: list[dict] = []
    for sheet_row, row in _block_rows(cells, headers, date):
        rows.append(
            {
                "row_number": sheet_row,
                "date": date,
                "account": row[headers["Business/Account"]].strip(),
                "task_type": row[headers["Task Type"]].strip(),
                "task_description": row[headers["Task Description"]].strip(),
                "status": row[headers["Status"]].strip(),
                "post_link": row[headers["Post Link"]].strip(),
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
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    width = max(headers.values()) + 1
    values = []
    for row in rows:
        values.append(
            _row_values(
                width,
                headers,
                {
                    "Date": "",
                    "Business/Account": row.account,
                    "Task Type": row.task_type,
                    "Task Description": row.task_description,
                    "Status": row.status,
                    "Post Link": "",
                },
            )
        )
    return _append_block(service, sid, settings, tab, headers, width, date, rows, values)


def update_rows(token: str, updates: list[dict], sheet_id: str = "") -> None:
    """Batch-update individual cells in existing rows.

    Only the changed cells are written, so existing Date and Post Link values
    are never overwritten. A Date cell that a past sync wiped is restored from
    the update's supplied date (only when the cell is empty).
    """
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if not updates:
        return

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)

    field_to_header = {
        "account": "Business/Account",
        "task_type": "Task Type",
        "task_description": "Task Description",
        "status": "Status",
    }

    date_column = _column_letter(headers["Date"])
    date_cells = _get_values(
        service, sid, _tab_range(tab, f"{date_column}2:{date_column}")
    )

    data: list[dict] = []
    for update in updates:
        row_number = update["row_number"]
        if row_number < 2:
            raise AutomationError("SHEET_ROW_NOT_FOUND", f"Invalid row number: {row_number}")
        for field, header in field_to_header.items():
            value = update.get(field)
            if value is None:
                continue
            column = _column_letter(headers[header])
            data.append(
                {
                    "range": _tab_range(tab, f"{column}{row_number}"),
                    "values": [[value]],
                }
            )
        # Repair only: restore the Date cell when an earlier buggy sync wiped it.
        supplied_date = update.get("date")
        if supplied_date:
            index = row_number - 2
            current = _cell_value(date_cells[index : index + 1]) if index < len(date_cells) else ""
            if not current:
                data.append(
                    {
                        "range": _tab_range(tab, f"{date_column}{row_number}"),
                        "values": [[supplied_date]],
                    }
                )

    if not data:
        return
    _execute(
        lambda: service.spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": data},
        )
    )


def _row_has_real_content(row: list[str], headers: dict[str, int]) -> bool:
    """True when a row carries task data (Status alone does not count)."""
    for header in ("Date", "Business/Account", "Task Type", "Task Description", "Post Link"):
        column = headers.get(header)
        if column is None or column >= len(row):
            continue
        if str(row[column]).strip():
            return True
    return False


def _clear_hollow_rows(
    service, sid: str, settings, tab: str, headers: dict[str, int], width: int
) -> int:
    """Blank out rows that have no task data (e.g. Status-only leftovers).

    Returns the number of rows cleared. Keeps sheet_row numbers stable —
    values are cleared, rows are not deleted.
    """
    cells = _data_cells(service, sid, tab, width)
    last_col = _column_letter(width - 1)
    cleared = 0
    batch: list[dict] = []
    for index, row in enumerate(cells):
        if _row_has_real_content(row, headers):
            continue
        if not any(str(cell).strip() for cell in row):
            continue  # already fully empty
        sheet_row = index + 2
        batch.append(
            {
                "range": _tab_range(tab, f"A{sheet_row}:{last_col}{sheet_row}"),
                "values": [[""] * width],
            }
        )
        cleared += 1
    if batch:
        _execute(
            lambda: service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption": "RAW", "data": batch},
            )
        )
        log.info("cleared %d hollow row(s) on tab %r", cleared, tab)
    return cleared


def _append_block(
    service,
    sid: str,
    settings,
    tab: str,
    headers: dict[str, int],
    width: int,
    date: str,
    rows: list[RowInput],
    values: list[list[str]],
) -> dict[str, int]:
    """Place ``values`` into ``date``'s block.

    The first row of a brand-new block carries the Date; every later row gets
    the '||' continuation marker so a date appears only once per block. When
    the block already exists, new rows join it — at the end of the sheet when
    the block is last, or inserted directly after it when a later date block
    follows.
    """
    _clear_hollow_rows(service, sid, settings, tab, headers, width)
    dates = [
        row[headers["Date"]].strip()
        for row in _data_cells(service, sid, tab, width)
    ]
    bounds = _date_block_bounds(dates, date)

    if bounds is None:
        values[0][headers["Date"]] = date
        for value_row in values[1:]:
            value_row[headers["Date"]] = CONTINUATION
        return _append_values(service, sid, settings, tab, rows, values, headers)

    _start, end = bounds
    for value_row in values:
        value_row[headers["Date"]] = CONTINUATION

    # "Last block" means no further real content after it (ignore trailing blanks).
    last_real = max(
        (i for i, d in enumerate(dates) if d),
        default=len(dates) - 1,
    )
    if end >= last_real:
        return _append_values(service, sid, settings, tab, rows, values, headers)

    first_row = end + 3
    count = len(values)
    sheet_id_num = _numeric_sheet_id(service, sid, tab)
    _execute(
        lambda: service.spreadsheets()
        .batchUpdate(
            spreadsheetId=sid,
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheet_id_num,
                                "dimension": "ROWS",
                                "startIndex": first_row - 1,
                                "endIndex": first_row - 1 + count,
                            },
                            "inheritBefore": True,
                        }
                    }
                ]
            },
        )
    )
    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(
                tab,
                f"A{first_row}:{_column_letter(width - 1)}{first_row + count - 1}",
            ),
            valueInputOption=settings.date_input_mode,
            body={"values": values},
        )
    )
    return {row.row_id: first_row + index for index, row in enumerate(rows)}


def _last_used_row(
    service, spreadsheet_id: str, tab: str, headers: dict[str, int] | None = None
) -> int:
    """1-based sheet row of the last row with real task content.

    Status-only hollow rows are ignored so append never lands after junk.
    """
    values = _get_values(service, spreadsheet_id, _tab_range(tab, "A:Z"))
    last = 1
    for index, row_values in enumerate(values, start=1):
        row = list(row_values)
        if headers is not None:
            if _row_has_real_content(row, headers):
                last = index
        elif any(str(cell).strip() for cell in row):
            last = index
    return last


def _append_values(
    service,
    sid: str,
    settings,
    tab: str,
    rows: list[RowInput],
    values: list[list[str]],
    headers: dict[str, int] | None = None,
) -> dict[str, int]:
    """Write rows at the first empty row after the last real content row."""
    if not rows:
        return {}
    first_row = _last_used_row(service, sid, tab, headers) + 1
    width = max(len(value_row) for value_row in values)
    last_col = _column_letter(width - 1)
    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(tab, f"A{first_row}:{last_col}{first_row + len(values) - 1}"),
            valueInputOption=settings.date_input_mode,
            body={"values": values},
        )
    )
    return {row.row_id: first_row + index for index, row in enumerate(rows)}


def _date_block_bounds(dates: list[str], target: str) -> tuple[int, int] | None:
    """0-based ``[start, end]`` of ``target``'s block, or None when absent.

    The block starts at the first row whose Date equals ``target`` and ends
    at the last '||' continuation (or repeat of ``target``). An empty Date
    cell ends the block so hollow rows between dates never join the block.
    """
    start: int | None = None
    end: int | None = None
    for index, raw in enumerate(dates):
        cell = (raw or "").strip()
        if start is None:
            if cell == target:
                start = index
                end = index
            continue
        if cell == CONTINUATION or cell == target:
            end = index
        else:
            # Different date or empty Date → block is over.
            break
    if start is None or end is None:
        return None
    return start, end


def _block_rows(
    cells: list[list[str]], headers: dict[str, int], target: str
) -> list[tuple[int, list[str]]]:
    """``(sheet_row, cells)`` for the content rows of ``target``'s block."""
    dates = [row[headers["Date"]].strip() for row in cells]
    bounds = _date_block_bounds(dates, target)
    if bounds is None:
        return []
    start, end = bounds
    rows: list[tuple[int, list[str]]] = []
    for index in range(start, end + 1):
        row = cells[index]
        # Status alone is not a task — skip hollow leftovers inside a block.
        if not _row_has_real_content(row, headers):
            continue
        rows.append((index + 2, row))
    return rows


def _data_cells(service, spreadsheet_id: str, tab: str, width: int) -> list[list[str]]:
    """All data rows (from row 2), padded to ``width`` columns."""
    values = _get_values(
        service, spreadsheet_id, _tab_range(tab, f"A2:{_column_letter(width - 1)}")
    )
    return [list(row_values) + [""] * (width - len(row_values)) for row_values in values]


def _numeric_sheet_id(service, spreadsheet_id: str, tab: str) -> int:
    meta = _execute(
        lambda: service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))")
    )
    for sheet in meta.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == tab:
            return int(properties["sheetId"])
    raise AutomationError("SHEET_NOT_FOUND", f"Sheet tab {tab!r} was not found.")


def set_drive_link(token: str, row_number: int, drive_url: str, sheet_id: str = "") -> None:
    """Write the Drive share URL to the Post Link column at the given row.

    The cell is written as a native Drive smart chip (file chip). If the chip
    write is rejected, the plain URL is written instead so uploads never fail
    over a cosmetic difference.
    """
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if row_number < 2:
        raise AutomationError("SHEET_ROW_NOT_FOUND", "The Sheet row number is invalid.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    post_column = _column_letter(headers["Post Link"])

    try:
        _write_drive_chip(service, sid, tab, row_number, headers["Post Link"], drive_url)
        return
    except AutomationError as exc:
        log.warning("Drive chip write failed (%s); falling back to a plain link", exc.code)

    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(tab, f"{post_column}{row_number}"),
            valueInputOption="RAW",
            body={"values": [[drive_url]]},
        )
    )


def set_status(
    token: str, row_number: int, status: str, sheet_id: str = ""
) -> None:
    """Write the Status cell for a row (used to mark uploads Done)."""
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if row_number < 2:
        raise AutomationError("SHEET_ROW_NOT_FOUND", "The Sheet row number is invalid.")

    settings = get_settings()
    sid = _resolve_sheet_id(sheet_id)
    service = _service(token)
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    status_column = _column_letter(headers["Status"])
    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(tab, f"{status_column}{row_number}"),
            valueInputOption="RAW",
            body={"values": [[status]]},
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
    tab = _resolve_tab(service, sid, settings.sheet_tab)
    headers = _header_map(service, tab, sid)
    post_column = _column_letter(headers["Post Link"])
    expected_range = _tab_range(tab, f"{post_column}{row_number}")
    expected = _get_values(service, sid, expected_range)
    target_row = row_number
    if _cell_value(expected) != canva_link:
        column_values = _get_values(
            service,
            sid,
            _tab_range(tab, f"{post_column}:{post_column}"),
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

    try:
        _write_drive_chip(service, sid, tab, target_row, headers["Post Link"], drive_url)
        return
    except AutomationError as exc:
        log.warning("Drive chip write failed (%s); falling back to a plain link", exc.code)

    _execute(
        lambda: service.spreadsheets()
        .values()
        .update(
            spreadsheetId=sid,
            range=_tab_range(tab, f"{post_column}{target_row}"),
            valueInputOption="RAW",
            body={"values": [[drive_url]]},
        )
    )


def _write_drive_chip(
    service,
    spreadsheet_id: str,
    tab: str,
    row_number: int,
    column_index: int,
    url: str,
) -> None:
    """Write a Drive file smart chip into a single cell (Sheets native chip)."""
    sheet_id_num = _numeric_sheet_id(service, spreadsheet_id, tab)
    _execute(
        lambda: service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "updateCells": {
                            "range": {
                                "sheetId": sheet_id_num,
                                "startRowIndex": row_number - 1,
                                "endRowIndex": row_number,
                                "startColumnIndex": column_index,
                                "endColumnIndex": column_index + 1,
                            },
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {"stringValue": "@"},
                                            "chipRuns": [
                                                {
                                                    "chip": {
                                                        "richLinkProperties": {"uri": url}
                                                    }
                                                }
                                            ],
                                        }
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue,chipRuns",
                        }
                    }
                ]
            },
        )
    )


def _service(token: str):
    credentials = Credentials(token=token)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _resolve_tab(service, spreadsheet_id: str, tab: str) -> str:
    """Return the tab to operate on.

    Uses the configured tab when it exists. Otherwise falls back to the first
    tab whose header row contains every required header, and raises a clear
    error listing the available tabs when none matches.
    """
    info = _execute(
        lambda: service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(title))")
    )
    titles = [
        props["properties"]["title"]
        for props in info.get("sheets", [])
        if props.get("properties", {}).get("title")
    ]
    if tab in titles:
        return tab
    for title in titles:
        try:
            values = _get_values(service, spreadsheet_id, _tab_range(title, "1:1"))
        except AutomationError:
            continue
        row1 = {str(value).strip().casefold() for value in (values[0] if values else [])}
        if all(header.casefold() in row1 for header in _REQUIRED_HEADERS):
            log.warning("Sheet tab %r not found; using tab %r instead", tab, title)
            return title
    raise AutomationError(
        "SHEET_TAB_NOT_FOUND",
        f"Tab '{tab}' does not exist in this spreadsheet, and no tab has the required "
        f"header row ({', '.join(_REQUIRED_HEADERS)}). "
        f"Tabs found: {', '.join(titles) or 'none'}.",
    )


def _header_map(service, tab: str, spreadsheet_id: str) -> dict[str, int]:
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
