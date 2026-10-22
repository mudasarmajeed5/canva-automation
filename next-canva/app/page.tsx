"use client";

import { FormEvent, Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { signIn, signOut, useSession } from "next-auth/react";

const ACCOUNTS = [
  ["Excel Executive Cars", "Excel Executive Cars"],
  ["Platinum Executive Car", "Platinum Exetuive Car"],
  ["Reading Chauffers", "Reading Chauffers"],
  ["First Class Cars", "First Class Cars"],
] as const;

const TASK_TYPES = [
  "Graphic Design - Static Post/Carousel",
  "Video/Reel Editing",
  "Branding/Other",
] as const;

const STATUSES = ["Done", "In Progress", "Completed"] as const;

type SheetRow = {
  row_number: number;
  date: string;
  account: string;
  task_type: string;
  task_description: string;
  status: string;
  post_link: string;
};

type NewEntry = {
  account: string;
  task_type: string;
  task_description: string;
  status: string;
  canva_link: string;
};

type Job = {
  job_id: string;
  state: string;
  rows: Array<{
    row_id: string;
    sheet_row: number | null;
    state: string;
    file: string | null;
    drive_url: string | null;
    error: string | null;
  }>;
};

type ApiBody = {
  message?: string;
  error?: string;
  rows?: SheetRow[];
  rows_written?: number;
  row_numbers?: Record<string, number>;
  updated?: number;
  online?: boolean;
  job_id?: string;
  state?: string;
};

async function readJson(response: Response): Promise<ApiBody> {
  const text = await response.text();
  try {
    return JSON.parse(text) as ApiBody;
  } catch {
    throw new Error("The server returned an unexpected response. The automation backend may be unreachable.");
  }
}

function isValidCanvaLink(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      ["canva.com", "www.canva.com", "canva.link"].includes(url.hostname)
    );
  } catch {
    return false;
  }
}

const SHEET_STORE = "canva.sheet.id";

function today() {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })
    .format(new Date())
    .replace(/ /g, "-");
}

function defaultNewEntry(): NewEntry {
  return {
    account: ACCOUNTS[0][1],
    task_type: TASK_TYPES[0],
    task_description: "",
    status: "Done",
    canva_link: "",
  };
}

function HomeContent() {
  const searchParams = useSearchParams();
  const { data: session, status } = useSession();
  const [date, setDate] = useState(today);
  const [rows, setRows] = useState<SheetRow[]>([]);
  const [newEntries, setNewEntries] = useState<NewEntry[]>([defaultNewEntry()]);
  const [canvaLinks, setCanvaLinks] = useState<Record<number, string>>({});
  const [dirtyRows, setDirtyRows] = useState<Set<number>>(new Set());
  const [job, setJob] = useState<Job | null>(null);
  const [loadingRows, setLoadingRows] = useState(false);
  const [adding, setAdding] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [downloadingRow, setDownloadingRow] = useState<number | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [automationOnline, setAutomationOnline] = useState<boolean | null>(null);
  const [sheetId, setSheetId] = useState("");

  const oauthError = searchParams.get("error") ?? "";
  const activeToken = session?.accessToken ?? "";
  const activeEmail = session?.user?.email ?? "";
  const sessionLoading = status === "loading";

  useEffect(() => {
    try {
      const stored = localStorage.getItem(SHEET_STORE);
      if (stored) setSheetId(stored);
    } catch {
      /* storage unavailable */
    }
  }, []);

  function updateSheetId(value: string) {
    setSheetId(value);
    try {
      if (value.trim()) localStorage.setItem(SHEET_STORE, value.trim());
      else localStorage.removeItem(SHEET_STORE);
    } catch {
      /* storage unavailable */
    }
  }

  useEffect(() => {
    if (window.location.search) window.history.replaceState({}, "", "/");
  }, [searchParams]);

  useEffect(() => {
    let mounted = true;
    fetch("/api/automation-health")
      .then((r) => readJson(r))
      .then((body) => mounted && setAutomationOnline(body.online === true))
      .catch(() => mounted && setAutomationOnline(false));
    return () => { mounted = false; };
  }, []);

  const fetchRows = useCallback(async () => {
    if (!activeToken || !date || !sheetId.trim()) return;
    setLoadingRows(true);
    setError("");
    try {
      const params = new URLSearchParams({ date, token: activeToken, sheet_id: sheetId.trim() });
      const response = await fetch(`/api/sheet-rows?${params}`);
      const body = await readJson(response);
      if (!response.ok) throw new Error(body.message || body.error || "Could not load rows.");
      setRows(body.rows || []);
      setDirtyRows(new Set());
      const links: Record<number, string> = {};
      for (const row of body.rows || []) {
        if (!row.post_link) links[row.row_number] = "";
      }
      setCanvaLinks((prev) => ({ ...links, ...prev }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load rows.");
    } finally {
      setLoadingRows(false);
    }
  }, [activeToken, date, sheetId]);

  useEffect(() => {
    if (!activeToken) return;
    fetchRows();
  }, [activeToken, fetchRows]);

  useEffect(() => {
    if (!job || ["done", "done_with_errors", "failed"].includes(job.state)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`/api/jobs/${job.job_id}`);
        if (response.ok) {
          const updated = (await readJson(response)) as unknown as Job;
          setJob(updated);
          if (["done", "done_with_errors", "failed"].includes(updated.state)) {
            fetchRows();
          }
        }
      } catch {
        /* transient poll failure; next tick retries */
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [job, fetchRows]);

  const dirtyCount = dirtyRows.size;
  const activeJobRows = job?.rows.filter((r) => r.state !== "done" && r.state !== "failed").length ?? 0;
  const finishedRows = job?.rows.filter((r) => r.state === "done").length ?? 0;

  function updateNewEntry(index: number, patch: Partial<NewEntry>) {
    setNewEntries((cur) => cur.map((e, i) => (i === index ? { ...e, ...patch } : e)));
  }

  function addNewEntry() {
    setNewEntries((cur) => [...cur, defaultNewEntry()]);
  }

  function removeNewEntry(index: number) {
    setNewEntries((cur) => (cur.length === 1 ? cur : cur.filter((_, i) => i !== index)));
  }

  function updateRow(rowNumber: number, field: keyof SheetRow, value: string) {
    setRows((cur) =>
      cur.map((r) => (r.row_number === rowNumber ? { ...r, [field]: value } : r)),
    );
    setDirtyRows((cur) => new Set(cur).add(rowNumber));
  }

  function updateCanvaLink(rowNumber: number, value: string) {
    setCanvaLinks((cur) => ({ ...cur, [rowNumber]: value }));
  }

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!activeToken.trim()) {
      setError("Connect Google first.");
      return;
    }
    if (!sheetId.trim()) {
      setError("Paste your Google Sheet ID first.");
      return;
    }
    const valid = newEntries.filter((e) => e.task_description.trim());
    if (!valid.length) {
      setError("Add a description for at least one task.");
      return;
    }
    const badLink = valid.find((e) => e.canva_link.trim() && !isValidCanvaLink(e.canva_link.trim()));
    if (badLink) {
      setError("Canva links must be https:// links on canva.com or canva.link.");
      return;
    }
    setAdding(true);
    try {
      const rowsPayload = valid.map((entry) => ({
        row_id: crypto.randomUUID(),
        account: entry.account,
        task_type: entry.task_type,
        task_description: entry.task_description.trim(),
        status: entry.status,
        canva_link: "",
      }));
      const response = await fetch("/api/sheet-rows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          google_access_token: activeToken.trim(),
          date,
          sheet_id: sheetId.trim(),
          rows: rowsPayload,
        }),
      });
      const body = await readJson(response);
      if (!response.ok) throw new Error(body.message || body.error || "Could not add rows.");
      setMessage(`Added ${body.rows_written} task${body.rows_written === 1 ? "" : "s"} to Sheet.`);

      const rowNumbers = body.row_numbers ?? {};
      const downloadRows = valid
        .map((entry, index) => ({ entry, rowNumber: rowNumbers[rowsPayload[index].row_id] }))
        .filter((item) => item.entry.canva_link.trim() && typeof item.rowNumber === "number")
        .map((item) => ({
          row_number: item.rowNumber as number,
          account: item.entry.account,
          task_type: item.entry.task_type,
          canva_link: item.entry.canva_link.trim(),
        }));

      if (downloadRows.length) {
        try {
          const dlResponse = await fetch("/api/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              google_access_token: activeToken.trim(),
              date,
              sheet_id: sheetId.trim(),
              rows: downloadRows,
            }),
          });
          const dlBody = await readJson(dlResponse);
          if (!dlResponse.ok) {
            throw new Error(dlBody.message || dlBody.error || "could not queue the downloads.");
          }
          setJob({ job_id: dlBody.job_id ?? "", state: dlBody.state ?? "queued", rows: [] });
          setMessage(
            `Added ${body.rows_written} task${body.rows_written === 1 ? "" : "s"} to Sheet. ` +
              `Queued ${downloadRows.length} download${downloadRows.length === 1 ? "" : "s"} — watch the Live queue.`,
          );
        } catch (dlErr) {
          setError(
            `Rows were added, but queuing downloads failed: ${
              dlErr instanceof Error ? dlErr.message : "unknown error"
            }. Paste the Canva links under Existing tasks to retry.`,
          );
        }
      }
      setNewEntries([defaultNewEntry()]);
      await fetchRows();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add rows.");
    } finally {
      setAdding(false);
    }
  }

  async function handleSync() {
    if (!dirtyCount || !activeToken.trim()) return;
    if (!sheetId.trim()) {
      setError("Paste your Google Sheet ID first.");
      return;
    }
    setError("");
    setMessage("");
    setSyncing(true);
    try {
      const updates = Array.from(dirtyRows).map((rowNumber) => {
        const row = rows.find((r) => r.row_number === rowNumber)!;
        return {
          row_number: rowNumber,
          account: row.account,
          task_type: row.task_type,
          task_description: row.task_description,
          status: row.status,
        };
      });
      const response = await fetch("/api/sheet-rows", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          google_access_token: activeToken.trim(),
          sheet_id: sheetId.trim(),
          updates,
        }),
      });
      const body = await readJson(response);
      if (!response.ok) throw new Error(body.message || body.error || "Could not sync.");
      setMessage(`Synced ${body.updated} row${body.updated === 1 ? "" : "s"} to Sheet.`);
      setDirtyRows(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sync.");
    } finally {
      setSyncing(false);
    }
  }

  async function handleDownload(rowNumber: number) {
    const row = rows.find((r) => r.row_number === rowNumber);
    const canvaLink = canvaLinks[rowNumber]?.trim();
    if (!row || !canvaLink || !activeToken.trim()) return;
    if (!sheetId.trim()) {
      setError("Paste your Google Sheet ID first.");
      return;
    }
    if (!isValidCanvaLink(canvaLink)) {
      setError("Canva links must be https:// links on canva.com or canva.link.");
      return;
    }
    setError("");
    setMessage("");
    setDownloadingRow(rowNumber);
    try {
      const response = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          google_access_token: activeToken.trim(),
          date,
          sheet_id: sheetId.trim(),
          rows: [
            {
              row_number: rowNumber,
              account: row.account,
              task_type: row.task_type,
              canva_link: canvaLink,
            },
          ],
        }),
      });
      const body = await readJson(response);
      if (!response.ok) throw new Error(body.message || body.error || "Could not queue download.");
      setJob({ job_id: body.job_id ?? "", state: body.state ?? "queued", rows: [] });
      setMessage(`Download queued for row ${rowNumber}. Watch the VNC screen.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue download.");
    } finally {
      setDownloadingRow(null);
    }
  }

  async function handleRevoke() {
    if (!activeToken) return;
    setError("");
    setMessage("");
    setRevoking(true);
    try {
      const response = await fetch("/api/auth/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: activeToken }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || "Could not revoke access.");
      }
      setRows([]);
      setDirtyRows(new Set());
      setCanvaLinks({});
      setJob(null);
      setMessage("Google access revoked. Connect again when ready.");
      await signOut({ callbackUrl: "/" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke access.");
    } finally {
      setRevoking(false);
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">CANVA → DRIVE / CONTROL DESK</p>
          <h1>Move the work,<br /><em>not the links.</em></h1>
          <p className="intro">
            Add tasks, sync to Sheet, then paste Canva links to download and deliver to Drive.
          </p>
        </div>
        <div className={`status-pill ${automationOnline ? "is-ready" : ""}`}>
          <span className="status-dot" />
          {automationOnline === null ? "Checking worker…" : automationOnline ? "Automation online" : "Automation offline"}
        </div>
      </header>

      <div className="rule" />

      <div className="workspace">
        <div className="card intake-card">
          <div className="card-heading">
            <div>
              <p className="section-label">New delivery</p>
              <h2>Add your tasks</h2>
            </div>
            <span className="step-mark">01 / INPUT</span>
          </div>

          <label className="date-field">
            Calendar date
            <input value={date} onChange={(e) => setDate(e.target.value)} placeholder="21-Oct-2026" required />
            <small>Tasks are added to and loaded from the Sheet for this date.</small>
          </label>

          <label className="date-field">
            Google Sheet ID
            <input
              value={sheetId}
              onChange={(e) => updateSheetId(e.target.value)}
              placeholder="ID from the sheet URL — between /d/ and /edit"
              spellCheck={false}
            />
            <small>
              Saved in this browser. Row 1 must contain: Date, Business/Account, Task Type,
              Task Description, Status, Post Link.
              {sheetId.trim() && (
                <>
                  {" "}
                  <a
                    href={`https://docs.google.com/spreadsheets/d/${sheetId.trim()}/edit`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open Sheet ↗
                  </a>
                </>
              )}
            </small>
          </label>

          <form onSubmit={handleAdd}>
            <div className="links-heading">
              <div>
                <p className="section-label">New task entries</p>
                <p className="muted">Fill in the details, then click &quot;Add to Sheet&quot;.</p>
              </div>
              <span className="count">{newEntries.length} {newEntries.length === 1 ? "task" : "tasks"}</span>
            </div>

            <div className="entry-list">
              {newEntries.map((entry, index) => (
                <div className="entry-row" key={index}>
                  <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
                  <div className="entry-fields">
                    <div className="entry-selects">
                      <label>Business
                        <select value={entry.account} onChange={(e) => updateNewEntry(index, { account: e.target.value })}>
                          {ACCOUNTS.map(([label, value]) => <option value={value} key={value}>{label}</option>)}
                        </select>
                      </label>
                      <label>Content type
                        <select value={entry.task_type} onChange={(e) => updateNewEntry(index, { task_type: e.target.value })}>
                          {TASK_TYPES.map((v) => <option value={v} key={v}>{v}</option>)}
                        </select>
                      </label>
                      <label>Status
                        <select value={entry.status} onChange={(e) => updateNewEntry(index, { status: e.target.value })}>
                          {STATUSES.map((v) => <option value={v} key={v}>{v}</option>)}
                        </select>
                      </label>
                    </div>
                    <input
                      value={entry.task_description}
                      onChange={(e) => updateNewEntry(index, { task_description: e.target.value })}
                      placeholder="Reel for Friday for 16-Oct-2026"
                      aria-label={`Task description ${index + 1}`}
                    />
                    <input
                      value={entry.canva_link}
                      onChange={(e) => updateNewEntry(index, { canva_link: e.target.value })}
                      placeholder="Canva link (optional — downloads automatically after adding)"
                      type="url"
                      spellCheck={false}
                      aria-label={`Canva link ${index + 1}`}
                    />
                  </div>
                  <button type="button" className="icon-button" onClick={() => removeNewEntry(index)} aria-label={`Remove task ${index + 1}`}>×</button>
                </div>
              ))}
            </div>
            <button type="button" className="add-link" onClick={addNewEntry}>＋ Add another task</button>

            <div className="submit-row">
              <div className="submit-note">
                <span className="lock-icon">↗</span>
                <span>
                  Rows are added to Sheet with an empty Post Link.<br />
                  Paste a Canva link on a task and it downloads automatically after adding —
                  the Drive link lands back in that row&apos;s Post Link.
                </span>
              </div>
              <button className="primary-button" disabled={adding}>
                {adding ? "Adding…" : "Add to Sheet"} <span>→</span>
              </button>
            </div>
          </form>
        </div>

        <aside className="side-column">
          <section className="card connect-card">
            <div className="card-heading compact">
              <div>
                <p className="section-label">Access</p>
                <h2>{activeToken ? "Google connected" : "Connect Google"}</h2>
              </div>
            </div>
            {sessionLoading ? (
              <p className="muted">Checking Google session…</p>
            ) : activeToken ? (
              <>
                <p className="connected-as">
                  Connected as <strong>{activeEmail || "your Google account"}</strong>
                </p>
                <p className="muted" style={{ marginTop: 8 }}>
                  Sheets and Drive access is active for this session.
                </p>
                <button
                  type="button"
                  className="google-button"
                  onClick={handleRevoke}
                  disabled={revoking}
                  style={{ marginTop: 16 }}
                >
                  {revoking ? "Revoking…" : "Revoke access"} <span>✕</span>
                </button>
              </>
            ) : (
              <>
                <p className="muted">Authorize Sheets and Drive to add, sync, and download tasks.</p>
                <button type="button" className="google-button" onClick={() => signIn("google")}>
                  Continue with Google <span>↗</span>
                </button>
              </>
            )}
            <p className={`worker-note ${automationOnline ? "online" : ""}`}>
              <span className="mini-dot" /> {automationOnline ? "Ubuntu worker is reachable." : automationOnline === false ? "Worker is offline. Nothing will be downloaded until it returns." : "Checking worker availability…"}
            </p>
          </section>

          <section className="card progress-card">
            <div className="card-heading compact">
              <div><p className="section-label">Live queue</p><h2>{job ? "Batch status" : "Nothing running"}</h2></div>
              <span className="step-mark">{job ? job.state.replaceAll("_", " ") : "READY"}</span>
            </div>
            {job ? (
              <>
                <div className="progress-summary"><strong>{finishedRows}/{job.rows.length}</strong><span>rows complete</span></div>
                <div className="progress-track"><span style={{ width: `${job.rows.length ? (finishedRows / job.rows.length) * 100 : 0}%` }} /></div>
                <div className="row-statuses">
                  {job.rows.map((r) => (
                    <div key={r.row_id}>
                      <span className={`mini-dot ${r.state}`} />
                      Row {r.sheet_row ?? "?"} — {r.file || r.state.replaceAll("_", " ")}
                    </div>
                  ))}
                </div>
              </>
            ) : <p className="empty-copy">Your next batch will appear here once a download is queued.</p>}
          </section>
        </aside>
      </div>

      {rows.length > 0 && (
        <>
          <div className="rule" />
          <section className="card" style={{ marginTop: "1.5rem" }}>
            <div className="card-heading">
              <div>
                <p className="section-label">Existing tasks</p>
                <h2>Rows for {date}</h2>
              </div>
              <div style={{ display: "flex", gap: "0.75rem", alignItems: "center" }}>
                {loadingRows && <span className="step-mark">LOADING…</span>}
                {dirtyCount > 0 && (
                  <button className="primary-button" onClick={handleSync} disabled={syncing} style={{ padding: "0.5rem 1rem", fontSize: "0.85rem" }}>
                    {syncing ? "Syncing…" : `Sync ${dirtyCount} change${dirtyCount === 1 ? "" : "s"}`}
                  </button>
                )}
                <span className="count">{rows.length} row{rows.length === 1 ? "" : "s"}</span>
              </div>
            </div>

            <div className="entry-list">
              {rows.map((row) => {
                const hasLink = !!row.post_link;
                const canvaLink = canvaLinks[row.row_number] ?? "";
                const isDownloading = downloadingRow === row.row_number;
                return (
                  <div className="entry-row" key={row.row_number}>
                    <span className="row-number">{String(row.row_number).padStart(2, "0")}</span>
                    <div className="entry-fields">
                      <div className="entry-selects">
                        <label>Business
                          <select value={row.account} onChange={(e) => updateRow(row.row_number, "account", e.target.value)}>
                            {ACCOUNTS.map(([label, value]) => <option value={value} key={value}>{label}</option>)}
                          </select>
                        </label>
                        <label>Content type
                          <select value={row.task_type} onChange={(e) => updateRow(row.row_number, "task_type", e.target.value)}>
                            {TASK_TYPES.map((v) => <option value={v} key={v}>{v}</option>)}
                          </select>
                        </label>
                        <label>Status
                          <select value={row.status} onChange={(e) => updateRow(row.row_number, "status", e.target.value)}>
                            {STATUSES.map((v) => <option value={v} key={v}>{v}</option>)}
                          </select>
                        </label>
                      </div>
                      <input
                        value={row.task_description}
                        onChange={(e) => updateRow(row.row_number, "task_description", e.target.value)}
                        placeholder="Task description"
                        aria-label={`Task description for row ${row.row_number}`}
                      />
                      {hasLink ? (
                        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem 0" }}>
                          <span className="mini-dot done" />
                          <span style={{ fontSize: "0.85rem", color: "var(--ok, #22c55e)" }}>Delivered</span>
                          <a href={row.post_link} target="_blank" rel="noreferrer" style={{ fontSize: "0.8rem", opacity: 0.8 }}>View on Drive ↗</a>
                        </div>
                      ) : (
                        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                          <input
                            value={canvaLink}
                            onChange={(e) => updateCanvaLink(row.row_number, e.target.value)}
                            placeholder="Paste Canva link to download…"
                            type="url"
                            style={{ flex: 1 }}
                            aria-label={`Canva link for row ${row.row_number}`}
                          />
                          <button
                            type="button"
                            className="primary-button"
                            disabled={!canvaLink.trim() || isDownloading || activeJobRows > 0}
                            onClick={() => handleDownload(row.row_number)}
                            style={{ padding: "0.5rem 1rem", fontSize: "0.85rem", whiteSpace: "nowrap" }}
                          >
                            {isDownloading ? "Queuing…" : "Download"}
                          </button>
                        </div>
                      )}
                      {dirtyRows.has(row.row_number) && (
                        <small style={{ opacity: 0.6 }}>Edited — click Sync to save</small>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        </>
      )}

      {(message || error || oauthError) && (
        <div className={`notice ${error || oauthError ? "notice-error" : ""}`} role="status">
          {error || (oauthError ? `Google sign-in failed: ${oauthError}` : message)}
        </div>
      )}
      <footer><span>Events / Canva automation</span><span>One batch at a time · Ubuntu worker</span></footer>
    </main>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<main className="shell"><p className="eyebrow">CANVA → DRIVE / CONTROL DESK</p><h1>Loading desk…</h1></main>}>
      <HomeContent />
    </Suspense>
  );
}
