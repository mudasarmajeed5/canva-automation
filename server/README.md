# Canva Automation — Python server (Module 1 of 5)

FastAPI service that receives `CANVA_AUTOMATION` jobs from the Vercel app, writes
the rows to Google Sheets, then downloads each Canva design, uploads it to the
right Google Drive folder, and replaces the Sheet's Post Link cell with the Drive
link.

**Current state:** Modules 1 (server core), 2 (Canva bot), 3 (Google Sheets),
and 4 (Google Drive/cleanup) are implemented.

## Requirements

- Python 3.12+ (the local venv lives one level up in `../canva`)
- `xvfb` on the server (only needed once the real Playwright bot lands)

## Setup (local)

```bash
cd server
# option A: use the existing venv at ../canva (Python 3.14 here)
source ../canva/bin/activate
pip install -r requirements.txt
# option B: fresh venv
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
# edit .env: AUTOMATION_SECRET is mandatory, SHEET_ID/ROOT_FOLDER_ID for later modules
```

Run (from `server/`, with the venv active):

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

- Status page: <http://127.0.0.1:8000/>
- Health: <http://127.0.0.1:8000/health>

## API

All `/api/*` calls need `X-Automation-Secret`.

```bash
export SECRET='the value from your .env'

# health (no secret)
curl -s http://127.0.0.1:8000/health

# queue a job
curl -s -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-Automation-Secret: $SECRET" \
  -d '{
    "request_type": "CANVA_AUTOMATION",
    "date": "21-Oct-2026",
    "rows": [
      {
        "row_id": "r1",
        "account": "Excel Executive Cars",
        "task_type": "Graphic Design - Static Post/Carousel",
        "task_description": "Static post for 22-Oct-2026",
        "status": "Done",
        "canva_link": "https://www.canva.com/design/DAFexample/edit"
      }
    ],
    "google_access_token": "stub-token-for-local-testing",
    "requested_by": "you@example.com"
  }'

# poll
curl -s http://127.0.0.1:8000/api/jobs/<job_id> -H "X-Automation-Secret: $SECRET"
```

Job states: `queued | running | done | done_with_errors | failed`.
Row states: `pending | downloading | uploading | updating_sheet | done | failed`.

## What is real vs. stubbed

| File | Module 1 behaviour |
|---|---|
| `main.py` | Real: secret check (constant time), validation, rate limit 10/min, 202/400/401/429/502, status page |
| `jobs.py` | Real: SQLite store; the Google token lives **only in memory** |
| `worker.py` | Real: one job at a time, 1–3 s pause between rows, daily maintenance |
| `pipeline.py` | Real: row state machine, per-row error isolation, `TOKEN_EXPIRED` handling |
| `canva_bot.py` | Real: Playwright + bundled Chromium, Share → Download → Download; zips are extracted to a folder |
| `sheets.py` | Real: header-driven Google Sheets v4 append/update with retries and token-safe errors |
| `drive.py` | Real: dynamic folder routing, resumable upload, collision handling, verification, and sharing |
| `cleanup.py` | Real: deletes only verified uploads inside `DOWNLOAD_DIR`; preserves failures and sweeps old leftovers |

Drive stub test hook: start the server with `STUB_FAIL_ON_CALL=2` to make the second
Drive upload fail (used to verify `done_with_errors`). Leave it unset normally.

## Acceptance tests (Module 1)

1. `curl .../health` → `{"ok":true}`; open `/` → green “Automation online”.
2. POST with a wrong secret → `401`. Invalid account or non-Canva link → `400`
   with a `details` list. Missing `google_access_token` → `400`.
3. POST 3 valid rows → `202`; polling shows rows moving
   `pending → downloading → uploading → updating_sheet → done`, one at a time;
   final state `done`. With the Drive stub, files appear in
   `downloads/<date>/<job_id>/` and are deleted at the end of the job.
4. Restart the server with `STUB_FAIL_ON_CALL=2`, POST 3 rows → row 2 `failed`
   with `DRIVE_UPLOAD_FAILED`, rows 1 and 3 `done`, job `done_with_errors`, and
   row 2's file is kept on disk.
5. Restart the service mid-job → that job becomes `failed` with
   “server restarted”. `grep -r "ya29" data/` and the logs contain nothing.

## Module 2 — the Canva bot

Requires the Playwright driver and its bundled Chromium:

```bash
source ../canva/bin/activate
pip install -r requirements.txt
playwright install chromium
sudo playwright install-deps     # first time on the Ubuntu server (apt packages)
```

The bot uses Playwright's **bundled Chromium** by default. System Google Chrome
154 segfaults during downloads on this machine (see troubleshooting below).

One-time manual login (normal Chrome, **not** an automated browser):

```bash
python login_canva.py            # log in by hand, then CLOSE the Chrome window
python check_session.py          # -> "logged in"
```

`login_canva.py` launches the real Chrome binary with the same profile directory,
waits until you close the window, then verifies the session with Playwright. The
bundled Chromium reads that same profile (cookies use `--password-store=basic`).
Use `--playwright` only as a fallback (Google will likely block sign-in there).

On the Ubuntu server, stop the service first (Chrome cannot share the profile with
a running job) and run it under a display: `sudo systemctl stop canva-automation`,
then VNC or `ssh -X` + `python login_canva.py`.

### “This browser or app may not be secure” (Google sign-in blocked)

Google blocks OAuth inside automation-controlled browsers, which is exactly what
Playwright is. Two ways around it:

1. **Use `login_canva.py` as-is** — it starts normal Chrome, so Google treats it
   like any other browser. Log in, close the window, and Playwright reuses the
   cookies. (`--playwright` re-introduces the block.)
2. **Use Canva email + password instead of “Continue with Google”** — if the
   account was created with Google, set a password first via Canva's
   “Forgot password” flow, then log in with that email/password.

Do not try to sign in to Google from `test_download.py`/the bot itself; the bot
only opens Canva links, and the session comes from the profile you logged into.

### “It logs me out every time the bot opens the browser” (Linux)

Playwright **always** launches Chrome with `--password-store=basic` on Linux
(see its default arguments). Normal Chrome uses the GNOME keyring / KWallet
instead, so the two write cookies with different encryption keys: each browser
sees the other's cookies as unreadable and Canva looks logged out. This is a
known Linux-only Playwright/Chromium issue.

`login_canva.py` and `canva_bot.py` now both pass `--password-store=basic`. To apply it:

1. Stop the server (if running) and close every Chrome window.
2. Delete the old profile — it contains keyring-encrypted cookies from before
   the fix: `rm -rf server/chrome-profile`.
3. Run `python login_canva.py` again and log in.

Diagnose at any time with:

```bash
python check_session.py --cookies
```

It opens exactly one browser tab, checks Canva's authenticated `/projects`
page, and prints the number of `canva.com` cookies the bot can decrypt (names
and expiry, never values). `0` means the login did not persist; >0 with
`logged in` means the profile is good.

Each bot run uses a single tab and does not restore previous tabs. Do **not**
add `--restore-last-session` back: Canva's auth cookies are persistent, and the
flag makes every launch reopen the last session's tabs, so they pile up.

Cloudflare also blocks the browser in **headless** mode (“Verify you are human”),
which is why `CANVA_HEADLESS=1` must only be used for non-Canva debugging. The
bot runs headed; on a server with no desktop that means Xvfb (see below).

### “The browser closed before the download finished” (Chrome 154 crash)

This is **not** Canva taking too long, and not the download timeout. System
Chrome 154.0.8037.97 segfaults (`SIGSEGV`) in its main process a few seconds
after a download starts. `coredumpctl list` shows the crash:

```
Wed 2026-10-21 18:33:03 ... SIGSEGV ... /opt/google/chrome/chrome
```

The bot now uses Playwright's **bundled Chromium** by default, which downloads
the same static posts and reels reliably. Only use system Chrome to reproduce:

```bash
CANVA_CHROME_CHANNEL=chrome python test_download.py "<link>"
```

The download wait is also 10 minutes now (`CANVA_DOWNLOAD_TIMEOUT_MS`) and logs
every 15 s while Canva prepares the file, so a slow reel is never cut off early.

### Running on a headless server (optional Xvfb)

On a server with no display, install Xvfb once (Fedora):

```bash
sudo dnf install -y xorg-x11-server-Xvfb
```

and run the server/test on a virtual display:

```bash
env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE \
  xvfb-run -a -s "-screen 0 1440x900x24" \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

For one link:

```bash
env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE \
  xvfb-run -a -s "-screen 0 1440x900x24" python test_download.py "<edit link>"
```

(`WAYLAND_DISPLAY` must be removed or Chromium tries Wayland and ignores Xvfb.)
Keep `login_canva.py` on your normal desktop — it needs a real window for the
one-time login — and it writes the same profile Xvfb later reads.

Only one browser may use the profile at a time. The bot takes a file lock now;
a second run (or the status page) waits up to 8 s and then fails with
`DOWNLOAD_TIMEOUT` instead of killing the active download.

Test one link:

```bash
python test_download.py "https://www.canva.com/design/DAF.../edit"
```

**Zip carousels:** when Canva returns a `.zip`, the bot extracts it into
`<downloads>/<date>/<job>/<design-name>/` (safe extraction, no zip-slip, skips
`__MACOSX`), deletes the zip, and returns `DownloadedFile(is_directory=True)`.
Module 4 therefore uploads one Drive **folder** per carousel instead of a zip
file. If extraction fails, the zip is kept and uploaded as a single file.

Useful overrides: `CANVA_HEADLESS=1` runs the browser headless,
`CANVA_CHROME_CHANNEL=chrome` uses system Chrome instead of bundled Chromium
(system Chrome 154 crashes during downloads), and `CANVA_DOWNLOAD_TIMEOUT_MS`
changes the 10-minute download wait.

Selectors are all in `canva_selectors.py` (renamed from the plan's `selectors.py`,
which shadowed Python's stdlib `selectors` module). If Canva changes its UI, run
`playwright codegen <edit link>`, click Share → Download → Download, and update
that file — no other file needs to change.

## Acceptance tests (Module 2)

1. `login_canva.py` → log in by hand → `check_session.py` prints “logged in”.
2. `test_download.py <real edit link>` → file appears in `./test_downloads/`;
   name/size printed; no Chrome process left (`pgrep chrome`).
3. Carousel link → `.zip` is downloaded and **extracted**; the script reports a
   folder and its member files. Reel link → `.mp4`.
4. Garbage link → `CANVA_LINK_INVALID`. Logged-out profile → `CANVA_SESSION_EXPIRED`.
   Broken selector → screenshot + HTML in `debug/` and `CANVA_BUTTON_NOT_FOUND`.
5. Same link three times → files/folders get ` (2)`, ` (3)` suffixes.

## Deploy (Ubuntu 24.04)

```bash
sudo useradd --system --create-home canva-auto
sudo mkdir -p /opt/canva-automation && sudo chown canva-auto: /opt/canva-automation
# copy this directory there, then:
sudo -u canva-auto python3.12 -m venv /opt/canva-automation/.venv
sudo -u canva-auto /opt/canva-automation/.venv/bin/pip install -r requirements.txt
sudo -u canva-auto cp .env.example .env && sudo chmod 600 .env

sudo cp deploy/canva-automation.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now canva-automation

sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

Logs: `journalctl -u canva-automation -f`. Stop: `sudo systemctl stop canva-automation`.

## Notes / things to confirm

- The venv on this machine is Python **3.14**, while the plan says 3.12. Everything
  (including Playwright 1.64) runs on 3.14 here.
- `AUTOMATION_SECRET` is required at startup, so copy `.env.example` first.
- Flat imports (`from models import ...`): always start uvicorn from this directory
  (`main:app`), not from the repository root.

### Deviations from the module plans

1. **`selectors.py` → `canva_selectors.py`.** A top-level `selectors.py` shadows
   Python's stdlib `selectors` module, which `asyncio`/`subprocess` import — the
   server would not start. The content and purpose are unchanged.
2. **Zip carousels are extracted and uploaded as a folder** (requested change):
   `download_design` returns `DownloadedFile(is_directory=True)` and Module 4 must
   create one Drive folder and upload its members. If extraction fails, the zip
   is kept and uploaded as a single file.
3. `DownloadedFile` gained `is_directory: bool = False` (default keeps the old
   interface); the drive stub already returns a folder link for it.

## Strict Drive routing and read-only replay

Uploads now resolve existing folders deterministically by business, publication
month and content type. Business conflicts and missing folders fail before
upload. Duplicate matches use the newest modification time across the folder
and its non-trashed descendants; ties or unavailable activity stop delivery. Paths
come from Drive metadata. AI routing environment variables are retained for
legacy tools but do not select upload destinations.

See [ROUTING_RESULTS.md](ROUTING_RESULTS.md) for rules, actual-folder replay
results, limitations and the no-upload test command. To check a saved job
against current Drive metadata without uploading:

```bash
python routing_dry_run.py JOB_ID --output /tmp/routing-report.json
```

The command prompts for a hidden Google access token and only exposes Drive
metadata `get` and `list` operations. It does not run Canva, update Sheets, send
email or change Drive contents. Destination aliases live in `folder_resolver.py`.
