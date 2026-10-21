import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  if (!backend || !secret) return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  const response = await fetch(`${backend.replace(/\/$/, "")}/api/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Automation-Secret": secret },
    body: await request.text(),
    cache: "no-store",
  });
  return NextResponse.json(await response.json(), { status: response.status });
}
