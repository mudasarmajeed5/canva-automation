"""Inline tests for hollow-row append fixes (not committed as a suite)."""

import sys
from unittest.mock import MagicMock, patch

import sheets
from sheets import CONTINUATION

HEADERS = {
    "Date": 0,
    "Business/Account": 1,
    "Task Type": 2,
    "Task Description": 3,
    "Status": 4,
    "Post Link": 5,
}
WIDTH = 6


def test_date_block_bounds_stops_at_empty_date():
    dates = ["26-Oct-2026"] + [CONTINUATION] * 7 + [""] * 18 + ["27-Oct-2026", ""]
    bounds = sheets._date_block_bounds(dates, "26-Oct-2026")
    assert bounds == (0, 7), bounds
    bounds27 = sheets._date_block_bounds(dates, "27-Oct-2026")
    # trailing empty after 27 ends the block at the 27 row itself
    assert bounds27 == (26, 26), bounds27


def test_row_has_real_content_ignores_status_only():
    hollow = ["", "", "", "", "Not Started", ""]
    real = ["26-Oct-2026", "", "", "", "Done", ""]
    task = ["", "Excel", "T", "d", "Not Started", ""]
    assert not sheets._row_has_real_content(hollow, HEADERS)
    assert sheets._row_has_real_content(real, HEADERS)
    assert sheets._row_has_real_content(task, HEADERS)


def test_block_rows_skips_hollow():
    cells = []
    cells.append(["26-Oct-2026", "Excel", "T", "d1", "Done", "u"])
    cells.append([CONTINUATION, "Excel", "T", "d2", "Done", "u"])
    for _ in range(3):
        cells.append(["", "", "", "", "Not Started", ""])
    cells.append(["27-Oct-2026", "Excel", "T", "d3", "Not Started", ""])
    rows = sheets._block_rows(cells, HEADERS, "26-Oct-2026")
    assert [r[0] for r in rows] == [2, 3], rows
    rows27 = sheets._block_rows(cells, HEADERS, "27-Oct-2026")
    assert [r[0] for r in rows27] == [7], rows27


def test_last_used_row_ignores_status_only():
    grid = [
        ["Date", "Business/Account", "Task Type", "Task Description", "Status", "Post Link"],
        ["26-Oct-2026", "Excel", "T", "d", "Done", "u"],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "Not Started", ""],
    ]
    with patch.object(sheets, "_get_values", return_value=grid):
        last = sheets._last_used_row(MagicMock(), "sid", "Sheet1", HEADERS)
    assert last == 2, last


def test_clear_hollow_rows_clears_status_only():
    cells = [
        ["26-Oct-2026", "Excel", "T", "d", "Done", "u"],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "", ""],  # fully empty → leave alone
        ["27-Oct-2026", "Excel", "T", "d2", "Not Started", ""],
    ]
    service = MagicMock()
    batch = MagicMock()
    service.spreadsheets.return_value.values.return_value.batchUpdate.return_value = batch
    with patch.object(sheets, "_data_cells", return_value=cells), patch.object(
        sheets, "_execute", side_effect=lambda fn: fn()
    ):
        cleared = sheets._clear_hollow_rows(
            service, "sid", sheets.get_settings(), "Saroosh (GD)", HEADERS, WIDTH
        )
    assert cleared == 1, cleared
    call = service.spreadsheets.return_value.values.return_value.batchUpdate.call_args
    body = call.kwargs.get("body") or call[1].get("body")
    data = body["data"]
    assert len(data) == 1
    assert data[0]["range"].endswith("A3:F3")
    assert data[0]["values"] == [[""] * WIDTH]


def test_append_block_mid_sheet_inserts_after_real_content_not_hollows():
    """New 26-Oct rows must join the real block, not land after Status-only junk."""
    cells = []
    cells.append(["26-Oct-2026", "Excel", "T", "d1", "Done", ""])
    cells.append([CONTINUATION, "Excel", "T", "d2", "Done", ""])
    for _ in range(5):
        cells.append(["", "", "", "", "Not Started", ""])
    cells.append(["27-Oct-2026", "Excel", "T", "d3", "Not Started", ""])

    service = MagicMock()
    settings = sheets.get_settings()
    rows = [
        sheets.RowInput(row_id="10", account="Excel", task_type="T", task_description="d-new", status="Not Started", canva_link="")
    ]
    values = [["", "Excel", "T", "d-new", "Not Started", ""]]

    insert_mock = MagicMock()
    update_mock = MagicMock()
    get_mock = MagicMock()
    get_mock.execute.return_value = {"valueRanges": [{"values": [["Date"]]}]}
    meta_mock = MagicMock()
    meta_mock.execute.return_value = {"sheets": [{"properties": {"sheetId": 42, "title": "Saroosh (GD)"}}]}

    def fake_spreadsheets_get(**kwargs):
        if "fields" in kwargs and "sheetId" in kwargs:
            return meta_mock
        return get_mock

    with patch.object(sheets, "_data_cells", return_value=cells), patch.object(
        sheets, "_clear_hollow_rows", return_value=0
    ), patch.object(sheets, "_numeric_sheet_id", return_value=42), patch.object(
        sheets, "_execute", side_effect=lambda fn: fn()
    ), patch.object(
        sheets, "_get_values", return_value=grid_for_last_used()
    ):
        service.spreadsheets.return_value.spreadsheets.return_value.batchUpdate.return_value = MagicMock()
        service.spreadsheets.return_value.values.return_value.update.return_value = MagicMock()
        # batchUpdate on service.spreadsheets() for insertDimension
        service.spreadsheets.return_value.batchUpdate.return_value = MagicMock()
        service.spreadsheets.return_value.values.return_value.update.return_value = MagicMock()

        mapped = sheets._append_block(
            service, "sid", settings, "Saroosh (GD)", HEADERS, WIDTH,
            "26-Oct-2026", rows, values,
        )

    # end of real 26-Oct block = index 1 → sheet row 3 → first_row = 1+3 = 4
    assert mapped == {'10': 4}, mapped
    # insertDimension called with startIndex = first_row-1 = 3
    insert_body = service.spreadsheets.return_value.batchUpdate.call_args.kwargs.get("body") or \
        service.spreadsheets.return_value.batchUpdate.call_args[1]["body"]
    req = insert_body["requests"][0]["insertDimension"]
    assert req["range"]["startIndex"] == 3, req
    assert req["range"]["endIndex"] == 4


def grid_for_last_used():
    return [
        ["Date", "Business/Account", "Task Type", "Task Description", "Status", "Post Link"],
        ["26-Oct-2026", "Excel", "T", "d1", "Done", ""],
        [CONTINUATION, "Excel", "T", "d2", "Done", ""],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "Not Started", ""],
        ["", "", "", "", "Not Started", ""],
        ["27-Oct-2026", "Excel", "T", "d3", "Not Started", ""],
    ]


def test_append_values_uses_last_real_row():
    grid = grid_for_last_used()
    service = MagicMock()
    with patch.object(sheets, "_get_values", return_value=grid), patch.object(
        sheets, "_execute", side_effect=lambda fn: fn()
    ):
        service.spreadsheets.return_value.values.return_value.update.return_value = MagicMock()
        rows = [sheets.RowInput(row_id="1", account="A", task_type="T", task_description="D", status="Not Started", canva_link="")]
        values = [["28-Oct-2026", "A", "T", "D", "Not Started", ""]]
        mapped = sheets._append_values(
            service, "sid", sheets.get_settings(), "Saroosh (GD)", rows, values, HEADERS
        )
    # last real row is index 8 → sheet row 9 → write at 10
    assert mapped == {'1': 10}, mapped
    call = service.spreadsheets.return_value.values.return_value.update.call_args
    body = call.kwargs.get("body") or call[1].get("body")
    rng = call.kwargs.get("range") or call[1].get("range")
    assert body["values"] == values
    assert rng.endswith("!A10:F10") or rng.startswith("A10:"), rng


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:
            failed += 1
            import traceback
            print(f"FAIL {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
