import { NextResponse } from "next/server";

export async function GET(request: Request, context: { params: Promise<{ jobId: string }> }) {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  if (!backend || !secret) return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  const { jobId } = await context.params;
  const response = await fetch(`${backend.replace(/\/$/, "")}/api/jobs/${encodeURIComponent(jobId)}`, {
    headers: { "X-Automation-Secret": secret },
    cache: "no-store",
  });
  return NextResponse.json(await response.json(), { status: response.status });
}
