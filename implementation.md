# Implementation Plan — Canva Automation

Read this end-to-end before touching code. Implement **in order**. Do not skip steps.
Do **not** `git push` unless the user explicitly says push.

## Invariants (must not break)

- Sheets updates work today (date blocks, `||` continuation, smart-chip Post Link, cell-scoped sync).
- Drive upload works today (fresh upload, in-place replace, verification, sharing).
- Re-upload from UI (`↻ Re-download`) sends `replace: true` + `existing_drive_url`; backend keeps the **same file ID/URL**.
- Keyword routing (`routing.yaml` + `_find_child`) must remain as fallback when AI routing is unavailable or low-confidence.
- Error responses must never be HTTP 502 (Cloudflare masks 502 bodies → frontend shows "unexpected response").
- Local downloads are deleted after verified upload; failed uploads are kept.
- Package manager is **npm**. Backend runs as `canva-automation.service` (restart without sudo). Frontend deploys via Vercel on push.

## Environment facts

- Server `.env` (already present): `GEMINI_API_KEY`, `GROQ_API_KEY`, `SMTP_APP_PASSWORD`.
- Gmail SMTP: host **`smtp.gmail.com` port 587 STARTTLS** (`smtp.google.com` times out — do not use).
- SMTP user/from: `hsarosh569@gmail.com`.
- Email recipient: frontend `session.user.email`; if empty → fallback `hsarosh569@gmail.com`.
- GROQ primary model: `openai/gpt-oss-120b` (JSON mode verified). Gemini fallback: `gemini-3.8-flash` (retry 503/429 ×2).
- Status vocabulary after this change: `("Not Started", "In Progress", "Done", "Blocked")`. **Replace every `Completed` with `Done`.**
- New-task default status stays `Not Started`; set `Done` only after successful upload.
- No pytest — run inline `mock.patch` scripts with `server/.venv/bin/python`.
- Sheet dates use `D-MMM-YYYY` (e.g. `27-Oct-2026`).

---

## Task 1 — Sheet append to true end of sheet

**Bug:** `_append_values` in `server/sheets.py` calls `values.append(range=A1, INSERT_ROWS)`. Google treats A1 as a table and inserts at the **first empty row**, so new tasks land mid-sheet when blank rows separate date blocks.

**Fix (only `server/sheets.py`):**

1. Add helper `_last_used_row(service, sid, tab) -> int`:
   - Read full grid `A:Z` (or at least through the header width) via `_get_values`.
   - Return the 1-based sheet row number of the **last row with any non-empty cell** (header counts; if only headers, return 1).
2. Change `_append_values`:
   - Compute `start = _last_used_row(...) + 1`.
   - Use `values().update` on `A{start}:{col}{start+len(values)-1}` (not `append`, not `INSERT_ROWS`).
   - Return `{row.row_id: start + index}`.
3. Keep `_append_block` mid-sheet `insertDimension` path **only** when joining an existing date block that is not last (intentional block layout).
4. New-date and last-block paths continue through `_append_values` → now guaranteed sheet end.

**Verify:** unit-test `_last_used_row` with a mocked grid containing a mid-sheet blank row; assert new rows write after the true last row.

---

## Task 2 — Status vocabulary `Completed` → `Done`

**Backend:** `server/models.py` — `STATUSES = ("Not Started", "In Progress", "Done", "Blocked")`.

**Frontend:** `next-canva/app/page.tsx` — `const STATUSES = ["Not Started", "In Progress", "Done", "Blocked"] as const;`.

- `DEFAULT_STATUS` stays `"Not Started"`.
- `RowUpdate._check_status` already accepts any non-empty ≤64 chars (legacy `Completed` rows keep working).
- `statusOptions()` already lists the current value if not in the list — no change needed there.
- Grep for remaining `Completed` in app code (not docs/plan.md) and replace.

---

## Task 3 — Auto-set Status = `Done` after successful upload

**Where:** `server/pipeline.py` `_process_row`, after Post Link write succeeds (after `sheets.set_drive_link` / no-op when URL unchanged), before `set_row_state(..., "done")`.

**How:** new `sheets.set_status(token, row_number, status, sheet_id="")` in `server/sheets.py`:
- Resolve tab + header map; column letter for `Status`.
- `values().update` single cell to `status` (default `"Done"`).
- Same error mapping as other sheet helpers (`AutomationError`).
- Call it with `"Done"` from the pipeline for both fresh upload and replace paths (replace also ends `done`).

**Do not** fail the whole row if Status write fails after the file is already on Drive — log a warning, still mark row `done`, include warning in email.

---

## Task 4 — Email notifications (Google SMTP)

### 4a. Config (`server/config.py` + `.env`)

Add to `Settings` (with defaults so missing keys don't crash startup):

- `smtp_host` = `_env("SMTP_HOST", "smtp.gmail.com")`
- `smtp_port` = `_env_int("SMTP_PORT", 587)`
- `smtp_user` = `_env("SMTP_USER", "hsarosh569@gmail.com")`
- `smtp_app_password` = `_env("SMTP_APP_PASSWORD", "")` (already in `.env`)
- `smtp_from` = `_env("SMTP_FROM", "hsarosh569@gmail.com")`
- `email_fallback_to` = `_env("EMAIL_FALLBACK_TO", "hsarosh569@gmail.com")`

Update `.env.example` with these keys (no real password in the example).

### 4b. New `server/emailer.py`

```text
send_upload_report(to: str, subject: str, lines: list[str]) -> bool
```

- stdlib `smtplib` + `email.message.EmailMessage`.
- STARTTLS to `smtp_host:smtp_port`, auth `smtp_user` / `smtp_app_password`, from `smtp_from`.
- Timeout 10s. Return `False` and `log.exception` on any failure — **never raise into the pipeline**.
- Build a plain-text body from `lines` (one bullet per file).

### 4c. Persist recipient on the job

- `DownloadRequest` (`models.py`): add `notify_email: str = ""` (validator: empty or valid-ish email, max 320). `extra="forbid"` means frontend **must** send it or omit — omit is fine (default `""`).
- `jobs.create_job(...)`: new kwarg `notify_email: str = ""`; store in `jobs` table.
- Migration: `ALTER TABLE jobs ADD COLUMN notify_email TEXT NOT NULL DEFAULT ''` guarded like existing PRAGMA migrations.
- `_job_to_dict`: include `notify_email`.

### 4d. Frontend — pass `session.user.email`

In `next-canva/app/page.tsx`, all three `/api/download` POST bodies add:

```ts
notify_email: activeEmail.trim(),
```

Sites: `handleDownload` (Re-download), `handleUploadAll`, New-task auto-queue download block.

### 4e. Send at job end (`server/pipeline.py`)

After `store.set_job_state(job_id, final_state)`:

1. `final_job = store.get_job(job_id)` (already loaded).
2. `to = (job.get("notify_email") or "").strip() or settings.email_fallback_to`.
3. Build `lines` from `final_job["rows"]`:
   - done + replace: `REPLACED: {file_name} → {folder_path or "same Drive link"} — previous version replaced ({drive_url})`
   - done + fresh: `UPLOADED: {file_name} → {folder_path} ({drive_url})`
   - failed: `FAILED: {account}/{task_type} — {error}`
4. Subject: `Canva upload report — {date} ({n} file(s))` or `… with errors`.
5. `await asyncio.to_thread(send_upload_report, to, subject, lines)` inside try/except.

Use row fields already stored: `file_name`, `drive_url`, `warnings`, `state`, `error`. `folder_path` may be on the upload result — if not persisted, derive from warnings/`drive_url` or add `folder_path` to `set_row_state` (prefer: extend `set_row_state` / `job_rows` with `folder_path TEXT` if missing; otherwise parse from upload warnings — simplest is to pass `folder_path` through `set_row_state` like `drive_url`).

**Simplest robust approach:** in `_process_row`, after upload, call `store.set_row_state(..., drive_url=..., warnings=..., folder_path=upload.folder_path)` — add optional `folder_path` kwarg that updates the column (add column if needed).

---

## Task 5 — Re-upload E2E (verify, minimal code)

Already correct in UI/backend. Verify only:

1. Row with existing Post Link → `↻ Re-download` → payload `replace: true`.
2. Backend `replace_file` keeps file ID; Sheet chip untouched when URL unchanged.
3. Status column set to `Done`.
4. Email contains REPLACED line with same Drive URL.
5. If replace falls back to fresh upload (file gone), email says UPLOADED and Sheet chip is rewritten.

Fix only if a gap is found (e.g. missing `notify_email` on one payload, Status not set on replace path).

---

## Task 6 — AI folder routing (GROQ → Gemini → keyword)

### 6a. Config additions

- `groq_api_key`, `gemini_api_key` from env (already in `.env`).
- `routing_min_confidence` float default `0.75` (`ROUTING_MIN_CONFIDENCE`).
- `routing_max_depth` int default `3`.
- `routing_max_nodes` int default `300`.
- `routing_timeout_seconds` int default `20`.
- `routing_model_groq` default `openai/gpt-oss-120b`.
- `routing_model_gemini` default `gemini-3.8-flash`.

### 6b. New `server/folder_tree.py`

```text
explore_folder_tree(service, root_id, *, max_depth, max_nodes) -> list[dict]
# each: {"id", "name", "path", "depth"}  path like "Root/Business/Sub"
```

- BFS with Drive `files().list` (`mimeType=folder`, `trashed=false`, `supportsAllDrives=True`).
- Stop at depth limit or node cap.
- Job-scoped cache: module dict `{root_id: tree}` + `clear_folder_tree_cache()`; call from `drive.clear_folder_cache()` or pipeline job start (pipeline already calls `drive.clear_folder_cache()` — hook there).
- Raise `AutomationError("DRIVE_FOLDER_MISSING")` if root unreadable.

### 6c. New `server/llm_router.py`

```text
route(tree, file_name, account, task_type, settings) -> dict | None
# {"folder_id", "path", "confidence", "reason"} or None
```

1. **GROQ first:** POST `https://api.groq.com/openai/v1/chat/completions`
   - `response_format: {type: "json_object"}`, `model=settings.routing_model_groq`.
   - On any error/timeout/bad JSON → step 2.
2. **Gemini fallback:** POST `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...`
   - `generationConfig.responseMimeType = "application/json"`.
   - Retry 429/503 up to 2 times. On failure → step 3.
3. **Return None** → caller uses keyword routing.

**Prompt (user message):** compact indented tree with IDs + file name + account + task type + instruction to return only:

```json
{"folder_id": "...", "path": "...", "confidence": 0.0, "reason": "..."}
```

**Validation (hard):**
- `folder_id` must be in the explored tree id set (reject hallucinated IDs).
- `confidence` must be `>= settings.routing_min_confidence`.
- Else return `None`.

Use `requests` (already installed). Timeout `routing_timeout_seconds`.

### 6d. Wire into `drive.upload_and_share`

After `root_id` resolved, **before** keyword `_find_child`:

1. Explore tree (cached per job).
2. `decision = llm_router.route(...)`.
3. If decision: upload directly to `decision["folder_id"]`; `folder_path = decision["path"]`; warning `routed by AI (confidence X.XX)`.
4. If None: existing keyword path unchanged; optional warning `keyword routing (AI unavailable or low confidence)`.

**Do not** change `replace_file` (same location).

**Account keyword still used only as fallback.** Filename never changes target except via AI/keyword rules.

---

## Task 7 — Tests (inline, no pytest)

Run with `server/.venv/bin/python` from `server/`:

1. **Append:** mock `_get_values` / `_execute` with a grid that has a mid-sheet blank; assert `_append_values` targets `last+1` and uses `update`.
2. **Status vocab:** `STATUSES` contains `"Done"`, not `"Completed"`.
3. **set_status:** mock service; assert range is Status column + correct row.
4. **Emailer:** mock `smtplib.SMTP`; assert STARTTLS/login/send; failure returns False.
5. **notify_email:** `DownloadRequest` accepts `notify_email`; create_job persists it (temp DB).
6. **Folder tree:** mock `files().list`; depth/node caps; cache cleared.
7. **LLM router:** mock GROQ success; GROQ fail → Gemini success; both fail → None; low confidence → None; bad folder_id → None.
8. **upload_and_share:** mock tree+router → uses AI folder; router None → keyword path.
9. **Regression:** existing chip/replace/status-mapping inline tests still pass.
10. `python -m py_compile` on all changed `.py` files.
11. `npx tsc --noEmit` in `next-canva`.

---

## Task 8 — Deploy & live verify

1. `systemctl restart canva-automation` (no sudo); `curl -s http://127.0.0.1:8473/health` → `{"ok":true}`.
2. Live: `GET /api/sheet-rows` on a sheet with a mid-sheet blank + new POST append → row numbers after true end; Status column becomes `Done` after a real or mocked upload path if safe to test.
3. Email: run a one-off `emailer.send_upload_report` to `hsarosh569@gmail.com` with a test line — confirm delivery (check phone).
4. Tunnel health: `https://events.mudasarmajeed5.tech/health`.
5. Frontend: after Vercel deploy (only if user says push), confirm CSS/content change.
6. **Never push without explicit user "push".**

---

## File touch list

| File | Change |
|------|--------|
| `server/sheets.py` | `_last_used_row`, `_append_values` fix, `set_status` |
| `server/models.py` | `STATUSES` Done; `DownloadRequest.notify_email` |
| `server/pipeline.py` | set Status Done; job-end email; pass folder_path |
| `server/jobs.py` | `notify_email` column + create_job + `_job_to_dict`; optional `folder_path` |
| `server/config.py` | SMTP + routing settings |
| `server/emailer.py` | **new** |
| `server/folder_tree.py` | **new** |
| `server/llm_router.py` | **new** |
| `server/drive.py` | AI routing branch in `upload_and_share`; tree cache clear |
| `server/main.py` | pass `notify_email` into `create_job` |
| `next-canva/app/page.tsx` | `STATUSES` Done; `notify_email` on 3 download payloads |
| `.env.example` | SMTP + routing keys (no secrets) |
| `implementation.md` | this file |

## Out of scope

- Changing Auth.js / OAuth scopes.
- Changing Canva download logic.
- Deleting the old keyword `routing.yaml` (keep as fallback).
- Mass-updating historical `Completed` cells in user sheets (leave history alone).
