# MODULE 2 of 5 — Canva download bot (Playwright)

**Goal:** replace the `canva_bot.py` stub with real code that opens a Canva design link in a logged-in Chrome and downloads the file, exactly like a human: **open link → click Share → popup appears → click Download → wait ~3 seconds → a second Download button appears → click it → file downloads.**

## Files to create/replace
`canva_bot.py` · `selectors.py` (every selector/text in ONE place) · `login_canva.py` (one-time manual login helper) · `check_session.py` (CLI that prints logged in / not logged in) · `test_download.py` (CLI: `python test_download.py <canva_link>`).

## Interfaces to honor (Section 5)
```python
def download_design(canva_link: str, dest_dir: Path) -> DownloadedFile
def check_session() -> bool
```
Raise `AutomationError` with these codes: `CANVA_SESSION_EXPIRED`, `CANVA_BUTTON_NOT_FOUND`, `CANVA_LINK_INVALID`, `DOWNLOAD_TIMEOUT`.

## Requirements
**Browser setup**
- Playwright **Python**, `launch_persistent_context(user_data_dir=CHROME_PROFILE_DIR, channel="chrome", headless=False, accept_downloads=True, viewport=1440x900)`. The server runs it under Xvfb (module 1's systemd unit), so headed mode works without a screen.
- Install with `playwright install chrome` (real Google Chrome) and `playwright install-deps`.
- Open the context once per job or per call and **always close it in `finally`**. Never leave Chrome processes running.

**Per-link flow in `download_design`**
1. Validate the link (Section 7 rule 4) → else `CANVA_LINK_INVALID`.
2. `page.goto(link)`; wait until the editor is loaded by waiting for the **Share** button to be visible (do not use a fixed sleep). If the page shows a login screen instead → `CANVA_SESSION_EXPIRED`.
3. Click **Share**. Wait for the popup.
4. Click **Download** inside the popup.
5. Wait until the second **Download** button is visible and enabled (the human observation is ~3 seconds; use that as a *minimum* pause plus visibility check).
6. Click it **inside** `with page.expect_download(timeout=180_000) as dl:` (videos can take minutes).
7. `download.save_as(dest_dir / safe_name)` where `safe_name` is `download.suggested_filename` with path separators removed. If the name already exists in `dest_dir`, append ` (2)`, ` (3)`…
8. Return `DownloadedFile(path, name=original suggested filename, size=file size)`.
9. If more than one download event fires for the one click, keep the first and put a warning in the log (do not crash).
- Multi-page designs and videos may arrive as `.zip` / `.mp4`: **save as given, do not unzip.**

**Failure handling**
- On any exception: save `debug/<timestamp>.png` (screenshot) and `debug/<timestamp>.html`, then raise `AutomationError` with the right code. (Debug files must not contain tokens; Canva pages don't.)
- `check_session()`: open `https://www.canva.com/` in the profile and return True only if the logged-in home (not the login page) is detected.

**Selectors:** use role/text locators first (`get_by_role("button", name="Share")`, `get_by_role("button", name="Download")`), with fallbacks, all defined in `selectors.py` with comments. Canva's UI changes often, so this file must be the only place that needs edits.

**`login_canva.py`:** opens Canva in the **same persistent profile** headed and waits until the user finishes logging in manually (they handle 2FA/captcha), then prints "session saved". Document in README how to run it on the server (VNC or `ssh -X`).

**Politeness:** no parallel tabs, no hammering. One design at a time.

**Links:** must be **edit** links from an account that can edit the design. If the Share button never appears, report `CANVA_BUTTON_NOT_FOUND` with message "Share button not found — is this an edit link?".

## Docs to check
Playwright Python: downloads (`expect_download`, `save_as`), `launch_persistent_context`, locators/`get_by_role`, `codegen`. Chrome channel install.

## Acceptance tests (the human runs these)
1. Run `login_canva.py`, log in by hand, close. Run `check_session.py` → "logged in".
2. `python test_download.py <one real Canva edit link>` → a file appears in `./test_downloads/` with the expected name; script prints name and size; no Chrome process left (`pgrep chrome`).
3. A carousel link → a `.zip`; a reel link → an `.mp4`.
4. A garbage link → `CANVA_LINK_INVALID`. A link while logged out (delete profile copy) → `CANVA_SESSION_EXPIRED`. Break a selector on purpose → screenshot appears in `debug/` and error is `CANVA_BUTTON_NOT_FOUND`.
5. Run it 3 times in a row → all succeed, files named `(2)`, `(3)` if the name repeats.

**Tip for the human:** if selectors don't match the real Canva UI, run `playwright codegen https://www.canva.com/design/<your design>/edit`, click Share → Download → Download, and paste the recorded selectors to DeepSeek so it can fix `selectors.py`.

---

# SHARED CONTRACT (identical in all 5 module files — do not change it, other modules depend on it)

## 0. The project in one paragraph
A small team posts designs on social media for 4 car businesses. Their Google Sheet "calendar" has these columns:

**Date | Business/Account | Task Type | Task Description | Status | Post Link**

A web page (Next.js on Vercel) lets the user fill those columns for each design, and paste the **Canva link** into the Post Link column. When they press **Upload**, a Python server (Ubuntu 24.04, `events.mudasarmajeed5.tech`) (1) writes the rows to the Sheet with the **Canva link** in Post Link, (2) downloads each design from Canva with Playwright, (3) uploads each file to the correct Google Drive folder, (4) **replaces the Canva link in the Post Link cell with the Google Drive share link**, and (5) deletes the downloaded files from the server.

## 1. Architecture

```
 Browser ── Google login (Better Auth) ──► Next.js on VERCEL
                                              │ POST /api/jobs  (header X-Automation-Secret)
                                              ▼
                              events.mudasarmajeed5.tech  (Caddy HTTPS → FastAPI, Ubuntu 24.04)
                                1. validate request
                                2. sheets.append_rows()      → rows appear in Sheet, Post Link = Canva link
                                3. return 202 {job_id}       (browser then polls status)
                                4. worker (one job at a time), for each row:
                                     canva_bot.download_design()
                                     drive.upload_and_share()
                                     sheets.set_post_link()  → Post Link = Drive share link
                                5. cleanup.cleanup_job_dir() → delete downloaded files
```

## 2. Fixed values (use exactly these strings)

Sheet header row: `Date`, `Business/Account`, `Task Type`, `Task Description`, `Status`, `Post Link`

Business/Account (UI label → value written to the Sheet):
- Excel Executive Cars → `Excel Executive Cars`
- Platinum Executive Car → `Platinum Exetuive Car`  (**typo is intentional**: it matches the existing sheet)
- Reading Chauffers → `Reading Chauffers`
- First Class Cars → `First Class Cars`

Task Type: `Graphic Design - Static Post/Carousel`, `Video/Reel Editing`, `Branding/Other`

Status: `Done`, `In Progress`, `Completed` (default `Done`)

Date format written to the Sheet: `D-MMM-YYYY` e.g. `21-Oct-2026`.

Drive root folder ID: `1SNb5Nby17k988eSygucMJkvedOUzGlkq` (inside it: one folder per business, inside each: subfolders by content type — real subfolder names are discovered at runtime, never hard-coded).

Google scopes requested at login: `openid email profile`, `https://www.googleapis.com/auth/spreadsheets`, `https://www.googleapis.com/auth/drive` (full Drive is required — `drive.file` cannot see the existing folder).

## 3. Row lifecycle in the Sheet
1. User submits → Python appends one row per design: Date, Business/Account, Task Type, Task Description, Status, **Post Link = Canva link**.
2. After that row's file is uploaded and verified on Drive → Python overwrites **only the Post Link cell** with the Drive share link.
3. If anything fails for a row, its Post Link **stays as the Canva link** (nothing is lost) and the error is reported in the job status.
4. Python never changes Date, Business/Account, Task Type, Task Description or Status after the first write.

## 4. API contract (Vercel → Ubuntu)

`POST https://events.mudasarmajeed5.tech/api/jobs`
Headers: `X-Automation-Secret: <secret>`, `Content-Type: application/json`

```json
{
  "request_type": "CANVA_AUTOMATION",
  "date": "21-Oct-2026",
  "rows": [
    {
      "row_id": "b1c2-uuid",
      "account": "Excel Executive Cars",
      "task_type": "Graphic Design - Static Post/Carousel",
      "task_description": "Static post for 22-Oct-2026",
      "status": "Done",
      "canva_link": "https://www.canva.com/design/DAF.../edit"
    }
  ],
  "google_access_token": "ya29....",
  "requested_by": "user@example.com"
}
```

Responses:
- `202 {"job_id": "abc123", "state": "queued", "rows_written": 3}` — Sheet rows already written.
- `400` validation error `{"error": "...", "details": [...]}` — nothing written.
- `401` wrong/missing secret. `429` rate limited.
- `502 {"error": "sheet_write_failed", "message": "..."}` — nothing queued.

`GET /api/jobs/{job_id}` (secret required):

```json
{
  "job_id": "abc123",
  "state": "running",
  "created_at": "2026-10-21T10:00:00Z",
  "rows": [
    {
      "row_id": "b1c2-uuid",
      "sheet_row": 412,
      "state": "uploading",
      "file": "Excel-Static-Post-22-Oct-2026.png",
      "drive_url": null,
      "error": null,
      "warnings": []
    }
  ]
}
```
Job `state`: `queued | running | done | done_with_errors | failed`.
Row `state`: `pending | downloading | uploading | updating_sheet | done | failed`.
Error `error` is a short human sentence, plus an `error_code` such as `CANVA_SESSION_EXPIRED`, `CANVA_BUTTON_NOT_FOUND`, `DOWNLOAD_TIMEOUT`, `DRIVE_FOLDER_NOT_FOUND`, `DRIVE_UPLOAD_FAILED`, `SHEET_ROW_NOT_FOUND`, `TOKEN_EXPIRED`.

`GET /` → HTML status page. `GET /health` → `{"ok": true}` (no secret).

## 5. Python interfaces every module must honor (file → function)

```python
# models.py
@dataclass
class RowInput:
    row_id: str; account: str; task_type: str
    task_description: str; status: str; canva_link: str

@dataclass
class DownloadedFile:
    path: Path; name: str; size: int          # name = original file name from Canva

@dataclass
class UploadResult:
    file_id: str; web_view_link: str; folder_path: str   # e.g. "Excel Executive Cars/Posts"
    size: int; warnings: list[str]

class AutomationError(Exception):
    code: str; message: str                    # code is one of the error codes above

# sheets.py
def append_rows(token: str, date: str, rows: list[RowInput]) -> dict[str, int]:
    """Append rows (Post Link = canva_link). Returns {row_id: sheet_row_number}."""
def set_post_link(token: str, row_number: int, canva_link: str, drive_url: str) -> None:
    """Replace ONLY the Post Link cell. Verify the cell still holds canva_link; if not, find the row by searching the Post Link column for canva_link; if still not found raise AutomationError('SHEET_ROW_NOT_FOUND')."""

# canva_bot.py
def download_design(canva_link: str, dest_dir: Path) -> DownloadedFile: ...
def check_session() -> bool: ...              # True if Canva profile is logged in

# drive.py
def upload_and_share(token: str, account: str, task_type: str, file: DownloadedFile) -> UploadResult:
    """Pick the folder, upload, verify (size + md5), make 'anyone with link can view', return link."""

# cleanup.py
def cleanup_job_dir(job_dir: Path, uploaded_files: list[Path]) -> None: ...
def sweep_old_leftovers() -> None: ...        # runs at startup and once a day
```

## 6. Downloaded-files rule (IMPORTANT)
- Every job downloads into its own folder: `DOWNLOAD_DIR/<YYYY-MM-DD of the run>/<job_id>/`.
- Files are deleted **once, at the end of the job, after ALL files of that batch have been processed**: every file that was uploaded **and verified** on Drive (size and md5 match) is deleted. Then the job folder and the date folder are removed if empty.
- A file that downloaded but failed to upload is **kept** (so it can be retried by hand) and listed in the job result with its path. Leftovers older than `KEEP_FAILED_DAYS` (default 7) are removed by `sweep_old_leftovers()`.
- Deletion code must only ever touch paths inside `DOWNLOAD_DIR` (resolve real paths, refuse symlinks and anything outside).
- Playwright saves with `expect_download()` + `save_as()`. It does **not** use Chrome's default Downloads folder (Playwright's own temp downloads are deleted when the context closes).

## 7. Security rules
1. HTTPS everywhere. Compare `X-Automation-Secret` with `hmac.compare_digest`.
2. Only allow-listed Google emails (`ALLOWED_EMAILS`) can use the web app.
3. Only the short-lived Google **access token** goes to Python. Hold it in memory for the job. **Never write it to disk, SQLite, logs or error messages.**
4. Validate every Canva link (host must be `canva.com` or `www.canva.com` or `canva.link`, scheme https) on **both** Vercel and Python.
5. Canva Chrome profile directory: permission 700, never in git.
6. Rate limit `POST /api/jobs` (10/min). Max 30 rows per job.
7. The Google access token lasts about 1 hour. If a Google call returns 401 mid-job, mark remaining rows `failed` with code `TOKEN_EXPIRED` (their Post Link stays as the Canva link).

## 8. Environment variables
Vercel: `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`, `AUTOMATION_URL`, `AUTOMATION_SECRET`.
Ubuntu `.env` (mode 600): `AUTOMATION_SECRET`, `ROOT_FOLDER_ID`, `SHEET_ID`, `SHEET_TAB`, `DATE_INPUT_MODE` (`RAW` default, or `USER_ENTERED` if the Date column is a real date type), `DOWNLOAD_DIR`, `CHROME_PROFILE_DIR`, `KEEP_FAILED_DAYS=7`, `CREATE_MISSING_FOLDERS=false`, `MAX_ROWS_PER_JOB=30`.

## 9. Tech decisions already made
Better Auth (Google, `accessType: "offline"`, `prompt: "select_account consent"`) + Neon Postgres; FastAPI + uvicorn under systemd; Caddy for HTTPS; Playwright (Python) driving real Google Chrome with a persistent logged-in Canva profile, headed under Xvfb; one job at a time.

## 10. Rules for the implementer (DeepSeek)
- Before coding, **check the latest official docs** of every library named in "Docs to check" below. Do not rely on memory for API names/options.
- Implement **only this module**. Where another module is needed, use the interface in Section 5 (a stub is fine).
- Don't invent extra features. Write small, readable code with type hints and clear error messages. No secrets in code.
- Finish with: the file list, how to run the acceptance tests, and anything you were unsure about.
