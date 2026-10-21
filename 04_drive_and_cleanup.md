# MODULE 4 of 5 — Google Drive upload, folder routing, and download cleanup

**Goal:** replace the `drive.py` and `cleanup.py` stubs. Put each downloaded file into the right Drive folder, make a share link, verify the upload, and delete the local files afterwards.

## Files to create/replace
`drive.py` · `routing.py` · `routing.yaml` · `cleanup.py` · `drive_cli.py` (manual test tool).

## Interfaces to honor (Section 5)
```python
def upload_and_share(token: str, account: str, task_type: str, file: DownloadedFile) -> UploadResult
def cleanup_job_dir(job_dir: Path, uploaded_files: list[Path]) -> None
def sweep_old_leftovers() -> None
```
Error codes: `DRIVE_FOLDER_NOT_FOUND`, `DRIVE_UPLOAD_FAILED`, `DRIVE_VERIFY_FAILED`, `DRIVE_PERMISSION_DENIED`, `TOKEN_EXPIRED`.

## Requirements — routing
Root folder: `ROOT_FOLDER_ID`. Structure: `Root / <business folder> / <content-type subfolder>`.

**Step 1 — business folder:** chosen from the **`account` value** (what the user picked in the form, not the file name). Map in `routing.yaml`:

| account value | folder-name keyword |
|---|---|
| Excel Executive Cars | excel |
| Platinum Exetuive Car | platinum |
| Reading Chauffers | reading |
| First Class Cars | first class |

List the real children of the root via the Drive API and match by **normalized name** (lowercase, remove spaces/dashes/underscores, "contains keyword"). No match → `DRIVE_FOLDER_NOT_FOUND`.

**Step 2 — subfolder:** from the **file name** (lowercase), first match wins:

| keyword in file name | subfolder name hint |
|---|---|
| `carousel` | carousel |
| `reel` or extension `.mp4` | reel |
| `meme` | meme |
| `story`, `stories`, `highlight` | insta (InstaAssets) |
| `post`, `static` | post |

Fallback if no keyword matches, based on `task_type`: `Video/Reel Editing` → reel; `Branding/Other` → insta; `Graphic Design - Static Post/Carousel` → post.
Match the hint against the **real subfolder names** inside the business folder (normalized, "contains"). All keywords and hints live in `routing.yaml` so they can be edited without touching code.
⚠️ The exact real subfolder names are not known to the plan author — **never hard-code them**. If no subfolder matches: upload into the **business folder itself** and add a warning to `UploadResult.warnings` (e.g. "no subfolder matched, saved in business folder"). Create folders only if `CREATE_MISSING_FOLDERS=true`.
Sanity check: if the file name starts with a different business than `account` (e.g. file `Platinum-…` but account is Excel) → still upload per `account`, add a warning.

## Requirements — upload
- Credentials from the access token only; Drive API v3 via `google-api-python-client`; pass `supportsAllDrives=True` on calls.
- Use **resumable upload** with `MediaFileUpload(..., resumable=True)` in chunks (e.g. 8 MB) for large videos. Set the correct MIME type (`mimetypes`).
- **Name collision:** if a file with the same name exists in the target folder, upload as `name (2).ext`, `name (3).ext`… (never overwrite).
- **Verify:** after upload, `files.get(fields="id,size,md5Checksum,webViewLink")` and compare `size` and `md5Checksum` with the local file (compute local md5). Mismatch → delete the bad remote copy if possible and raise `DRIVE_VERIFY_FAILED`.
- **Share:** `permissions.create` with `type=anyone`, `role=reader`; return `webViewLink`.
- Cache folder-ID lookups inside one job (module-level dict cleared per job) to save API calls.
- Map HTTP errors: 401 → `TOKEN_EXPIRED`, 403 → `DRIVE_PERMISSION_DENIED`, 404 on root → `DRIVE_FOLDER_NOT_FOUND`, 429/5xx → retry 4× with backoff then `DRIVE_UPLOAD_FAILED`. Never log the token.

## Requirements — cleanup (see Section 6, follow it exactly)
- `cleanup_job_dir(job_dir, uploaded_files)` is called **once at the end of the job**. `uploaded_files` are only files that were uploaded **and verified**.
  - Delete each file in `uploaded_files`.
  - Keep any other file in `job_dir` (downloaded but not uploaded) and log its path.
  - Remove `job_dir` if now empty, then its parent date folder if empty.
- **Safety:** `Path.resolve()` every path; refuse (raise) if it is not inside `DOWNLOAD_DIR`; never follow symlinks; never delete `DOWNLOAD_DIR` itself.
- `sweep_old_leftovers()`: delete job folders under `DOWNLOAD_DIR` whose files are older than `KEEP_FAILED_DAYS` (default 7), then empty date folders.

## Docs to check
Google Drive API v3: `files.list` (q syntax, `supportsAllDrives`, `includeItemsFromAllDrives`), `files.create` + resumable `MediaFileUpload`, `files.get` fields (`md5Checksum`, `webViewLink`), `permissions.create` · `pyyaml`.

## Acceptance tests (the human runs these; use a test folder id first)
`drive_cli.py` takes `TEST_TOKEN`, `--account`, `--task-type`, and a local file.
1. Upload `Excel-Static-Post-22-Oct-2026.png` with account Excel → lands in Excel's "post"-like subfolder; the printed link opens in a private window.
2. Upload `Reading-Carousel-23-Sep-2026.zip`, `Platinum-Reel-24-Sep-2026.mp4`, `First-Class-Meme-16-Sep-2026.png`, `Excel-Stories-8-Oct-2026` → each goes to the right subfolder. Upload the same file twice → second becomes `(2)`.
3. File with an unknown keyword → falls back by task type; a business with no matching subfolder → business folder + warning.
4. A 200 MB video uploads fully; md5 verification passes.
5. Cleanup: create a fake job dir with 3 files, mark 2 as uploaded → those 2 are deleted, 1 remains, folder remains; mark all 3 → folders vanish. Try passing `/etc/passwd` or a symlink → refused.
6. Expired token → `TOKEN_EXPIRED`. Logs never contain the token.

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
