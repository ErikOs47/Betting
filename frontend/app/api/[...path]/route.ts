// Server-side proxy to the FastAPI backend. The optional API token and all provider keys live only on the server.
import type { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";
export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${BACKEND}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (process.env.NBI_API_TOKEN) headers.authorization = `Bearer ${process.env.NBI_API_TOKEN}`;
  try {
    const res = await fetch(url, {
      method: req.method, headers, cache: "no-store",
      body: req.method === "GET" ? undefined : await req.text(),
    });
    return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json" } });
  } catch {
    return Response.json({ detail: "Backend nicht erreichbar" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT };
