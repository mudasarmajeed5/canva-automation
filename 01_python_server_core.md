# MODULE 1 of 5 — Python server core (API, queue, status page, deploy)

**Goal:** the always-on Python backend at `events.mudasarmajeed5.tech` that receives jobs, runs them one at a time, shows a status page, and exposes progress. The Canva / Drive / Sheets work is plugged in later by modules 2–4, so here you build the **pipeline skeleton using stub implementations** of those modules.

## Files to create (`canva-automation/`)
`main.py` (FastAPI app) · `config.py` (loads `.env`) · `models.py` (dataclasses from Section 5 + pydantic request models) · `jobs.py` (job store + queue) · `worker.py` (background worker loop) · `pipeline.py` (`run_job`) · **stubs:** `sheets.py`, `canva_bot.py`, `drive.py`, `cleanup.py` (each implements the Section 5 signature and returns fake-but-realistic data, e.g. sleeps 1s and returns a made-up file/link) · `templates/status.html` · `requirements.txt` · `.env.example` · `deploy/canva-automation.service` · `deploy/Caddyfile` · `README.md` (setup + curl examples).

## Requirements
**Endpoints** (exact contract in Section 4): `GET /`, `GET /health`, `POST /api/jobs`, `GET /api/jobs/{job_id}`.

**POST /api/jobs flow, in this order:**
1. Check `X-Automation-Secret` (constant-time). 401 if wrong.
2. Validate body with pydantic: `request_type == "CANVA_AUTOMATION"`; `date` matches `D-MMM-YYYY`; 1–`MAX_ROWS_PER_JOB` rows; each `account`, `task_type`, `status` is in the fixed lists (Section 2); `task_description` non-empty (max 500 chars); `canva_link` passes rule 4 of Section 7; `row_id` unique; `google_access_token` present. 400 with a clear list of problems otherwise.
3. Call `sheets.append_rows(...)` (run it in a thread so the event loop isn't blocked). If it raises → `502 sheet_write_failed`, **do not queue anything**.
4. Create the job (state `queued`), enqueue it, return `202` with `rows_written`.

**Job store:** SQLite file (job id, state, timestamps, rows with state/file/drive_url/error/warnings/sheet_row). **The Google token is kept only in an in-memory dict keyed by job_id and is never written to SQLite or logs.** On startup, any job left `queued`/`running` in SQLite (token lost) is marked `failed` with message "server restarted". Delete jobs older than 24 h.

**Worker:** a single background task that takes one job at a time. `pipeline.run_job(job, token)` for each row, sequentially:
```
state=downloading  → canva_bot.download_design(link, job_dir)
state=uploading    → drive.upload_and_share(token, account, task_type, file)
state=updating_sheet → sheets.set_post_link(token, sheet_row, canva_link, drive_url)
state=done
```
- Catch `AutomationError` per row → row `failed` with `error_code` + message, **continue with the next row**.
- If the error code is `TOKEN_EXPIRED`, mark all remaining rows failed and stop.
- `job_dir = DOWNLOAD_DIR/<YYYY-MM-DD>/<job_id>/` (create it).
- After the loop call `cleanup.cleanup_job_dir(job_dir, uploaded_files)` once (rule in Section 6) and set the job state `done` (all rows done) or `done_with_errors`.
- Add a 1–3 s random pause between rows.
- Call `cleanup.sweep_old_leftovers()` at startup and once every 24 h.

**Status page `GET /`:** clean single HTML page (no framework, inline CSS, auto-refresh every 10 s). Show: green/red dot "Automation online"; worker state (idle / running job X, row n of m); queue length; last successful job time; **Canva session status** (call `canva_bot.check_session()` at most once every 5 minutes and cache it; stub returns True); free disk space of `DOWNLOAD_DIR` and number of leftover files. Never show tokens, secrets, links or user emails.

**Deploy files:**
- systemd unit: `ExecStart=/usr/bin/xvfb-run -a /path/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000`, `Restart=always`, `EnvironmentFile=/path/.env`, runs as a non-root user.
- Caddyfile: `events.mudasarmajeed5.tech { reverse_proxy 127.0.0.1:8000 }`.
- README: install steps (Python 3.12 venv, `xvfb`, Caddy), how to start/stop, how to read logs (`journalctl -u canva-automation`).

## Docs to check
FastAPI (lifespan, background tasks, Pydantic v2) · Python `sqlite3` or `aiosqlite` · Caddy reverse_proxy · systemd service units.

## Acceptance tests (the human runs these)
1. `curl https://events.mudasarmajeed5.tech/health` → `{"ok":true}`; opening `/` in a browser shows the green "online" page.
2. POST with a wrong secret → 401. POST with an invalid account name or a non-Canva link → 400 listing the problem.
3. POST a valid job with 3 rows (stubs active) → immediate 202; polling `GET /api/jobs/{id}` shows rows moving `pending → downloading → uploading → updating_sheet → done`, one row at a time; final state `done`.
4. Make the stub `drive.upload_and_share` raise for row 2 → row 2 `failed` with its code, rows 1 and 3 `done`, job `done_with_errors`.
5. Restart the service mid-job → that job becomes `failed` ("server restarted"); the token is nowhere in the SQLite file or logs (grep for it).

## Hand-off to the next modules
Stub files are replaced one by one by modules 2 (`canva_bot.py`), 3 (`sheets.py`), 4 (`drive.py`, `cleanup.py`). Do **not** change function signatures.

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
