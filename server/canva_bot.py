"""Real Canva download bot (Module 2).

Flow, exactly like a human: open the edit link → click Share → click Download →
wait for the download panel → click the final Download → save the file.

Carousels arrive as a **.zip**; it is extracted and the folder is returned
instead of the zip, so Module 4 uploads a Drive folder of images.

All selectors live in ``canva_selectors.py``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime
from itertools import count
from pathlib import Path

try:  # Linux/macOS; the server is Linux
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

import canva_selectors as selectors
from config import BASE_DIR, get_settings
from models import AutomationError, DownloadedFile, is_valid_canva_link

log = logging.getLogger(__name__)

DEBUG_DIR = BASE_DIR / "debug"
_CHROME_CHANNEL = (
    os.getenv("CANVA_CHROME_CHANNEL", "chromium").strip() or "chromium"
)
_HEADLESS = os.getenv("CANVA_HEADLESS", "").strip().lower() in {"1", "true", "yes"}
_SKIP_ZIP_PREFIXES = ("__MACOSX/",)
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# --- public API --------------------------------------------------------------


def download_design(canva_link: str, dest_dir: Path) -> DownloadedFile:
    """Download one Canva design into ``dest_dir``.

    Returns a ``DownloadedFile`` pointing at either a single file or an extracted
    folder (``is_directory=True``) for zipped carousels.
    """
    if not is_valid_canva_link(canva_link):
        raise AutomationError(
            "CANVA_LINK_INVALID",
            "The Canva link must be an https link on canva.com, www.canva.com or canva.link.",
        )

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    page: Page | None = None
    extra_downloads: list[str] = []

    try:
        with _browser() as context:
            page = _single_page(context)
            page.set_default_timeout(15_000)
            page.set_default_navigation_timeout(selectors.EDITOR_LOAD_TIMEOUT_MS)

            # If a click ever produces more than one download, keep the first
            # (handled below) and just remember the extras for the log.
            page.on(
                "download",
                lambda download: extra_downloads.append(download.suggested_filename),
            )

            # Catch failures *inside* the context so the debug screenshot and
            # HTML can still be captured before the browser closes.
            try:
                share_button = _open_editor(page, canva_link)
                _open_download_panel(page, share_button)
                download = _confirm_download(page)

                saved = _save_download(download, dest_dir)
                result = _maybe_extract(saved, dest_dir)
            except AutomationError:
                raise
            except PlaywrightTimeoutError as exc:
                _dump_debug(page, "timeout")
                raise AutomationError(
                    "DOWNLOAD_TIMEOUT",
                    "Canva did not finish loading or responding in time. Try again.",
                ) from exc
            except Exception as exc:  # noqa: BLE001 - converted to a reportable error
                _dump_debug(page, "error")
                log.exception("unexpected Canva failure")
                raise AutomationError(
                    "CANVA_BUTTON_NOT_FOUND",
                    f"Could not download the design ({type(exc).__name__}). "
                    "The Canva UI may have changed; check debug/ and canva_selectors.py.",
                ) from exc

            if len(extra_downloads) > 1:
                log.warning(
                    "Canva fired %s download events; kept %s and ignored the rest",
                    len(extra_downloads),
                    result.name,
                )
            log.info(
                "downloaded %s (%s, %s)",
                result.name,
                "folder" if result.is_directory else "file",
                _human_size(result.size),
            )
            return result

    except AutomationError:
        raise
    except PlaywrightError as exc:  # browser launch/profile problems
        log.exception("could not start the Canva browser")
        raise AutomationError(
            "DOWNLOAD_TIMEOUT",
            f"Could not open Chrome ({type(exc).__name__}). Is another Chrome using the profile?",
        ) from exc


CANVA_PROJECTS_URL = "https://www.canva.com/projects"


def inspect_session() -> tuple[bool, list[dict]]:
    """Open Canva once; return (logged_in, decryptable canva.com cookies).

    ``/projects`` is an authenticated page: logged-out visitors are redirected
    to the login page, so it is a much more reliable signal than looking for
    buttons on the marketing home page.  One browser, one tab, no leftovers.
    """
    logged_in = False
    with _browser() as context:
        page = _single_page(context)
        page.set_default_timeout(15_000)
        try:
            page.goto(
                CANVA_PROJECTS_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except PlaywrightError:
            log.warning("could not open the Canva projects page", exc_info=True)

        logged_in = _wait_for_projects_page(page)
        report = _cookie_report(context)
    return logged_in, report


def check_session() -> bool:
    """True only when the persistent profile is logged in to Canva."""
    try:
        logged_in, _ = inspect_session()
        return logged_in
    except AutomationError as exc:
        log.warning("could not check the Canva session: %s", exc.message)
        return False
    except PlaywrightError:
        log.exception("Canva session check failed")
        return False


# --- browser -----------------------------------------------------------------


@contextmanager
def _browser() -> BrowserContext:
    settings = get_settings()
    profile_dir = Path(settings.chrome_profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile_dir, 0o700)  # security rule 5
    except OSError:
        pass

    # A Chrome profile can only be driven by one browser at a time. If the
    # status page (or a second CLI run) opens the same profile mid-download,
    # Chrome forwards to the existing instance and Playwright loses the first
    # browser -> the download is aborted. Serialise access instead.
    lock = _profile_lock(profile_dir)
    try:
        playwright = sync_playwright().start()
    except Exception:
        _release_profile_lock(lock)
        raise
    context: BrowserContext | None = None
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=_CHROME_CHANNEL,
            headless=_HEADLESS,
            accept_downloads=True,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                # Google/Canva show fewer "automated browser" warnings; the
                # one-time login itself is done in normal Chrome (login_canva.py).
                "--disable-blink-features=AutomationControlled",
                # Playwright already adds this on Linux, but make it explicit:
                # login_canva.py passes the same flag so both browsers encrypt
                # cookies with the same (basic) key. Without it, normal Chrome
                # uses the GNOME keyring and Playwright cannot decrypt the
                # login cookies -> Canva appears logged out on every launch.
                "--password-store=basic",
                *(
                    ["--no-sandbox"]
                    if os.getenv("CANVA_CHROMIUM_NO_SANDBOX", "").strip().lower()
                    in {"1", "true", "yes", "on"}
                    else []
                ),
            ],
        )
        yield context
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                log.warning("could not close the browser context cleanly")
        playwright.stop()
        _release_profile_lock(lock)


def _profile_lock(profile_dir: Path, timeout_seconds: float = 8.0):
    """Take an exclusive lock for the Chrome profile, or fail clearly."""
    if fcntl is None:
        return None
    handle = open(profile_dir / ".automation-browser.lock", "a+")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                raise AutomationError(
                    "DOWNLOAD_TIMEOUT",
                    "Another Canva browser is already using this profile. "
                    "Wait for it to finish (or close the extra Chrome window) and retry.",
                )
            time.sleep(0.25)
        except OSError:
            handle.close()
            log.warning("could not lock the Chrome profile; continuing", exc_info=True)
            return None


def _release_profile_lock(handle) -> None:
    if handle is None or fcntl is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _single_page(context: BrowserContext) -> Page:
    """Return the one tab we use and close anything else that was restored."""
    pages = context.pages
    page = pages[0] if pages else context.new_page()
    for extra in pages[1:]:
        try:
            extra.close()
        except PlaywrightError:
            log.warning("could not close an extra browser tab")
    return page


# --- flow steps --------------------------------------------------------------


def _open_editor(page: Page, canva_link: str):
    """Open the link and wait for the editor; return the Share button."""
    page.goto(canva_link, wait_until="domcontentloaded")

    deadline = time.monotonic() + selectors.SHARE_BUTTON_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if _is_login_page(page):
            raise AutomationError(
                "CANVA_SESSION_EXPIRED",
                "Canva is not logged in in the browser profile. Run login_canva.py on the server.",
            )
        share_button = _find_visible(page, selectors.SHARE_BUTTON)
        if share_button is not None:
            return share_button
        page.wait_for_timeout(300)

    raise AutomationError(
        "CANVA_BUTTON_NOT_FOUND",
        "Share button not found — is this an edit link?",
    )


def _open_download_panel(page: Page, share_button) -> None:
    share_button.click()

    option = _wait_for(
        page, selectors.DOWNLOAD_OPTION, selectors.POPUP_TIMEOUT_MS
    )
    if option is None:
        raise AutomationError(
            "CANVA_BUTTON_NOT_FOUND",
            "Download option not found in the Share popup. Check canva_selectors.py.",
        )
    option.click()

    if not _wait_for_download_panel(page):
        raise AutomationError(
            "CANVA_BUTTON_NOT_FOUND",
            "The Download panel did not open. Check canva_selectors.py.",
        )

    # The plan's observation: the final button appears ~3 s after the panel.
    page.wait_for_timeout(int(selectors.MIN_PANEL_WAIT_SECONDS * 1000))


def _confirm_download(page: Page):
    # The final button often renders a few seconds after the panel markers;
    # wait the panel settle time, then keep polling until it shows up.
    confirm = _find_confirm_button(page)
    deadline = time.monotonic() + selectors.PANEL_TIMEOUT_MS / 1000
    while confirm is None and time.monotonic() < deadline:
        _pause(page, 250)
        confirm = _find_confirm_button(page)
    if confirm is None:
        raise AutomationError(
            "CANVA_BUTTON_NOT_FOUND",
            "Final Download button not found in the download panel. Check canva_selectors.py.",
        )

    timeout_seconds = selectors.DOWNLOAD_TIMEOUT_MS / 1000
    log.info(
        "clicking Download; Canva can spend a while preparing a reel, "
        "keeping the window open for up to %.0f minutes",
        timeout_seconds / 60,
    )

    # A Download object can arrive on the editor page or on a popup. Collect
    # from both instead of only listening to the page we clicked on.
    downloads: list = []

    def collect(download) -> None:
        downloads.append(download)

    page.on("download", collect)
    context = page.context

    def watch(other: Page) -> None:
        other.on("download", collect)

    for other in context.pages:
        if other is not page:
            watch(other)
    context.on("page", watch)

    confirm.click()

    started = time.monotonic()
    deadline = started + timeout_seconds
    last_note = 0.0
    while not downloads:
        if page.is_closed():
            raise AutomationError(
                "DOWNLOAD_TIMEOUT",
                "The Canva tab closed before the download started. Keep the "
                "Chrome window open while Canva processes the reel, then retry.",
            )
        try:
            page.wait_for_timeout(500)
        except PlaywrightError as exc:
            raise AutomationError(
                "DOWNLOAD_TIMEOUT",
                "The browser closed before Canva started the download. Retry; "
                "reels need the window to stay open until the download begins.",
            ) from exc

        elapsed = time.monotonic() - started
        if time.monotonic() >= deadline:
            _dump_debug(page, "download-timeout")
            raise AutomationError(
                "DOWNLOAD_TIMEOUT",
                f"Canva did not start the download within {timeout_seconds / 60:.0f} "
                "minutes. The reel may still be processing; retry and leave the "
                "window open longer.",
            )
        if elapsed - last_note >= 15:
            last_note = elapsed
            log.info("Canva is still preparing the download (%.0f s elapsed)", elapsed)

    download = downloads[0]
    log.info(
        "Canva started the download after %.0f s: %s",
        time.monotonic() - started,
        download.suggested_filename,
    )
    return download


# --- locator helpers ---------------------------------------------------------


def _spec_from_css(css_list: list[str]) -> dict:
    return {"css": css_list}


def _locators(scope, spec: dict):
    role = spec.get("role")
    name = spec.get("name")
    if role and name is not None:
        yield scope.get_by_role(role, name=name)
    elif role:
        yield scope.get_by_role(role)
    for css in spec.get("css", []):
        yield scope.locator(css)


def _find_visible(scope, spec: dict):
    """First visible element matching ``spec`` (no waiting)."""
    for locator in _locators(scope, spec):
        try:
            total = locator.count()
        except PlaywrightError:
            continue
        for index in range(min(total, 8)):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except PlaywrightError:
                continue
    return None


def _pause(scope, milliseconds: int) -> None:
    """Wait on a Page or a Locator (Locator has .page, Page does not)."""
    if isinstance(scope, Page):
        scope.wait_for_timeout(milliseconds)
    else:
        scope.page.wait_for_timeout(milliseconds)


def _wait_for(scope, spec: dict, timeout_ms: int):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        element = _find_visible(scope, spec)
        if element is not None:
            return element
        _pause(scope, 250)
    return None


def _is_login_page(page: Page) -> bool:
    url = page.url.lower()
    if any(marker in url for marker in selectors.LOGIN_URL_MARKERS):
        return True
    return _find_visible(page, _spec_from_css(selectors.LOGIN_INDICATORS)) is not None


def _wait_for_projects_page(page: Page, timeout_seconds: float = 15.0) -> bool:
    """True when Canva's authenticated projects page finished loading."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_login_page(page):
            return False
        try:
            title = page.title().strip().lower()
        except PlaywrightError:
            return False
        # "Projects - Canva" (en) / any localized "Projects" title.
        if title.startswith(tuple(selectors.LOGGED_IN_PROJECT_TITLE_PREFIXES)):
            return True
        page.wait_for_timeout(400)
    return False


def _cookie_report(context: BrowserContext) -> list[dict]:
    """Cookie names/expiry the profile can decrypt (values never returned)."""
    cookies = context.cookies(
        ["https://www.canva.com", "https://www.canva.com/login"]
    )
    report = [
        {
            "name": cookie.get("name", ""),
            "domain": cookie.get("domain", ""),
            "expires": cookie.get("expires", -1),
            "httpOnly": cookie.get("httpOnly", False),
            "secure": cookie.get("secure", False),
        }
        for cookie in cookies
    ]
    return sorted(report, key=lambda item: (item["domain"], item["name"]))


def _wait_for_download_panel(page: Page) -> bool:
    """Panel is ready when a panel marker shows up or a second Download appears."""
    deadline = time.monotonic() + selectors.PANEL_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        if _find_visible(page, _spec_from_css(selectors.DOWNLOAD_PANEL_MARKERS)) is not None:
            return True
        if len(_visible_confirm_buttons(page)) >= 2:
            return True
        page.wait_for_timeout(250)
    return False


def _visible_confirm_buttons(page: Page) -> list:
    buttons = []
    for locator in _locators(page, selectors.CONFIRM_DOWNLOAD):
        try:
            total = locator.count()
        except PlaywrightError:
            continue
        for index in range(total):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible() and candidate.is_enabled():
                    buttons.append(candidate)
            except PlaywrightError:
                continue
    return buttons


def _find_confirm_button(page: Page):
    buttons = _visible_confirm_buttons(page)
    if not buttons:
        return None
    # The panel's button is the newest/last one; the entry in the Share popup is first.
    return buttons[-1]


# --- saving / zip ------------------------------------------------------------


def _save_download(download, dest_dir: Path) -> Path:
    suggested = download.suggested_filename or "canva-download"
    name = _safe_filename(suggested)
    target = _unique_path(dest_dir / name)
    started = time.monotonic()
    try:
        download.save_as(target)
        failure = download.failure()
    except PlaywrightError as exc:
        target.unlink(missing_ok=True)
        log.exception("the Canva download was interrupted")
        raise AutomationError(
            "DOWNLOAD_TIMEOUT",
            "The browser closed before the download finished. Retry; if it keeps "
            "happening, check free RAM/disk on this machine.",
        ) from exc

    if failure:
        target.unlink(missing_ok=True)
        raise AutomationError(
            "DOWNLOAD_TIMEOUT",
            f"Canva interrupted the download ({failure}). Try again.",
        )

    log.info(
        "finished saving %s (%s) in %.1f s",
        target.name,
        _human_size(target.stat().st_size),
        time.monotonic() - started,
    )
    return target


def _maybe_extract(downloaded: Path, dest_dir: Path) -> DownloadedFile:
    if downloaded.suffix.lower() != ".zip":
        return DownloadedFile(
            path=downloaded,
            name=downloaded.name,
            size=downloaded.stat().st_size,
        )

    folder = _extract_zip(downloaded, dest_dir)
    if folder is None:
        log.warning("could not extract %s; keeping the zip", downloaded)
        return DownloadedFile(
            path=downloaded,
            name=downloaded.name,
            size=downloaded.stat().st_size,
        )

    downloaded.unlink(missing_ok=True)
    return DownloadedFile(
        path=folder,
        name=folder.name,
        size=_directory_size(folder),
        is_directory=True,
    )


def _extract_zip(zip_path: Path, dest_dir: Path) -> Path | None:
    """Extract safely (no zip-slip); return the folder, or None if unusable."""
    folder = _unique_path(dest_dir / _safe_filename(zip_path.stem))
    try:
        folder.mkdir(parents=True)
        root = folder.resolve()
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                name = info.filename
                if not name or name.endswith("/"):
                    continue
                if name.startswith(_SKIP_ZIP_PREFIXES) or Path(name).name.startswith("."):
                    continue
                target = (folder / name).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(f"unsafe path inside zip: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        log.warning("zip extraction failed for %s: %s", zip_path.name, exc)
        shutil.rmtree(folder, ignore_errors=True)
        return None

    if not any(path.is_file() for path in folder.rglob("*")):
        shutil.rmtree(folder, ignore_errors=True)
        return None
    return folder


def _safe_filename(name: str) -> str:
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("-", name).strip().strip(".")
    return cleaned[:200] or "canva-download"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for number in count(2):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("unreachable")


def _directory_size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# --- debug -------------------------------------------------------------------


def _dump_debug(page: Page | None, label: str) -> None:
    if page is None:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    screenshot = DEBUG_DIR / f"{stamp}-{label}.png"
    markup = DEBUG_DIR / f"{stamp}-{label}.html"
    try:
        page.screenshot(path=str(screenshot), full_page=True)
        markup.write_text(page.content(), encoding="utf-8")
        log.warning("saved debug artifacts: %s, %s", screenshot, markup)
    except PlaywrightError:
        log.warning("could not save debug artifacts")
