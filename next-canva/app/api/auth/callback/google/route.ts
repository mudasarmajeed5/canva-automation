import { NextResponse } from "next/server";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const error = url.searchParams.get("error");
  if (error || !code) {
    return NextResponse.redirect(new URL(`/?error=${encodeURIComponent(error || "missing authorization code")}`, url.origin));
  }
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    return NextResponse.json({ error: "Google OAuth is not configured." }, { status: 500 });
  }
  const callback = new URL("/api/auth/callback/google", url.origin);
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: callback.toString(),
      grant_type: "authorization_code",
    }),
  });
  const body = await response.json();
  if (!response.ok || !body.access_token) {
    return NextResponse.redirect(new URL(`/?error=${encodeURIComponent(body.error_description || "token exchange failed")}`, url.origin));
  }
  let email = "";
  if (body.id_token) {
    try {
      email = JSON.parse(Buffer.from(body.id_token.split(".")[1], "base64url").toString()).email || "";
    } catch {
      email = "";
    }
  }
  const destination = new URL("/", url.origin);
  destination.searchParams.set("token", body.access_token);
  if (email) destination.searchParams.set("email", email);
  return NextResponse.redirect(destination);
}
