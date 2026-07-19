# Auth Implementation Plan

Companion to [AUTH0_SETUP.md](AUTH0_SETUP.md). That file is your dashboard work;
this one is mine. Ticked boxes are done and tested.

**Ground rule:** every chunk below is small, independently testable, and leaves the
app runnable. Nothing here needs your Auth0 credentials — the backend is verified
against a **locally minted RS256 keypair and a stubbed JWKS**, which exercises the
real verification code path without Auth0 existing yet. Credentials are only needed
for Phase C (live end-to-end), which is where we meet when you're done.

---

## How this is tested without Auth0

The interesting security question is not "does login work" — it's **"does the API
reject tokens it should reject?"** That's testable offline and it's where the bugs
hide:

| Attack / mistake | Expected |
|---|---|
| No `Authorization` header | 401 |
| Garbage / malformed token | 401 |
| Expired token | 401 |
| Correct signature, **wrong audience** | 401 |
| Correct signature, **wrong issuer** | 401 |
| Valid claims, **signature from a different key** | 401 |
| **`alg: none`** (unsigned) | 401 |
| **HS256 token signed with the public key** (algorithm-confusion attack) | 401 |
| Valid token | 200 + claims |

A JWT library used naively passes the happy path and fails half of these. That's the
point of testing it.

---

## Phase A — Backend (no credentials needed)

- [x] **A1 — Config.** Auth0 settings in `BE/app/config.py`: `AUTH0_DOMAIN`,
      `AUTH0_AUDIENCE`, `AUTH0_ISSUER`, plus `AUTH_ENABLED` so the suite and local
      dev can run without a tenant.
      *Test:* settings import; issuer derives from domain; `AUTH_ENABLED` defaults on.

- [x] **A2 — JWT verifier** (`BE/app/security/jwt_verifier.py`). PyJWT +
      `PyJWKClient`, RS256 pinned, JWKS cached.
      *Test:* the full table above, via a local RSA keypair + stubbed JWKS.

- [x] **A3 — FastAPI dependency** (`BE/app/security/deps.py`). `Principal`
      (sub, email, tenant_id, roles) + `require_auth`.
      *Test:* `TestClient` on a throwaway app — 401s without/with bad token, 200 +
      correct principal with a good one.

- [x] **A4 — Users collection** (`BE/app/models/users.py`,
      `BE/app/services/user_service.py`). Upsert on Auth0 `sub`, indexes.
      *Test:* doc shaping is pure and asserted; upsert runs against a stub
      collection asserting the exact filter/update.

- [x] **A5 — Protect the routers.** Auth applied at the `api_router` level so a new
      router is protected by default. `/health` stays public.
      *Test:* `/health` 200 without a token; a real `/api/v1/*` route 401s without
      one, 200s with.

- [x] **A6 — CORS.** Drop the stale `job-hunt-kappa-two` origin; env-driven.
      *Test:* assert the resolved origin list.

## Phase B — Frontend (no credentials needed)

- [x] **B1 — SDK + client.** `@auth0/nextjs-auth0` v4, `UI/lib/auth0.ts`,
      `UI/.env.example`.
- [x] **B2 — Middleware.** Mounts `/auth/*`; protects app routes; leaves `/` and
      `/login` public.
- [x] **B3 — API proxy.** `UI/app/api/proxy/[...path]/route.ts` — attaches the
      access token server-side. This is the BFF hinge.
- [x] **B4 — Rewire `lib/api.ts`** to the same-origin proxy; drop
      `NEXT_PUBLIC_API_URL`.
- [x] **B5 — LoginPage.** Replace the placeholder `signIn()` with the real redirect;
      enable the Google/Microsoft tiles.
- [x] **B6 — Session in the shell.** Real user email + working logout in the
      sidebar (currently hardcoded `user@recruitr.io`).
- [x] **B7 — Landing.** Signed-in visitors get "Go to app" instead of "Sign in".

*Test for B:* `tsc --noEmit` + `next build` after each. The login flow itself can't
be exercised until Phase C — no tenant to redirect to.

## Phase C — End-to-end (needs your env values)

- [ ] **C1** — Paste `AUTH0_DOMAIN` / `AUTH0_CLIENT_ID` / `AUTH0_AUDIENCE`; secrets
      go in Vercel by you.
- [ ] **C2** — Live login → callback → session cookie.
- [ ] **C3** — Proxy carries a real token; Cloud Run accepts it.
- [ ] **C4** — Refresh rotation: force expiry, confirm silent renewal.
- [ ] **C5** — "Remember me": close browser, reopen, still signed in.
- [ ] **C6** — Logout clears session and Auth0 SSO.
- [ ] **C7** — Confirm `/api/v1/*` is dead to unauthenticated callers.

---

## Decisions inside this plan

**Auth is default-on, opt-out.** `AUTH_ENABLED=false` exists for tests and local
dev, and it's guarded: the app **refuses to start** if auth is disabled while a
production `AUTH0_DOMAIN` is configured. A flag that can silently disable auth in
production is worse than no flag.

**Protection is applied at the router aggregate**, not per-endpoint. Per-endpoint
means the next router someone adds is public by accident. Default-secure, with
`/health` explicitly opted out.

**`tenant_id` comes from the token, never the client.** A client-supplied tenant id
is just a request to read someone else's data.

---

## Known gaps I am not fixing here

- **Rotate the Firecrawl key** ([config.py](BE/app/config.py)) — yours to do.
- **RBAC / roles** — claim is stamped and plumbed, no roles defined yet.
- **Organizations** — single-tenant now; the seam is in place.
- **`tenantId` backfill** — `outreach_*` has it; `candidates`, `cv_candidates`,
  `runs` don't. Threading it through is a data migration, worth its own pass once
  auth lands.
