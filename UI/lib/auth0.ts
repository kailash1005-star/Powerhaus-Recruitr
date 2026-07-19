import 'server-only';
import { Auth0Client } from '@auth0/nextjs-auth0/server';

/**
 * The Auth0 client — server-side only.
 *
 * This is the BFF hinge. Tokens live in an AES-encrypted, httpOnly cookie that
 * JavaScript cannot read, so an XSS bug anywhere in the app cannot steal an API
 * token. That property is why we hold candidate PII behind this rather than the
 * SPA model. See docs/engineering/AUTH0_SETUP.md for the full reasoning.
 *
 * Configuration comes from the environment (never hardcoded):
 *   AUTH0_DOMAIN         tenant host, no scheme     e.g. recruitr-prod.eu.auth0.com
 *   AUTH0_CLIENT_ID
 *   AUTH0_CLIENT_SECRET  server-only; never NEXT_PUBLIC_*
 *   AUTH0_SECRET         32-byte hex; encrypts the session cookie
 *   APP_BASE_URL         https://recruit.vanceltech.com
 *
 * The `server-only` import on line 1 is load-bearing: it makes importing this
 * file from a Client Component a BUILD error rather than a silent leak of
 * AUTH0_CLIENT_SECRET into the browser bundle. It's a compiler-enforced
 * guarantee, which is worth more than a code review or a grep — those pass right
 * up until the day someone adds 'use client' to the wrong file.
 */
export const auth0 = new Auth0Client({
  authorizationParameters: {
    // `audience` is what makes Auth0 mint an ACCESS token for our API rather than
    // only an ID token. Omit it and you get a session with nothing the backend
    // will accept — a confusing failure, because login itself appears to work.
    audience: process.env.AUTH0_AUDIENCE,

    // `offline_access` is what yields a refresh token, which is what makes
    // "remember me" and silent renewal possible. Leave it out and users are
    // logged out when the access token expires, with no error to explain why.
    scope: process.env.AUTH0_SCOPE ?? 'openid profile email offline_access',
  },
});
