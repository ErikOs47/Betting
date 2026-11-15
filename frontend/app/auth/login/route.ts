import { NextResponse, type NextRequest } from "next/server";
import { COOKIE, MAX_AGE_S, loginEnabled, passwordMatches, sessionToken } from "@/lib/session";

export const dynamic = "force-dynamic";

// brute-force brake: at most 10 failed attempts per client IP per 15 minutes
const failures = new Map<string, number[]>();
const WINDOW_MS = 15 * 60 * 1000;

export async function POST(req: NextRequest) {
  const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || "local";
  const now = Date.now();
  const recent = (failures.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  if (recent.length >= 10) return NextResponse.json({ detail: "Zu viele Fehlversuche. Bitte 15 Minuten warten." }, { status: 429 });
  const { password } = await req.json().catch(() => ({ password: "" }));
  if (typeof password !== "string" || !passwordMatches(password)) {
    failures.set(ip, [...recent, now]);
    return NextResponse.json({ detail: "Falsches Passwort" }, { status: 401 });
  }
  failures.delete(ip);
  const res = NextResponse.json({ ok: true });
  const https = req.nextUrl.protocol === "https:" || req.headers.get("x-forwarded-proto") === "https";
  res.cookies.set(COOKIE, sessionToken(), { httpOnly: true, sameSite: "lax", secure: https, path: "/", maxAge: MAX_AGE_S });
  return res;
}

export function GET() {
  return NextResponse.json({ enabled: loginEnabled() });
}
