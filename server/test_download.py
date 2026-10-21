"""CLI: download one real Canva design (Module 2 acceptance test).

    python test_download.py <canva edit link> [--dest ./test_downloads]

Prints the saved name, size and type.  Carousels (.zip) are extracted and the
folder is reported instead of the zip.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from canva_bot import download_design
from models import AutomationError


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Download a Canva design like a human.")
    parser.add_argument("link", help="Canva edit link (https://www.canva.com/design/...)")
    parser.add_argument(
        "--dest",
        default="./test_downloads",
        help="destination directory (default: ./test_downloads)",
    )
    args = parser.parse_args()

    try:
        result = download_design(args.link, Path(args.dest))
    except AutomationError as exc:
        print(f"FAILED [{exc.code}] {exc.message}", file=sys.stderr)
        return 1

    kind = "folder" if result.is_directory else "file"
    print(f"name: {result.name}")
    print(f"path: {result.path}")
    print(f"type: {kind}")
    print(f"size: {result.size} bytes")
    if result.is_directory:
        members = sorted(p for p in result.path.rglob("*") if p.is_file())
        print(f"members: {len(members)}")
        for member in members:
            print(f"  - {member.relative_to(result.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
