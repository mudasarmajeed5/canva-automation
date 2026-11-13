import { NextResponse } from "next/server";

export async function POST(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  if (!backend || !secret) return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  const { jobId } = await context.params;
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Automation-Secret": secret },
      body: await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
    const text = await response.text();
    try {
      return NextResponse.json(text ? JSON.parse(text) : {}, { status: response.status });
    } catch {
      return NextResponse.json({ error: "automation_backend_bad_response", message: "Could not read the retry response. Refresh the job status before retrying again." }, { status: 502 });
    }
  } catch {
    return NextResponse.json({ error: "automation_backend_unreachable", message: "Could not reach the worker. Refresh the job status before retrying again." }, { status: 502 });
  }
}
