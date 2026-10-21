import { NextResponse } from "next/server";

export function GET(request: Request) {
  const url = new URL(request.url);
  const clientId = process.env.GOOGLE_CLIENT_ID;
  if (!clientId) {
    return NextResponse.json({ error: "GOOGLE_CLIENT_ID is not configured." }, { status: 500 });
  }
  const callback = new URL("/api/auth/callback/google", url.origin);
  const auth = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  auth.searchParams.set("client_id", clientId);
  auth.searchParams.set("redirect_uri", callback.toString());
  auth.searchParams.set("response_type", "code");
  auth.searchParams.set("access_type", "offline");
  auth.searchParams.set("prompt", "consent");
  auth.searchParams.set("scope", [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
  ].join(" "));
  return NextResponse.redirect(auth);
}
