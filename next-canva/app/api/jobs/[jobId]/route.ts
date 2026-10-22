import { NextResponse } from "next/server";

export async function GET(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  if (!backend || !secret) return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  const { jobId } = await context.params;
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/jobs/${encodeURIComponent(jobId)}`, {
      headers: { "X-Automation-Secret": secret },
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
    });
    const bodyText = await response.text();
    try {
      return NextResponse.json(bodyText ? JSON.parse(bodyText) : {}, { status: response.status });
    } catch {
      return NextResponse.json(
        { error: "automation_backend_bad_response", message: "Automation backend returned an unexpected response." },
        { status: 502 },
      );
    }
  } catch {
    return NextResponse.json(
      { error: "automation_backend_unreachable", message: "Automation backend is unreachable." },
      { status: 502 },
    );
  }
}
