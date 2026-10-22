import { NextResponse } from "next/server";

function backendConfigured() {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  return backend && secret ? { backend: backend.replace(/\/$/, ""), secret } : null;
}

async function proxyJson(
  target: string | URL,
  init: RequestInit | undefined,
  secret: string,
) {
  try {
    const response = await fetch(target, {
      ...init,
      headers: { ...(init?.headers as Record<string, string> | undefined), "X-Automation-Secret": secret },
      cache: "no-store",
      signal: AbortSignal.timeout(30000),
    });
    const text = await response.text();
    try {
      return NextResponse.json(text ? JSON.parse(text) : {}, { status: response.status });
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

export async function GET(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const url = new URL(request.url);
  const date = url.searchParams.get("date");
  const token = url.searchParams.get("token");
  const sheetId = url.searchParams.get("sheet_id");
  if (!date || !token) {
    return NextResponse.json({ error: "date and token are required." }, { status: 400 });
  }
  const target = new URL("/api/sheet-rows", config.backend);
  target.searchParams.set("date", date);
  target.searchParams.set("token", token);
  if (sheetId) target.searchParams.set("sheet_id", sheetId);
  return proxyJson(target, undefined, config.secret);
}

export async function POST(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  return proxyJson(new URL("/api/sheet-rows", config.backend), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: text,
  }, config.secret);
}

export async function PATCH(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  return proxyJson(new URL("/api/sheet-rows", config.backend), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: text,
  }, config.secret);
}
