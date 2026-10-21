# MODULE 5 of 5 — Next.js web app (Vercel) + final end-to-end test

**Goal:** the web UI the team uses: Google login, a sheet-style form where the user fills each row (Date, Business/Account, Task Type, Task Description, Status, Canva link), the Upload button, live progress, and final Drive links. Then run the full end-to-end test of all 5 modules.

## Files to create (`canva-web/`, Next.js App Router + TypeScript + Tailwind)
`lib/auth.ts` (Better Auth config) · `lib/auth-client.ts` · `lib/db.ts` · `lib/constants.ts` (the fixed values from Section 2) · `lib/validate.ts` (zod schemas) · `app/login/page.tsx` · `app/page.tsx` (protected form) · `app/api/auth/[...all]/route.ts` · `app/api/submit/route.ts` · `app/api/status/[jobId]/route.ts` · `app/api/automation-health/route.ts` · `components/RowEditor.tsx` · `components/ProgressTable.tsx` · `.env.example` · `README.md`.

## Requirements — auth
- **Better Auth** with the Google provider: `accessType: "offline"`, `prompt: "select_account consent"`, scopes from Section 2 (check docs for the exact option names). Store accounts/sessions in **Neon Postgres** via the adapter Better Auth documents.
- **Allow-list:** env `ALLOWED_EMAILS`. After login, reject (sign out + message) any email not in the list.
- Get the Google access token **only on the server** with Better Auth's "get access token" function (it refreshes automatically). Never expose it to client code.
- If the Google account did not grant both Sheets and Drive scopes, show a clear "please sign in again and allow access" message.

## Requirements — the form (`/`)
Layout, simple and fast, everything on one page:
1. **Date** picker at the top (applies to all rows; sent as `D-MMM-YYYY`, e.g. `21-Oct-2026`; default today).
2. A **rows table** (cards on mobile). Each row has:
   - **Business/Account** — dropdown (4 values, Section 2; show nice label, send the sheet value)
   - **Task Type** — dropdown (3 values)
   - **Task Description** — text input
   - **Status** — dropdown (`Done`, `In Progress`, `Completed`; default `Done`)
   - **Canva link** — input to paste the link (this becomes the sheet's Post Link)
   - a remove (trash) icon
3. A **`+ Add row`** button under the table. A new row copies Task Type, Status and Task Description from the previous row (not the link, not the account) to save typing. Start with one empty row.
4. **Upload** button: disabled until Date is set and every non-empty row is complete and valid (all fields filled, link starts with `https://www.canva.com/design/` or is a `canva.link` URL). Completely empty rows are ignored. Show inline validation errors.
5. Small banner at the top: "Automation online / offline" using `/api/automation-health` (which calls `AUTOMATION_URL/health`).

## Requirements — submit & progress
- `POST /api/submit`: verify session + allow-list → validate with zod (same rules as Python) → get fresh Google access token server-side → build the Section 4 request (`request_type: "CANVA_AUTOMATION"`, generate a `row_id` uuid per row, `requested_by` = user email) → `POST` to `AUTOMATION_URL/api/jobs` with header `X-Automation-Secret` → return `{ jobId, rowsWritten }`. Pass through Python's 400/502 errors as readable messages. **Keep the form content if submission fails.**
- `GET /api/status/[jobId]`: verify session, proxy to `AUTOMATION_URL/api/jobs/{id}` with the secret. The secret never reaches the browser.
- After submit, replace the form with a **progress table** (one line per row: account, description, state with spinner/check/cross, error message if any). Poll `/api/status/...` every 3 seconds until the job state is `done`, `done_with_errors` or `failed`.
- When a row is `done`, show its **Drive link** (clickable, with a copy button). Show a clear summary: "3 of 4 done — 1 failed (reason). Failed rows keep their Canva link in the Sheet."
- Buttons: "Start another batch" and a link "Open the Google Sheet" (env `NEXT_PUBLIC_SHEET_URL`).
- If automation is offline when pressing Upload: show "The automation server is offline. Nothing was written. Try again later." and keep the form.

## Requirements — deploy
- Deploy to Vercel; set the env vars listed in Section 8. Add the Google OAuth redirect `https://<vercel-domain>/api/auth/callback/google`.
- Do not log tokens, request bodies, or the secret.

## Docs to check
Better Auth (Next.js integration, Google provider, database adapters, `getAccessToken`) · Next.js App Router route handlers · zod · Tailwind · Vercel env vars · Neon Postgres.

## Human setup checklist for this module
1. Google Cloud Console: new project → enable **Drive API** and **Sheets API** → OAuth consent screen (add the two scopes, add yourself/team as **Test users**; note: in Testing mode Google refresh tokens expire after 7 days, so people sign in again weekly) → OAuth client (Web) with the redirect above.
2. Create the Neon database, put `DATABASE_URL` in Vercel.
3. Share the Drive folder and the Sheet (edit access) with every Google account that will log in.

## Acceptance tests (module 5 alone, with module 1 stubs running)
1. A non-allow-listed Google account is rejected. An allowed one reaches the form.
2. Add 3 rows with `+`, remove one, validation blocks Upload until everything is filled; link `https://example.com` is rejected.
3. Upload → progress table shows each row moving through states; Drive links appear (fake ones from the stub).
4. Stop the Python server → banner turns "offline", Upload shows the offline message and the form is preserved.

## FINAL END-TO-END TEST (after modules 1–5 are all real)
1. Use a **test tab** in the Sheet and a **test Drive folder** first.
2. Submit 4 rows: one Excel static post, one Platinum carousel, one Reading reel, one First Class story (all real Canva edit links).
3. Expected within a few minutes: 4 new Sheet rows appear instantly with Canva links in **Post Link**; as each file finishes, its Post Link cell turns into a Drive link that opens; files are in the right Drive subfolders; the server's `DOWNLOAD_DIR` ends **empty** (no job/date folders left); the status page shows idle.
4. Failure test: include one bad link → that row stays with its Canva link and shows an error; the other rows complete; only the files that uploaded are deleted from disk.
5. Switch config from test folder/tab to the real ones.

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
