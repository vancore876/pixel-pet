import { createClient } from "npm:@supabase/supabase-js@2.102.0";

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const oldAuthUrl = (Deno.env.get("JEFFERY_AUTH_URL") ?? "").replace(/\/$/, "");
const oldAuthKey = Deno.env.get("JEFFERY_AUTH_ANON_KEY") ?? "";
const oldApiUrl = (Deno.env.get("JEFFERY_API_URL") ?? "").replace(/\/$/, "");
const bridgeSecret = Deno.env.get("JEFFERY_AUTOMATE_BRIDGE_SECRET") ?? "";
const allowedOrigins = new Set((Deno.env.get("JEFFERY_SITE_ORIGINS") ?? "https://jeffery-vancore876.netlify.app")
  .split(",").map(value => value.trim()).filter(Boolean));

if (!supabaseUrl || !serviceKey) throw new Error("The AutoMate bridge Supabase environment is incomplete.");
const admin = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } });

const OPERATIONS = new Set([
  "get_inventory_item", "get_order", "search_orders", "get_invoice", "search_receivals",
]);
const WRITE_OPERATIONS = new Set<string>();
const forbiddenKey = /password|passwd|pin|hash|secret|token|credential|fingerprint|smtp/i;
const maxPayloadBytes = 24_000;

function reply(value: unknown, status = 200, origin = "") {
  const headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "Vary": "Origin" };
  if (origin && allowedOrigins.has(origin)) headers["Access-Control-Allow-Origin"] = origin;
  headers["Access-Control-Allow-Headers"] = "authorization, apikey, content-type, x-bridge-id, x-bridge-timestamp, x-bridge-signature";
  headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS";
  return new Response(JSON.stringify(value), { status, headers });
}
function fail(message: string, status = 400, origin = "") { return reply({ detail: message }, status, origin); }
function uuid(value: string) { return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value); }

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, child]) => forbiddenKey.test(key) || containsForbiddenKey(child));
}

function scrubResult(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(scrubResult);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !forbiddenKey.test(key))
    .map(([key, child]) => [key, scrubResult(child)]));
}

async function jefferyAccount(request: Request) {
  if (!oldAuthUrl || !oldAuthKey || !oldApiUrl) return null;
  const token = request.headers.get("Authorization")?.replace(/^Bearer\s+/i, "") ?? "";
  if (!token) return null;
  const auth = await fetch(`${oldAuthUrl}/auth/v1/user`, { headers: { apikey: oldAuthKey, Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!auth.ok) return null;
  const accountResponse = await fetch(`${oldApiUrl}/me`, { headers: { apikey: oldAuthKey, Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!accountResponse.ok) return null;
  const account = await accountResponse.json();
  if (!Number.isSafeInteger(account?.id) || typeof account?.username !== "string") return null;
  return { id: account.id as number, username: account.username as string };
}

function base64ToBytes(value: string): Uint8Array | null {
  try { return Uint8Array.from(atob(value), character => character.charCodeAt(0)); } catch { return null; }
}
async function signedBridgeBody(request: Request, rawBody: string) {
  if (!bridgeSecret) return null;
  const bridgeId = request.headers.get("x-bridge-id") ?? "";
  const timestamp = request.headers.get("x-bridge-timestamp") ?? "";
  const signature = base64ToBytes(request.headers.get("x-bridge-signature") ?? "");
  const seconds = Number(timestamp);
  if (!/^[A-Za-z0-9._-]{3,64}$/.test(bridgeId) || !Number.isSafeInteger(seconds)
      || Math.abs(Date.now() / 1000 - seconds) > 60 || !signature) return null;
  const path = new URL(request.url).pathname;
  const message = `${timestamp}\n${request.method}\n${path}\n${rawBody}`;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(bridgeSecret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const valid = await crypto.subtle.verify("HMAC", key, signature, new TextEncoder().encode(message));
  return valid ? bridgeId : null;
}

async function bridgeRoute(request: Request, url: URL, origin: string) {
  const raw = await request.text();
  if (raw.length > maxPayloadBytes) return fail("Bridge request is too large.", 413, origin);
  const bridgeId = await signedBridgeBody(request, raw);
  if (!bridgeId) return fail("The AutoMate bridge is not authorized.", 401, origin);
  let body: any = {};
  try { body = raw ? JSON.parse(raw) : {}; } catch { return fail("Bridge request is invalid.", 400, origin); }
  if (url.pathname.endsWith("/bridge/claim") && request.method === "POST") {
    const { data, error } = await admin.rpc("jeffery_automate_claim_jobs", { p_bridge_id: bridgeId, p_limit: body.limit ?? 5 });
    if (error) return fail("The bridge queue is unavailable.", 503, origin);
    return reply({ jobs: data ?? [] }, 200, origin);
  }
  const match = url.pathname.match(/\/bridge\/jobs\/([0-9a-f-]{36})\/complete$/i);
  if (match && request.method === "POST" && uuid(match[1])) {
    if (!body || typeof body.success !== "boolean" || (body.result != null && containsForbiddenKey(body.result))) return fail("The bridge result contains invalid or restricted fields.", 422, origin);
    const result = body.result == null ? null : scrubResult(body.result);
    if (result && JSON.stringify(result).length > maxPayloadBytes) return fail("The bridge result is too large.", 413, origin);
    const { data, error } = await admin.from("jeffery_automate_jobs").update({
      status: body.success ? "completed" : "failed",
      result: body.success ? result : null,
      error_code: body.success ? null : String(body.error_code ?? "operation_failed").slice(0, 64),
      completed_at: new Date().toISOString(),
    }).eq("id", match[1]).eq("status", "claimed").eq("bridge_id", bridgeId).select("id").maybeSingle();
    if (error) return fail("Could not save the AutoMate result.", 503, origin);
    if (!data) return fail("This bridge job expired or is no longer assigned to this connector.", 409, origin);
    return reply({ saved: true }, 200, origin);
  }
  return fail("Bridge route not found.", 404, origin);
}

async function webRoute(request: Request, url: URL, origin: string) {
  const account = await jefferyAccount(request);
  if (!account) return fail("Sign in to Jeffery before connecting to AutoMate.", 401, origin);
  if (url.pathname.endsWith("/jobs") && request.method === "POST") {
    let body: any;
    try { body = await request.json(); } catch { return fail("Enter a valid AutoMate request.", 400, origin); }
    const operation = typeof body?.operation === "string" ? body.operation : "";
    const payload = body?.payload ?? {};
    if (!OPERATIONS.has(operation) || !payload || typeof payload !== "object" || Array.isArray(payload)
        || containsForbiddenKey(payload) || JSON.stringify(payload).length > maxPayloadBytes) return fail("That AutoMate request contains unsupported or restricted fields.", 422, origin);
    if (WRITE_OPERATIONS.has(operation) && (account.role !== "admin" || body?.confirmed !== true)) return fail("An administrator must confirm this AutoMate change.", 403, origin);
    const { data, error } = await admin.from("jeffery_automate_jobs").insert({
      actor_account_id: account.id, actor_username: account.username, operation, payload,
    }).select("id,status,created_at,expires_at").single();
    if (error || !data) return fail("Could not queue this AutoMate request.", 503, origin);
    return reply({ job: data }, 201, origin);
  }
  const match = url.pathname.match(/\/jobs\/([0-9a-f-]{36})$/i);
  if (match && request.method === "GET" && uuid(match[1])) {
    const query = admin.from("jeffery_automate_jobs").select("id,operation,status,result,error_code,created_at,completed_at,expires_at").eq("id", match[1]);
    if (account.role !== "admin") query.eq("actor_account_id", account.id);
    const { data, error } = await query.maybeSingle();
    if (error) return fail("Could not load the AutoMate request.", 503, origin);
    return data ? reply({ job: { ...data, result: scrubResult(data.result) } }, 200, origin) : fail("AutoMate request not found.", 404, origin);
  }
  return fail("AutoMate route not found.", 404, origin);
}

Deno.serve(async request => {
  const origin = request.headers.get("Origin") ?? "";
  if (origin && !allowedOrigins.has(origin)) return fail("This website origin is not allowed.", 403);
  if (request.method === "OPTIONS") {
    const response = reply({}, 200, origin);
    return new Response(null, { status: 204, headers: response.headers });
  }
  const url = new URL(request.url);
  try {
    if (url.pathname.endsWith("/bridge/claim") || /\/bridge\/jobs\//.test(url.pathname)) return await bridgeRoute(request, url, origin);
    if (url.pathname.endsWith("/health") && request.method === "GET") return reply({ status: "ok", backend: "Jeffery AutoMate Bridge" }, 200, origin);
    return await webRoute(request, url, origin);
  } catch (error) {
    console.error("AutoMate bridge request failed", error instanceof Error ? error.message : "unknown error");
    return fail("Jeffery could not complete the AutoMate request.", 500, origin);
  }
});
