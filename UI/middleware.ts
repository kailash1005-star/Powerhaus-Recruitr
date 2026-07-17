import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';
import { auth0 } from '@/lib/auth0';

/**
 * Auth middleware. Two jobs:
 *
 *  1. Mount the SDK's routes. v4 serves /auth/login, /auth/logout, /auth/callback
 *     and /auth/profile from here — there are no route files for them. (v3 used
 *     /api/auth/*; every older tutorial says so and is wrong for this SDK.)
 *
 *  2. Gate the app. Anything that isn't public requires a session, and the
 *     redirect happens at the edge — before a page renders, before a proxy call
 *     is made.
 *
 * Running the session check HERE rather than per-page is deliberate: this is also
 * where the SDK writes refreshed tokens back to the cookie. A Server Component
 * cannot set cookies, so a token refreshed there would be recomputed on every
 * request and never persisted.
 */

/** Routes reachable signed-out. Everything else needs a session. */
const PUBLIC_PATHS = ['/', '/login'];

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  // The SDK's own endpoints must stay open or login can't start and — worse —
  // logout can't complete, locking a user into a session they can't drop.
  if (pathname.startsWith('/auth/')) return true;
  return false;
}

export async function middleware(request: NextRequest) {
  // Always let the SDK run first: it both serves /auth/* and rotates the session
  // cookie on other requests. Skipping it for public paths would mean a signed-in
  // user's session silently stops refreshing while they sit on the landing page.
  const authRes = await auth0.middleware(request);

  if (request.nextUrl.pathname.startsWith('/auth/')) {
    return authRes;
  }

  if (isPublic(request.nextUrl.pathname)) {
    return authRes;
  }

  const session = await auth0.getSession(request);
  if (!session) {
    // returnTo so a deep link survives the round trip — someone opening a shared
    // /matching/<id> URL lands back on it after login, not on a generic home page.
    const loginUrl = new URL('/auth/login', request.nextUrl.origin);
    loginUrl.searchParams.set(
      'returnTo',
      request.nextUrl.pathname + request.nextUrl.search,
    );
    return NextResponse.redirect(loginUrl);
  }

  // Carry the SDK's headers (refreshed session cookie) onto the response, or the
  // rotation it just did is thrown away.
  return authRes;
}

export const config = {
  /**
   * Everything except Next internals and static assets.
   *
   * `/api/proxy/*` is intentionally INCLUDED: it's the authenticated route to
   * candidate data and the single most important thing here to protect.
   */
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|logo-mark.svg|logo-wordmark.svg|lead-row-mark.svg|.*\\.png$|.*\\.jpg$|.*\\.svg$).*)',
  ],
};
