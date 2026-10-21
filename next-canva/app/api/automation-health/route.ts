import { NextResponse } from "next/server";

export async function GET() {
  const backend = process.env.AUTOMATION_BACKEND_URL;
  if (!backend) return NextResponse.json({ online: false, reason: "backend_not_configured" }, { status: 200 });
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json({ online: response.ok });
  } catch {
    return NextResponse.json({ online: false });
  }
}
