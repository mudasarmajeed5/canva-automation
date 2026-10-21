"""One-time manual Canva login.

Why this does not use Playwright: Google refuses "Sign in with Google" inside
automation-controlled browsers ("This browser or app may not be secure").
This script launches the **normal** Chrome binary with the same profile
directory, so Google sees a regular browser.  Playwright reuses those cookies
afterwards.

    python login_canva.py              # normal Chrome (recommended)
    python login_canva.py --playwright # fallback if Chrome cannot be launched

Close every Chrome window first, and stop the automation service if it is
running (Chrome cannot share a profile directory with a live process).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from config import get_settings

CANVA_LOGIN_URL = "https://www.canva.com/login"
_CANDIDATE_BINARIES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def _chrome_binary() -> str | None:
    configured = os.getenv("CHROME_BIN", "").strip()
    if configured:
        return configured
    for name in _CANDIDATE_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _profile_dir() -> Path:
    profile = Path(get_settings().chrome_profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(profile, 0o700)
    except OSError:
        pass
    return profile


def _login_with_real_chrome(profile: Path) -> int:
    chrome = _chrome_binary()
    if not chrome:
        print(
            "Google Chrome was not found. Install it or set CHROME_BIN=/path/to/chrome.",
            file=sys.stderr,
        )
        return 1

    lock = profile / "SingletonLock"
    if lock.exists():
        print(
            f"Warning: {lock} exists — Chrome may still be running with this profile "
            "or a previous run crashed. Close all Chrome windows, then try again.",
            file=sys.stderr,
        )

    print(f"Launching real Chrome with profile: {profile}", flush=True)
    print("Log in to Canva by hand (Google, email, 2FA — anything works here).", flush=True)
    print("When you can see your Canva home, close the Chrome window to continue.", flush=True)

    process = subprocess.Popen(  # noqa: S603 - fixed binary, user-owned profile
        [
            chrome,
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            # Playwright always launches Chrome with --password-store=basic on
            # Linux. Normal Chrome otherwise uses the GNOME keyring, producing
            # cookies Playwright cannot decrypt (logged out on every launch).
            "--password-store=basic",
            "--new-window",
            CANVA_LOGIN_URL,
        ]
    )
    try:
        returncode = process.wait()
    except KeyboardInterrupt:
        process.terminate()
        print("\nCancelled; not checking the session.", file=sys.stderr)
        return 130

    if returncode not in (0,):
        print(f"Chrome exited with code {returncode}.", file=sys.stderr)

    # Give Chrome a moment to flush its profile to disk.
    time.sleep(2)
    return 0


def _login_with_playwright() -> int:
    from playwright.sync_api import Error as PlaywrightError

    from canva_bot import (
        CANVA_PROJECTS_URL,
        _browser,
        _is_login_page,
        _single_page,
        _wait_for_projects_page,
    )

    try:
        with _browser() as context:
            page = _single_page(context)
            page.goto(CANVA_LOGIN_URL, wait_until="domcontentloaded")
            print("A Chrome window opened on canva.com.")
            print("Log in by hand (email, password, 2FA/captcha).")
            input("When you are fully logged in, press Enter here... ")

            if _is_login_page(page):
                print("Still on the login page — session NOT saved.", file=sys.stderr)
                return 1

            page.goto(CANVA_PROJECTS_URL, wait_until="domcontentloaded")
            if not _wait_for_projects_page(page):
                print(
                    "Could not open Canva's projects page — session NOT confirmed. "
                    "If you are logged in, the selectors may need updating "
                    "(see canva_selectors.py).",
                    file=sys.stderr,
                )
                return 1
            print("session saved")
            return 0
    except PlaywrightError as exc:
        print(f"Could not open Chrome: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time manual Canva login.")
    parser.add_argument(
        "--playwright",
        action="store_true",
        help="use Playwright's browser instead of launching Chrome directly "
        "(Google sign-in will likely be blocked; use email/password)",
    )
    args = parser.parse_args()

    if args.playwright:
        return _login_with_playwright()

    profile = _profile_dir()
    if _login_with_real_chrome(profile) != 0:
        return 1

    from canva_bot import check_session

    if check_session():
        print("session saved", flush=True)
        return 0

    print(
        "Not logged in. If you closed Chrome too early, run this again. "
        "If Google sign-in was blocked even in normal Chrome, use Canva's "
        "email + password login instead (set a password with 'Forgot password').",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
