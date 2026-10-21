import { NextResponse } from "next/server";

function backendConfigured() {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  return backend && secret ? { backend: backend.replace(/\/$/, ""), secret } : null;
}

export async function GET(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const url = new URL(request.url);
  const date = url.searchParams.get("date");
  const token = url.searchParams.get("token");
  if (!date || !token) {
    return NextResponse.json({ error: "date and token are required." }, { status: 400 });
  }
  const target = new URL("/api/sheet-rows", config.backend);
  target.searchParams.set("date", date);
  target.searchParams.set("token", token);
  const response = await fetch(target, {
    headers: { "X-Automation-Secret": config.secret },
    cache: "no-store",
  });
  const body = await response.json();
  return NextResponse.json(body, { status: response.status });
}

export async function POST(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  const response = await fetch(new URL("/api/sheet-rows", config.backend), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Automation-Secret": config.secret },
    body: text,
    cache: "no-store",
  });
  const body = await response.json();
  return NextResponse.json(body, { status: response.status });
}

export async function PATCH(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  const response = await fetch(new URL("/api/sheet-rows", config.backend), {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "X-Automation-Secret": config.secret },
    body: text,
    cache: "no-store",
  });
  const body = await response.json();
  return NextResponse.json(body, { status: response.status });
}
