import { NextResponse, type NextRequest } from "next/server";
import { COOKIE, loginEnabled, validSession } from "@/lib/session";

/** Optional password protection: active only when NBI_APP_PASSWORD is set on the server. */
export function proxy(req: NextRequest) {
  if (!loginEnabled() || validSession(req.cookies.get(COOKIE)?.value)) return NextResponse.next();
  if (req.nextUrl.pathname.startsWith("/api/")) return NextResponse.json({ detail: "Nicht angemeldet" }, { status: 401 });
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = req.nextUrl.pathname === "/" ? "" : `?next=${encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  // everything except the login itself, static assets, the PWA files and icons
  matcher: ["/((?!login|auth/|_next/static|_next/image|icons/|manifest.webmanifest|sw.js|offline.html|favicon.ico).*)"],
};
