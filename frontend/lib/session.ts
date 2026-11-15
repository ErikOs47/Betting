// Server-only helpers for the optional password login (NBI_APP_PASSWORD). No secret ever reaches the browser:
// the cookie holds an HMAC derived from the server secret, not the password.
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const COOKIE = "nbi_session";
export const MAX_AGE_S = 90 * 24 * 3600;

export const loginEnabled = () => Boolean(process.env.NBI_APP_PASSWORD);

function secret(): string {
  return process.env.NBI_SESSION_SECRET || `pw:${process.env.NBI_APP_PASSWORD ?? ""}`;
}

/** Changing the password or the session secret invalidates every existing session. */
export function sessionToken(): string {
  return createHmac("sha256", secret()).update("nbi-session-v1").digest("base64url");
}

function same(a: string, b: string): boolean {
  const ha = createHash("sha256").update(a).digest(), hb = createHash("sha256").update(b).digest();
  return timingSafeEqual(ha, hb);
}

export const validSession = (cookie: string | undefined) => Boolean(cookie) && same(cookie!, sessionToken());
export const passwordMatches = (pw: string) => loginEnabled() && same(pw, process.env.NBI_APP_PASSWORD!);
