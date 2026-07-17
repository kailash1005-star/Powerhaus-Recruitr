import { NextRequest, NextResponse } from 'next/server';
import { auth0 } from '@/lib/auth0';

/**
 * Authenticated proxy to the Recruitr API.
 *
 * This is the hinge of the BFF model. The browser calls same-origin
 * `/api/proxy/...`; this handler reads the access token out of the encrypted
 * session cookie and forwards to Cloud Run with an Authorization header. The
 * token never reaches the browser, so XSS cannot steal it.
 *
 *   browser ──/api/proxy/api/v1/runs──▶ here ──Bearer──▶ https://…run.app/api/v1/runs
 *
 * The path is forwarded VERBATIM after /api/proxy. That keeps every existing call
 * site untouched — the only change in lib/api.ts is the base string — which is a
 * far smaller blast radius than rewriting ~100 URLs by hand.
 *
 * The SDK refreshes an expired access token transparently inside
 * getAccessToken(), which is what makes the session survive without the user
 * noticing — the "auth renewal" half of the requirement.
 *
 * A quiet bonus of doing it this way: EventSource (used by the run-progress SSE
 * stream) and plain <a> downloads cannot send an Authorization header at all.
 * Under BFF they just work, because the browser sends the session cookie. Under
 * the SPA model both would have needed the token smuggled into a query string —
 * where it lands in server logs.
 */

const API_BASE_URL = process.env.API_BASE_URL;

/** Hop-by-hop and identity headers we must not forward.
 *
 * `authorization` is the important one: we set it ourselves from the session. If
 * a caller's own Authorization header were forwarded, the browser could choose
 * the token the backend sees — which would hand back exactly the attack the BFF
 * model exists to prevent.
 *
 * `host`/`connection`/`content-length` are recomputed by fetch; forwarding them
 * produces subtle, hard-to-debug transport errors.
 * `cookie` must never leave our origin — it carries the session itself.
 */
const STRIPPED_REQUEST_HEADERS = new Set([
  'authorization',
  'cookie',
  'host',
  'connection',
  'content-length',
  'transfer-encoding',
]);

/** Response headers that describe OUR transport, not the payload. */
const STRIPPED_RESPONSE_HEADERS = new Set([
  'content-encoding',
  'content-length',
  'transfer-encoding',
  'connection',
]);

async function handler(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  if (!API_BASE_URL) {
    console.error('[proxy] API_BASE_URL is not set');
    return NextResponse.json({ detail: 'API is not configured' }, { status: 500 });
  }

  // Middleware already redirects unauthenticated page loads, but this route is
  // also hit by fetch() from client components, where a redirect is useless.
  // Check again and answer 401 so the caller can react. Defence in depth: this
  // handler must be safe on its own, not because something upstream is.
  const session = await auth0.getSession();
  if (!session) {
    return NextResponse.json({ detail: 'Not authenticated' }, { status: 401 });
  }

  let token: string | undefined;
  try {
    // Refreshes silently if the access token has expired.
    token = (await auth0.getAccessToken()).token;
  } catch (e) {
    // Refresh failed — typically the refresh token was revoked or hit its
    // absolute expiry. 401 tells the client to restart login; anything else
    // would present a dead session as a server fault.
    console.error('[proxy] could not obtain access token:', e);
    return NextResponse.json({ detail: 'Session expired' }, { status: 401 });
  }

  const { path } = await ctx.params;

  // Refuse traversal outright. Next normalizes `..` before matching, so this
  // shouldn't be reachable — but this handler holds a live access token, and a
  // path that escaped the API base would send it somewhere unintended. Cheap
  // check, catastrophic thing to be wrong about.
  if (path.some((seg) => seg === '..' || seg.includes('\\'))) {
    return NextResponse.json({ detail: 'Invalid path' }, { status: 400 });
  }

  const search = request.nextUrl.search;
  const target = `${API_BASE_URL}/${path.join('/')}${search}`;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!STRIPPED_REQUEST_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  headers.set('Authorization', `Bearer ${token}`);

  // GET/HEAD must not carry a body; passing one throws in undici.
  const hasBody = !['GET', 'HEAD'].includes(request.method);

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      // Never let Next cache authenticated responses. The default could serve
      // one user's candidate list to another — the worst bug in this file.
      cache: 'no-store',
      redirect: 'manual',
    });
  } catch (e) {
    console.error(`[proxy] upstream unreachable: ${request.method} ${target}`, e);
    return NextResponse.json({ detail: 'Upstream API unreachable' }, { status: 502 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!STRIPPED_RESPONSE_HEADERS.has(key.toLowerCase())) responseHeaders.set(key, value);
  });
  responseHeaders.set('Cache-Control', 'no-store');

  // Stream the body through rather than buffering: /runs/{id}/stream is an SSE
  // endpoint, and buffering would break live run progress.
  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = handler;
export const POST = handler;
export const PATCH = handler;
export const PUT = handler;
export const DELETE = handler;

// Authenticated, per-user, never cacheable.
export const dynamic = 'force-dynamic';
