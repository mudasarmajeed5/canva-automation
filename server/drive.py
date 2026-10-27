"""Google Drive v3 uploader and data-driven folder router."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import random
import re
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import routing
from config import get_settings
from models import AutomationError, DownloadedFile, UploadResult

log = logging.getLogger(__name__)
_RETRYABLE = {429, 500, 502, 503, 504}
_folder_cache: dict[tuple[str, str], tuple[str, str]] = {}


def clear_folder_cache() -> None:
    _folder_cache.clear()
    try:
        import folder_tree

        folder_tree.clear_folder_tree_cache()
    except Exception:
        log.exception("could not clear folder tree cache")


def upload_and_share(
    token: str,
    account: str,
    task_type: str,
    file: DownloadedFile,
    root_folder_id: str = "",
) -> UploadResult:
    if not token:
        raise AutomationError("TOKEN_EXPIRED", "Google access token is missing.")
    if not file.path.exists():
        raise AutomationError("DRIVE_UPLOAD_FAILED", f"Local file does not exist: {file.path.name}")

    settings = get_settings()
    root_id = root_folder_id.strip() or settings.root_folder_id
    if not root_id:
        raise AutomationError(
            "DRIVE_FOLDER_MISSING",
            "No Drive folder configured. Paste a Drive folder ID in the app or set ROOT_FOLDER_ID on the server.",
        )
    service = build("drive", "v3", credentials=Credentials(token=token), cache_discovery=False)
    warnings: list[str] = []

    target_id, folder_path, ai_used = _ai_route_target(
        service, root_id, file, account, task_type, settings, warnings
    )

    if not ai_used:
        target_id, folder_path, keyword_warnings = _keyword_route_target(
            service, root_id, account, task_type, file
        )
        warnings.extend(keyword_warnings)

    if file.is_directory:
        uploaded_id = _create_folder(service, target_id, file.name)
        total = 0
        for child in sorted(path for path in file.path.rglob("*") if path.is_file()):
            uploaded = _upload_one(service, child, uploaded_id)
            _verify(service, uploaded[0], child, uploaded[1])
            total += uploaded[1]
        _share(service, uploaded_id)
        return UploadResult(
            uploaded_id,
            _link(service, uploaded_id),
            f"{folder_path}/{file.name}",
            total,
            warnings,
        )

    remote_id, size = _upload_one(service, file.path, target_id)
    _verify(service, remote_id, file.path, size)
    _share(service, remote_id)
    return UploadResult(remote_id, _link(service, remote_id), folder_path, size, warnings)


def _ai_route_target(
    service,
    root_id: str,
    file: DownloadedFile,
    account: str,
    task_type: str,
    settings,
    warnings: list[str],
) -> tuple[str, str, bool]:
    """Return (folder_id, folder_path, used_ai). used_ai False → keyword path."""
    try:
        import folder_tree
        import llm_router

        tree = folder_tree.explore_folder_tree(
            service,
            root_id,
            max_depth=settings.routing_max_depth,
            max_nodes=settings.routing_max_nodes,
        )
        decision = llm_router.route(tree, file.name, account, task_type, settings)
    except AutomationError:
        raise
    except Exception:
        log.exception("AI folder routing failed; falling back to keyword rules")
        return "", "", False

    if not decision:
        return "", "", False

    warnings.append(f"routed by AI (confidence {decision['confidence']:.2f})")
    return decision["folder_id"], decision["path"], True


def _keyword_route_target(
    service, root_id: str, account: str, task_type: str, file: DownloadedFile
) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    business_id, business_name = _find_child(service, root_id, routing.account_keyword(account))
    hint = routing.content_hint(file.name, task_type)
    if hint:
        try:
            target_id, target_name = _find_child(service, business_id, hint)
        except AutomationError as exc:
            if exc.code != "DRIVE_FOLDER_NOT_FOUND":
                raise
            target_id, target_name = business_id, business_name
            warnings.append("no subfolder matched, saved in business folder")
    else:
        target_id, target_name = business_id, business_name
    if target_id == business_id and "no subfolder matched, saved in business folder" not in warnings:
        warnings.append("no subfolder matched, saved in business folder")
    if not routing.matches(file.name, routing.account_keyword(account)):
        warnings.append("file name business differs from selected account; routed by account")
    folder_path = (
        f"{business_name}/{target_name}" if target_name else business_name
    )
    return target_id, folder_path, warnings


_DRIVE_FILE_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]{10,})|[?&]id=([A-Za-z0-9_-]{10,})")


def extract_drive_file_id(url: str) -> str:
    """Pull the file ID out of a Drive URL (view/open/link forms)."""
    match = _DRIVE_FILE_ID_RE.search(url or "")
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""


def replace_file(
    token: str,
    account: str,
    task_type: str,
    file: DownloadedFile,
    drive_url: str,
    root_folder_id: str = "",
) -> UploadResult:
    """Replace an existing Drive file's contents with a fresh download.

    The file ID (and therefore the Sheet link/chip and all sharing) stays the
    same. Falls back to a normal upload when the target is gone from Drive, is
    a folder download, or no file ID can be parsed from the URL.
    """
    file_id = extract_drive_file_id(drive_url)
    if file.is_directory:
        log.info("replace target is a folder download; uploading a new copy instead")
        return upload_and_share(token, account, task_type, file, root_folder_id)
    if not file_id:
        log.info("no Drive file ID found in %r; uploading a new copy", (drive_url or "")[:80])
        return upload_and_share(token, account, task_type, file, root_folder_id)

    service = build("drive", "v3", credentials=Credentials(token=token), cache_discovery=False)
    media = MediaFileUpload(
        str(file.path),
        mimetype=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
        resumable=True,
        chunksize=8 * 1024 * 1024,
    )
    try:
        updated = _resumable(
            service.files().update(
                fileId=file_id,
                body={"name": file.name},
                media_body=media,
                fields="id,size,webViewLink",
                supportsAllDrives=True,
            )
        )
        _verify_soft(service, file_id, file.path, int(updated.get("size") or 0))
    except AutomationError as exc:
        if exc.code == "DRIVE_FOLDER_NOT_FOUND":
            log.warning("Drive file %s is gone; uploading a replacement copy", file_id)
            return upload_and_share(token, account, task_type, file, root_folder_id)
        raise
    log.info("replaced Drive file in place: %s (id %s)", file.name, file_id)
    return UploadResult(
        file_id,
        _link(service, file_id),
        f"replaced:{file.name}",
        int(updated.get("size") or file.path.stat().st_size),
        ["replaced existing Drive file in place"],
    )


def _verify_soft(service, file_id: str, path: Path, size: int) -> None:
    """Verify a replaced file WITHOUT deleting it on mismatch.

    Unlike ``_verify`` (used for fresh uploads), a failure here must never
    remove the user's file — the previous content stays recoverable via Drive
    version history.
    """
    remote = _execute(
        lambda: service.files().get(
            fileId=file_id, fields="id,size,md5Checksum", supportsAllDrives=True
        )
    )
    if int(remote.get("size", -1)) != size or remote.get("md5Checksum") != _md5(path):
        raise AutomationError(
            "DRIVE_VERIFY_FAILED",
            f"Drive verification failed for {path.name}; the previous file version is kept.",
        )


def _find_child(service, parent_id: str, keyword: str) -> tuple[str, str]:
    key = (parent_id, keyword)
    if key in _folder_cache:
        return _folder_cache[key]
    if not keyword:
        return parent_id, ""
    response = _execute(lambda: service.files().list(
        q=f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
        spaces="drive", fields="files(id,name)", pageSize=1000,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ))
    for item in response.get("files", []):
        if routing.matches(item["name"], keyword):
            result = (item["id"], item["name"])
            _folder_cache[key] = result
            return result
    raise AutomationError("DRIVE_FOLDER_NOT_FOUND", f"No Drive folder matched {keyword!r}.")


def _upload_one(service, path: Path, parent_id: str) -> tuple[str, int]:
    name = _unique_name(service, parent_id, path.name)
    media = MediaFileUpload(str(path), mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream", resumable=True, chunksize=8 * 1024 * 1024)
    request = service.files().create(body={"name": name, "parents": [parent_id]}, media_body=media, fields="id,size,md5Checksum", supportsAllDrives=True)
    response = _resumable(request)
    return response["id"], path.stat().st_size


def _resumable(request):
    while True:
        try:
            result = request.next_chunk()
            if result[1] is not None:
                return result[1]
        except Exception as exc:
            _raise_drive_error(exc)


def _unique_name(service, parent_id: str, name: str) -> str:
    escaped = name.replace("'", "\\'")
    response = _execute(lambda: service.files().list(
        q=f"'{parent_id}' in parents and name='{escaped}' and trashed=false",
        spaces="drive", fields="files(id,name)", pageSize=100,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ))
    if not response.get("files"):
        return name
    path = Path(name)
    for number in range(2, 10000):
        candidate = f"{path.stem} ({number}){path.suffix}"
        if not _execute(lambda: service.files().list(
            q=f"'{parent_id}' in parents and name='{candidate.replace(chr(39), chr(92)+chr(39))}' and trashed=false",
            spaces="drive", fields="files(id)", pageSize=1,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        )).get("files"):
            return candidate
    raise AutomationError("DRIVE_UPLOAD_FAILED", f"Could not find an unused name for {name}.")


def _create_folder(service, parent_id: str, name: str) -> str:
    response = _execute(lambda: service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id", supportsAllDrives=True,
    ))
    return response["id"]


def _verify(service, file_id: str, path: Path, size: int) -> None:
    remote = _execute(lambda: service.files().get(fileId=file_id, fields="id,size,md5Checksum,webViewLink", supportsAllDrives=True))
    if int(remote.get("size", -1)) != size or remote.get("md5Checksum") != _md5(path):
        try:
            _execute(lambda: service.files().delete(fileId=file_id, supportsAllDrives=True))
        except AutomationError:
            log.exception("could not remove failed Drive verification copy")
        raise AutomationError("DRIVE_VERIFY_FAILED", f"Drive verification failed for {path.name}.")


def _share(service, file_id: str) -> None:
    _execute(lambda: service.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}, supportsAllDrives=True))


def _link(service, file_id: str) -> str:
    item = _execute(lambda: service.files().get(fileId=file_id, fields="webViewLink", supportsAllDrives=True))
    return item.get("webViewLink") or f"https://drive.google.com/open?id={file_id}"


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _execute(factory):
    for attempt in range(4):
        try:
            return factory().execute()
        except (RefreshError, HttpError) as exc:
            status = _status(exc)
            if status == 401:
                raise AutomationError("TOKEN_EXPIRED", "Google access token expired. Sign in again.") from exc
            if status == 403:
                raise AutomationError("DRIVE_PERMISSION_DENIED", "The Google account has no Drive permission.") from exc
            if status == 404:
                raise AutomationError("DRIVE_FOLDER_NOT_FOUND", "The configured Drive folder was not found.") from exc
            if status not in _RETRYABLE or attempt == 3:
                raise AutomationError("DRIVE_UPLOAD_FAILED", "Google Drive request failed.") from exc
            time.sleep(2**attempt + random.uniform(0, 0.25))


def _raise_drive_error(exc: Exception) -> None:
    if isinstance(exc, (RefreshError, HttpError)):
        _execute(lambda: (_ for _ in ()).throw(exc))
    raise AutomationError("DRIVE_UPLOAD_FAILED", "Google Drive resumable upload failed.") from exc


def _status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "resp", None), "status", None) or (401 if isinstance(exc, RefreshError) else None)
