"""Explore a Google Drive folder tree for AI-based routing."""

from __future__ import annotations

import logging
from typing import Any

from models import AutomationError

log = logging.getLogger(__name__)

# Job-scoped cache: root_id -> list of tree nodes. Cleared at job start.
_tree_cache: dict[str, list[dict[str, Any]]] = {}


def clear_folder_tree_cache() -> None:
    _tree_cache.clear()


def explore_folder_tree(
    service: Any,
    root_id: str,
    *,
    max_depth: int = 3,
    max_nodes: int = 300,
) -> list[dict[str, Any]]:
    """BFS the folder tree under ``root_id``.

    Returns nodes: ``{"id", "name", "path", "depth"}`` (depth 0 is the root).
    Results are cached per root until ``clear_folder_tree_cache()``.
    """
    root_id = (root_id or "").strip()
    if not root_id:
        raise AutomationError(
            "DRIVE_FOLDER_MISSING",
            "No Drive folder configured for routing.",
        )
    cached = _tree_cache.get(root_id)
    if cached is not None:
        return cached

    root_name = _folder_name(service, root_id)
    tree: list[dict[str, Any]] = [
        {"id": root_id, "name": root_name, "path": root_name, "depth": 0}
    ]
    seen = {root_id}
    frontier: list[tuple[str, str]] = [(root_id, root_name)]  # (id, path)

    for depth in range(1, max_depth + 1):
        next_frontier: list[tuple[str, str]] = []
        for parent_id, parent_path in frontier:
            if len(tree) >= max_nodes:
                break
            for child_id, child_name in _list_subfolders(service, parent_id):
                if child_id in seen or len(tree) >= max_nodes:
                    continue
                seen.add(child_id)
                child_path = f"{parent_path}/{child_name}"
                tree.append(
                    {
                        "id": child_id,
                        "name": child_name,
                        "path": child_path,
                        "depth": depth,
                    }
                )
                next_frontier.append((child_id, child_path))
        frontier = next_frontier
        if not frontier or len(tree) >= max_nodes:
            break

    log.info(
        "explored folder tree under %s: %d node(s), depth ≤ %d",
        root_id,
        len(tree),
        max_depth,
    )
    _tree_cache[root_id] = tree
    return tree


def _folder_name(service: Any, folder_id: str) -> str:
    from googleapiclient.errors import HttpError

    from drive import _execute  # local import to avoid cycle at module load

    try:
        info = _execute(
            lambda: service.files()
            .get(
                fileId=folder_id,
                fields="id,name",
                supportsAllDrives=True,
            )
        )
        return str(info.get("name") or folder_id)
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if isinstance(exc, HttpError) and status in (403, 404):
            raise AutomationError(
                "DRIVE_FOLDER_MISSING",
                f"Drive folder {folder_id} was not found or is not accessible.",
            ) from exc
        if isinstance(exc, AutomationError):
            raise
        log.warning("could not read folder name for %s: %s", folder_id, exc)
        return folder_id


def _list_subfolders(service: Any, parent_id: str) -> list[tuple[str, str]]:
    from drive import _execute

    response = _execute(
        lambda: service.files().list(
            q=(
                f"'{parent_id}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            ),
            spaces="drive",
            fields="files(id,name)",
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
    )
    return [
        (str(item["id"]), str(item["name"]))
        for item in response.get("files", [])
        if item.get("id") and item.get("name")
    ]
