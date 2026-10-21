import { NextResponse } from "next/server";

export async function POST(request: Request) {
  let token = "";
  try {
    const body = await request.json();
    token = typeof body.token === "string" ? body.token.trim() : "";
  } catch {
    return NextResponse.json({ error: "Invalid request body." }, { status: 400 });
  }
  if (!token) {
    return NextResponse.json({ error: "token is required." }, { status: 400 });
  }

  try {
    const response = await fetch("https://oauth2.googleapis.com/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token }),
      cache: "no-store",
    });
    // Google returns 400 invalid_token for already-revoked tokens — treat as success.
    if (!response.ok && response.status !== 400) {
      return NextResponse.json({ error: "Google rejected the revocation." }, { status: 502 });
    }
    return NextResponse.json({ revoked: true });
  } catch {
    return NextResponse.json({ error: "Could not reach Google." }, { status: 502 });
  }
}
