import { NextResponse } from "next/server";

export async function POST(request: Request) {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  if (!backend || !secret) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  const response = await fetch(new URL("/api/download", backend.replace(/\/$/, "")), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Automation-Secret": secret },
    body: text,
    cache: "no-store",
  });
  const body = await response.json();
  return NextResponse.json(body, { status: response.status });
}
