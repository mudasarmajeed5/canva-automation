import { NextResponse } from "next/server";

function backendConfigured() {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  const secret = process.env.AUTOMATION_SECRET;
  return backend && secret ? { backend: backend.replace(/\/$/, ""), secret } : null;
}

async function proxy(target: string | URL, init: RequestInit | undefined, secret: string) {
  try {
    const response = await fetch(target, {
      ...init,
      headers: {
        ...(init?.headers as Record<string, string> | undefined),
        "X-Automation-Secret": secret,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(15000),
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
  const email = new URL(request.url).searchParams.get("email");
  if (!email) {
    return NextResponse.json({ error: "email is required." }, { status: 400 });
  }
  const target = new URL("/api/settings", config.backend);
  target.searchParams.set("email", email);
  return proxy(target, undefined, config.secret);
}

export async function PUT(request: Request) {
  const config = backendConfigured();
  if (!config) {
    return NextResponse.json({ error: "Automation backend is not configured." }, { status: 500 });
  }
  const text = await request.text();
  return proxy(new URL("/api/settings", config.backend), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: text,
  }, config.secret);
}
