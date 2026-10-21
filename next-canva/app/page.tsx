"use client";

import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

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

type Job = {
  job_id: string;
  state: string;
  rows: Array<{
    row_id: string;
    state: string;
    file: string | null;
    drive_url: string | null;
    error: string | null;
  }>;
};

type Entry = {
  account: string;
  taskType: (typeof TASK_TYPES)[number];
  status: (typeof STATUSES)[number];
  description: string;
  link: string;
};

function today() {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  })
    .format(new Date())
    .replace(/ /g, "-");
}

function HomeContent() {
  const searchParams = useSearchParams();
  const [date, setDate] = useState(today);
  const [entries, setEntries] = useState<Entry[]>([{
    account: ACCOUNTS[0][1],
    taskType: TASK_TYPES[0],
    status: "Done",
    description: "",
    link: "",
  }]);
  const [token, setToken] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [automationOnline, setAutomationOnline] = useState<boolean | null>(null);
  const oauthToken = searchParams.get("token") ?? "";
  const oauthEmail = searchParams.get("email") ?? "";
  const oauthError = searchParams.get("error") ?? "";
  const activeToken = token || oauthToken;
  const activeEmail = oauthEmail;
  const initialMessage = activeToken && !message && !oauthError
    ? "Google is connected. The access token is ready for this session."
    : message;

  useEffect(() => {
    if (window.location.search) window.history.replaceState({}, "", "/");
  }, []);

  useEffect(() => {
    let mounted = true;
    fetch("/api/automation-health")
      .then((response) => response.json())
      .then((body) => mounted && setAutomationOnline(body.online === true))
      .catch(() => mounted && setAutomationOnline(false));
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!job || ["done", "done_with_errors", "failed"].includes(job.state)) {
      return;
    }
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/jobs/${job.job_id}`);
      if (response.ok) setJob(await response.json());
    }, 2500);
    return () => window.clearInterval(timer);
  }, [job]);

  const completedEntries = useMemo(
    () => entries.filter((entry) => entry.link.trim()),
    [entries],
  );
  const finishedRows = job?.rows.filter((row) => row.state === "done").length ?? 0;

  function updateEntry(index: number, patch: Partial<Entry>) {
    setEntries((current) => current.map((entry, i) => (
      i === index ? { ...entry, ...patch } : entry
    )));
  }

  function addEntry() {
    const previous = entries[entries.length - 1];
    setEntries((current) => [...current, {
      account: ACCOUNTS[0][1],
      taskType: previous.taskType,
      status: previous.status,
      description: previous.description,
      link: "",
    }]);
  }

  function removeEntry(index: number) {
    setEntries((current) =>
      current.length === 1 ? current : current.filter((_, i) => i !== index),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!activeToken.trim()) {
      setError("Connect Google first, or paste a Google access token below.");
      return;
    }

    if (!completedEntries.length) {
      setError("Add at least one task with a Canva link.");
      return;
    }
    if (completedEntries.some((entry) => !entry.description.trim())) {
      setError("Complete the description for every task that has a Canva link.");
      return;
    }
    if (automationOnline === false) {
      setError("The automation server is offline. Nothing was written. Try again later.");
      return;
    }

    setSubmitting(true);
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_type: "CANVA_AUTOMATION",
          date,
          google_access_token: activeToken.trim(),
          requested_by: activeEmail.trim() || undefined,
          rows: completedEntries.map((entry) => ({
            row_id: crypto.randomUUID(),
            account: entry.account,
            task_type: entry.taskType,
            task_description: entry.description.trim(),
            status: entry.status,
            canva_link: entry.link.trim(),
          })),
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || body.error || "Could not queue the job.");
      setJob(body);
      setMessage(`Queued ${body.rows_written} design${body.rows_written === 1 ? "" : "s"}.`);
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Could not queue the job.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">CANVA → DRIVE / CONTROL DESK</p>
          <h1>Move the work,<br /><em>not the links.</em></h1>
          <p className="intro">
            Queue finished designs for download, Drive delivery, and a clean calendar update.
          </p>
        </div>
        <div className={`status-pill ${automationOnline ? "is-ready" : ""}`}>
          <span className="status-dot" />
          {automationOnline === null ? "Checking worker…" : automationOnline ? "Automation online" : "Automation offline"}
        </div>
      </header>

      <div className="rule" />

      <section className="workspace">
        <form className="card intake-card" onSubmit={submit}>
          <div className="card-heading">
            <div>
              <p className="section-label">New delivery</p>
              <h2>Add your tasks</h2>
            </div>
            <span className="step-mark">01 / INPUT</span>
          </div>

          <label className="date-field">
            Calendar date
            <input value={date} onChange={(event) => setDate(event.target.value)} placeholder="21-Oct-2026" required />
            <small>This date applies to every task below.</small>
          </label>

          <div className="links-heading">
            <div>
              <p className="section-label">Task entries</p>
              <p className="muted">Each row is one Sheet entry. Add the Canva link when that design is ready.</p>
            </div>
            <span className="count">{entries.length} {entries.length === 1 ? "task" : "tasks"}</span>
          </div>

          <div className="entry-list">
            {entries.map((entry, index) => (
              <div className="entry-row" key={index}>
                <span className="row-number">{String(index + 1).padStart(2, "0")}</span>
                <div className="entry-fields">
                  <div className="entry-selects">
                    <label>Business
                      <select value={entry.account} onChange={(event) => updateEntry(index, { account: event.target.value })}>
                        {ACCOUNTS.map(([label, value]) => <option value={value} key={value}>{label}</option>)}
                      </select>
                    </label>
                    <label>Content type
                      <select value={entry.taskType} onChange={(event) => updateEntry(index, { taskType: event.target.value as Entry["taskType"] })}>
                        {TASK_TYPES.map((value) => <option value={value} key={value}>{value}</option>)}
                      </select>
                    </label>
                    <label>Status
                      <select value={entry.status} onChange={(event) => updateEntry(index, { status: event.target.value as Entry["status"] })}>
                        {STATUSES.map((value) => <option value={value} key={value}>{value}</option>)}
                      </select>
                    </label>
                  </div>
                  <input value={entry.description} onChange={(event) => updateEntry(index, { description: event.target.value })} placeholder="Reel for Friday for 16-Oct-2026" aria-label={`Task description ${index + 1}`} />
                  <input value={entry.link} onChange={(event) => updateEntry(index, { link: event.target.value })} placeholder="Paste Canva link when ready…" type="url" aria-label={`Canva link ${index + 1}`} />
                </div>
                <button type="button" className="icon-button" onClick={() => removeEntry(index)} aria-label={`Remove task ${index + 1}`}>×</button>
              </div>
            ))}
          </div>
          <button type="button" className="add-link" onClick={addEntry}>＋ Add another task</button>

          <div className="submit-row">
            <div className="submit-note">
              <span className="lock-icon">↗</span>
              <span>Links are written to Sheets first.<br />Drive replaces them after verification.</span>
            </div>
            <button className="primary-button" disabled={submitting}>
              {submitting ? "Queuing…" : "Upload ready tasks"} <span>→</span>
            </button>
          </div>
        </form>

        <aside className="side-column">
          <section className="card connect-card">
            <div className="card-heading compact">
              <div><p className="section-label">Access</p><h2>Connect Google</h2></div>
              <span className="google-g">G</span>
            </div>
            <p className="muted">Authorize Sheets and Drive, then use the token for this session only.</p>
            <a className="google-button" href="/api/auth/google">Continue with Google <span>↗</span></a>
            <label className="token-label">
              Access token
              <textarea value={token} onChange={(event) => setToken(event.target.value)} placeholder="Paste token here if you already have one" rows={3} spellCheck={false} />
            </label>
            {activeEmail && <p className="connected-as">Connected as <strong>{activeEmail}</strong></p>}
            <p className={`worker-note ${automationOnline ? "online" : ""}`}>
              <span className="mini-dot" /> {automationOnline ? "Ubuntu worker is reachable." : automationOnline === false ? "Worker is offline. Nothing will be written until it returns." : "Checking worker availability…"}
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
                <div className="row-statuses">{job.rows.map((row) => <div key={row.row_id}><span className={`mini-dot ${row.state}`} />{row.file || row.state.replaceAll("_", " ")}</div>)}</div>
              </>
            ) : <p className="empty-copy">Your next batch will appear here once it is queued.</p>}
          </section>
        </aside>
      </section>

      {(initialMessage || error || oauthError) && <div className={`notice ${error || oauthError ? "notice-error" : ""}`} role="status">{error || (oauthError ? `Google sign-in failed: ${oauthError}` : initialMessage)}</div>}
      <footer><span>Events / Canva automation</span><span>One batch at a time · Ubuntu worker</span></footer>
    </main>
  );
}

export default function Home() {
  return <Suspense fallback={<main className="shell"><p className="eyebrow">CANVA → DRIVE / CONTROL DESK</p><h1>Loading desk…</h1></main>}><HomeContent /></Suspense>;
}
