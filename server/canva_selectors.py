"""Every Canva selector and UI string lives here — the only file to edit when
Canva changes its UI.

How the lists work: each spec is tried in order (role locators first, then CSS),
and the first *visible* match wins.  To debug a broken step, run:

    playwright codegen https://www.canva.com/design/<id>/edit

click Share → Download → Download, and paste the recorded selectors below.
"""

from __future__ import annotations

import os
import re

# --- Share button (top right of the editor) ---------------------------------

SHARE_BUTTON = {
    "role": "button",
    "name": re.compile(r"^\s*share\s*$", re.IGNORECASE),
    "css": [
        'button[data-testid="share-button"]',
        'button[aria-label="Share"]',
        'button:has-text("Share")',
    ],
}

# --- "Download" entry inside the Share popup ---------------------------------

DOWNLOAD_OPTION = {
    "role": "button",
    "name": re.compile(r"^\s*download\s*$", re.IGNORECASE),
    "css": [
        '[data-testid="download-button"]',
        'button:has-text("Download")',
        '[role="menuitem"]:has-text("Download")',
    ],
}

# --- Markers that the download panel (file type / page selectors) is open ----

DOWNLOAD_PANEL_MARKERS = [
    'text="File type"',
    'text="Page"',
    '[data-testid="download-options"]',
    '[data-testid="download-panel"]',
    'select',
]

# --- Final "Download" button that actually starts the download ---------------

CONFIRM_DOWNLOAD = {
    "role": "button",
    "name": re.compile(r"^\s*download\s*$", re.IGNORECASE),
    "css": [
        '[data-testid="download-panel-download-button"]',
        'button:has-text("Download")',
    ],
}

# --- Login / logged-in detection ---------------------------------------------

LOGIN_INDICATORS = [
    'a[href*="/login"]',
    'a:has-text("Log in")',
    'button:has-text("Log in")',
    '[data-testid="login-button"]',
]

LOGGED_IN_PROJECT_TITLE_PREFIXES = ("projects", "home - canva")

LOGIN_URL_MARKERS = ("/login", "/signup", "auth.canva.com")

# --- Timing (milliseconds unless noted) --------------------------------------

EDITOR_LOAD_TIMEOUT_MS = 60_000
SHARE_BUTTON_TIMEOUT_MS = 45_000
POPUP_TIMEOUT_MS = 30_000
PANEL_TIMEOUT_MS = 30_000
MIN_PANEL_WAIT_SECONDS = 6.0  # human observation: ~3 s before the final button; doubled for slow loads
DOWNLOAD_TIMEOUT_MS = int(
    os.getenv("CANVA_DOWNLOAD_TIMEOUT_MS", str(10 * 60 * 1000))
)  # reels: Canva can spend minutes "preparing" before the download starts
