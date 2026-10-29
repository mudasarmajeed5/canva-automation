# Drive routing verification — 29 October 2026

Implemented locally; worker has not been restarted or deployed. No Drive files
were uploaded, moved, deleted, restored, or changed during this work.

## Design

Routing now uses `folder_resolver.py`, not the LLM or keyword fallback.

1. Require a known Sheet business. If the filename identifies a different
   business, stop before Drive discovery or upload. Correct the Sheet account
   or Canva link; neither source silently overrides the other.
2. Identify one content type using explicit filename markers. Unknown or
   conflicting types stop delivery. An unmarked video task can select reels.
3. Use the filename's publication date; if absent, use the job date. Never
   guess from the machine's current month. Invalid or multiple dates stop delivery.
4. Resolve exact, case-insensitive aliases down the existing hierarchy:
   configured root / business / publication month / content type.
   Facebook cover assets use business / Facebook Assets.
5. Require a matching folder at every level. For duplicate matches, choose the
   unique folder with the newest modifiedTime across itself and all visible,
   non-trashed descendants. Ties, missing timestamps, incomplete searches or
   scans exceeding 10000 items stop delivery. Missing matches stop delivery; there is no fallback to the business folder or My Drive.
   Folder listing follows pagination and excludes trashed folders.
6. Construct report paths from actual Drive metadata, never model-generated text.
7. For actual future uploads, verify returned item parents before reporting success.
   For folder downloads, verify the new container before uploading children and
   verify each child's parent. Replacements also validate destination and existing
   parent before updating content.

Aliases are explicit in `BUSINESSES` and `CONTENT` in `folder_resolver.py`.
New naming conventions must be added there; unfamiliar names stop delivery.
The old LLM module remains available, but no longer controls production uploads.
Its legacy validation also now uses the canonical tree path.

## Replay against your actual Drive folder snapshot

Read-only Google Drive metadata queries captured 48 existing folders under the
configured root, through business / month / content type. The production resolver
was then run offline against that snapshot using yesterday's saved job inputs.
The replay service exposes only metadata reads and rejects mutation operations.

Job: `job_7b673cc807518e5b`, date: `28-Oct-2026`.
All READY paths below are beneath `Aimen- Saroosh GD Data Ex, Pla, RC, FC`.

| File | Result | Destination or reason |
| --- | --- | --- |
| Reading-Stories-29-Oct-2026 | READY | READING CHAUFFEURS / October 2026 / Stories |
| Excel-Stories-29-Oct-2026 | BLOCKED | Sheet account is First Class Cars; filename identifies Excel |
| Platinum-Stories-29-Oct-2026 | READY | Platinum Executive Cars / October 2026 / Stories |
| First-Class-Cars-Stories-29-Oct-2026 | BLOCKED | Sheet account is Excel Executive Cars; filename identifies First Class |
| Platinum-FB-CoverPage.png | READY | Platinum Executive Cars / Facebook Assets |

Verified target IDs:

- Reading Stories: `1CKRjL_j_52-ArTSz7POTW6x-QhTs6leW`
- Platinum Stories: `18oGnEkvFp32O8Fe0fh9ycXla3mhcnV7U`
- Platinum Facebook Assets: `1Wvs9kkPX-FAcSzig4bDoQP4-jLRug1eD`

Additional replay: correcting Excel's account to Excel Executive Cars is still
BLOCKED because EXCEL CAR / October 2026 contains BOTH:

- `Stories`: `1ZdZvHrygEc8W_N22Ip-rxgTihoz9dK1E`
- `STORIES`: `1D18eKEq5XnhzFfnrTEACuXN3QZY55g0a`

That month also has two `Static Posts` folders. The initial replay above used
strict duplicate rejection. Following your updated instruction, these duplicate
folders now use recent modification activity to select the destination. The
folder snapshot did not contain activity timestamps, so the actual winner has
not been rechecked against live Drive. No duplicates were modified.

## Automated validation

34 tests passed in `test_folder_resolver.py`, covering all four businesses,
yesterday's two account conflicts, wrong content folder prevention, duplicate
business/month/type folders, case variants, missing and trashed folders,
pagination, duplicate folder and descendant activity, trashed activity exclusion,
shortcuts, missing activity timestamps, publication month boundaries, missing/invalid/conflicting dates,
unknown/conflicting content, canonical report paths, parent verification,
replacement rejection before update, and upload integration with mocked writes.
Compilation and `git diff --check` passed.

Run from the repository root:

```sh
server/.venv/bin/python -m unittest discover -s server -p test_folder_resolver.py -v
```

Reusable live metadata-only replay (prompts for a hidden token; never stores it):

```sh
server/.venv/bin/python server/routing_dry_run.py job_7b673cc807518e5b --output /tmp/routing-report.json
```

## Limits

This design prefers a clear failure over a guessed destination. It cannot
promise every input will upload successfully. Filenames, accounts, dates and
folder structure must be consistent. Untested content naming conventions will
be blocked until explicitly supported. Drive folders can also be renamed or
moved concurrently; parent checks detect observed placement errors but cannot
prevent someone moving a file after verification. No end-to-end upload was
performed, as requested.
