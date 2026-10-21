"""Manual Google Sheets acceptance-test client.

Set TEST_TOKEN and run from ``server/``:
  python sheets_cli.py append --date 21-Oct-2026
  python sheets_cli.py setlink --row 412 --canva-link https://... --drive-url https://...
"""

from __future__ import annotations

import argparse
import os
import sys

from models import RowInput
import sheets


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Google Sheets module.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    append = subparsers.add_parser("append")
    append.add_argument("--date", required=True)
    append.add_argument("--account", default="Excel Executive Cars")
    append.add_argument("--task-type", default="Video/Reel Editing")
    append.add_argument("--description", default="Sheets module acceptance test")
    append.add_argument("--status", default="Done")
    append.add_argument("--canva-link", default="https://canva.link/sheets-module-test")

    setlink = subparsers.add_parser("setlink")
    setlink.add_argument("--row", type=int, required=True)
    setlink.add_argument("--canva-link", required=True)
    setlink.add_argument("--drive-url", required=True)
    args = parser.parse_args()

    token = os.getenv("TEST_TOKEN", "")
    if not token:
        print("TEST_TOKEN is required.", file=sys.stderr)
        return 2

    try:
        if args.command == "append":
            row = RowInput(
                row_id="sheets-cli-test",
                account=args.account,
                task_type=args.task_type,
                task_description=args.description,
                status=args.status,
                canva_link=args.canva_link,
            )
            print(sheets.append_rows(token, args.date, [row]))
        else:
            sheets.set_post_link(token, args.row, args.canva_link, args.drive_url)
            print("updated")
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
