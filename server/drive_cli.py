"""Manual Google Drive acceptance-test client."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import drive
from models import DownloadedFile


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload one file to Google Drive.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--account", required=True)
    parser.add_argument("--task-type", required=True)
    args = parser.parse_args()
    token = os.getenv("TEST_TOKEN", "")
    if not token:
        print("TEST_TOKEN is required.", file=sys.stderr)
        return 2
    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        return 2
    try:
        result = drive.upload_and_share(
            token,
            args.account,
            args.task_type,
            DownloadedFile(args.file, args.file.name, args.file.stat().st_size),
        )
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"id: {result.file_id}")
    print(f"url: {result.web_view_link}")
    print(f"folder: {result.folder_path}")
    print(f"size: {result.size}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
