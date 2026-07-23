# Auth0 Setup — Recruitr

**Audience:** you, doing the Auth0 dashboard setup by hand.
**Outcome:** a configured Auth0 tenant + the exact env vars I need to write the code.
**Time:** ~45 minutes.

Everything here is verified against the Auth0 docs as of **17 July 2026** —
Next.js SDK **v4** and the current session/rotation limits. Auth0 changed the Next.js
SDK significantly in v4 (routes moved from `/api/auth/*` to `/auth/*`), so older
blog posts and Stack Overflow answers will actively mislead you. Follow this file.

> **Do this first, before anything else:** there is a live Firecrawl API key
> committed in [`BE/app/config.py`](BE/app/config.py) as a default value. It is in
> your git history. Rotate it at firecrawl.dev and move it to an env var. See
> [Step 10](#step-10--rotate-the-committed-firecrawl-key).

---

## Contents

- [What we're building, and why](#what-were-building-and-why)
- [Decisions already made](#decisions-already-made)
- [Your exact values](#your-exact-values)
- [Step 1 — Create the tenant (EU region — irreversible)](#step-1--create-the-tenant-eu-region--irreversible)
- [Step 2 — Create the API](#step-2--create-the-api)
- [Step 3 — Create the Application](#step-3--create-the-application)
- [Step 4 — Refresh token rotation](#step-4--refresh-token-rotation)
- [Step 5 — "Remember me" sessions](#step-5--remember-me-sessions)
- [Step 6 — Google SSO](#step-6--google-sso)
- [Step 7 — Custom claims Action](#step-7--custom-claims-action)
- [Step 8 — Environment variables](#step-8--environment-variables)
- [Step 9 — Backend CORS](#step-9--backend-cors)
- [Step 10 — Rotate the committed Firecrawl key](#step-10--rotate-the-committed-firecrawl-key)
- [Verification checklist](#verification-checklist)
- [Hand back to me](#hand-back-to-me)

---

## What we're building, and why

You asked me to choose the token model. **I chose the Backend-For-Frontend (BFF)
pattern** — `@auth0/nextjs-auth0` v4, tokens in an encrypted httpOnly cookie, never
in JavaScript.

```
Browser  ──login──▶  Next.js on Vercel  ──▶  Auth0 (EU)
   │                 (httpOnly session cookie:
   │                  id + access + refresh token,
   │                  AES-encrypted, JS cannot read it)
   │
   └──/api/…──▶  Next route handler  ──Bearer──▶  Cloud Run FastAPI
                 (reads token from cookie,          (verifies RS256 via JWKS)
                  attaches Authorization header)
```

**Why this and not the SPA model.** The alternative — `auth0-react`, access token in
browser memory, `Bearer` straight to Cloud Run — is a smaller change to your current
code, because your pages already call Cloud Run directly. I didn't pick it, for one
reason that matters specifically to you: **you hold candidate PII.** Names, emails,
phone numbers, CVs of real people who never opted into your product. In the SPA
model, any XSS anywhere in the app — one bad dependency, one unescaped candidate
name rendered as HTML — hands the attacker a working API token. In the BFF model,
the same XSS gets nothing: the cookie is httpOnly, so JavaScript cannot read it. For
a GDPR-scoped dataset that difference is the difference between an incident and a
reportable breach.

**What it costs you.** The browser can no longer call Cloud Run directly. Requests
go through Next route handlers, which adds a hop (Vercel → Cloud Run, both in
Europe, so single-digit ms) and means I rewrite [`UI/lib/api.ts`](UI/lib/api.ts) to
point at a same-origin proxy. That is my work, not yours.

**If you'd rather have the SPA model**, say so before I start — it's a ~2 hour
difference now and a rewrite later. Steps 1–10 below are ~90% identical either way;
only [Step 3](#step-3--create-the-application) changes materially (SPA app type
instead of Regular Web App).

---

## Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Token model | BFF, httpOnly cookies | XSS can't reach tokens; you hold candidate PII |
| Tenant region | **EU (Frankfurt)** | GDPR; your Cloud Run is already `europe-west1`. **Cannot be changed after creation** |
| Multi-tenancy | Single tenant now | Your call. I'll shape claims + the Mongo user model so Organizations switch on later without a migration |
| "Remember me" | Persistent sessions + rotation | Your call — users stay signed in across browser restarts |
| Backend JWT lib | **PyJWT + PyJWKClient**, not `auth0-fastapi-api` | See below |
| App type | Regular Web Application | Required by the BFF model — *not* "Single Page Application" |

**On the backend library.** Auth0's own FastAPI quickstart recommends
`auth0-fastapi-api`. I read it, and I'm recommending against it: the current release
is **`1.0.0b5` — a beta**. I'm not putting a beta release on the code path that
decides who can read candidate data. `PyJWT` with `PyJWKClient` is stable, boring,
does JWKS fetching and caching, and is ~40 lines to wire up. Tell me if you'd rather
have the official SDK and I'll switch.

---

## Your exact values

Copy these carefully. A trailing slash or a wrong scheme is the single most common
reason login breaks, and the error Auth0 gives you (`Callback URL mismatch`) is
accurate but not helpful about *which* character is wrong.

| Thing | Value |
|---|---|
| Frontend (prod) | `https://recruit.vanceltech.com` |
| Frontend (local) | `http://localhost:3000` |
| Backend (prod) | `https://powerhaus-recruitr-5308215649.europe-west1.run.app` |
| API identifier (audience) | `https://api.recruit.vanceltech.com` |
| Claim namespace | `https://recruit.vanceltech.com/` |

> **The API identifier is not a URL that gets called.** It's just a unique string
> that must match on both sides. Deliberately **do not** use the
> `…run.app` hostname: if the Cloud Run service is ever recreated that hostname
> changes, and every previously issued token would silently stop validating.
> `https://api.recruit.vanceltech.com` never has to resolve in DNS.

---

## Step 1 — Create the tenant (EU region — irreversible)

1. Go to <https://auth0.com/signup> (or **Create tenant** in an existing account).
2. **Tenant Domain:** `recruitr-prod` → gives you `recruitr-prod.eu.auth0.com`.
3. **Region: Europe (Frankfurt)** ← the important one.
4. **Environment Type:** Development for now; flip to Production before real clients.

> ⚠️ **Region cannot be changed after creation.** Moving regions later means a new
> tenant and re-registering every user. You're pitching German staffing agencies on
> a GDPR/EU-AI-Act story — EU data residency isn't a nice-to-have, it's the sales
> pitch. Get this right on the first try.

Consider a second tenant, `recruitr-dev`, later — Auth0 has no environment separation
*inside* a tenant. Not needed today.

**Record:** your tenant domain → `AUTH0_DOMAIN`, e.g. `recruitr-prod.eu.auth0.com`
(no `https://`, no trailing slash — the most common cause of `Invalid issuer`).

---

## Step 2 — Create the API

**Applications → APIs → Create API**

| Field | Value |
|---|---|
| Name | `Recruitr API` |
| Identifier | `https://api.recruit.vanceltech.com` |
| Signing Algorithm | **RS256** |

RS256 (not HS256) means the backend verifies with Auth0's *public* key fetched from
JWKS, so no shared secret ever sits on Cloud Run.

Then open the API's **Settings** tab:

- **Allow Offline Access: ON** ← required, or you get no refresh token and "remember
  me" cannot work. Easy to miss.
- Token Expiration: `86400` (24h) is the default and fine — refresh rotation makes
  the access token's own lifetime much less interesting.

Skip **Permissions** for now — that's RBAC, and we agreed single-tenant-flat first.
I'll come back to it when you want roles.

---

## Step 3 — Create the Application

**Applications → Applications → Create Application**

- Name: `Recruitr Web`
- Type: **Regular Web Applications** ← **not** Single Page Application.

> This is the step people get wrong. "It's a React frontend, so it's a SPA, right?"
> No — in the BFF model the *Next.js server* is the OAuth client, and it can hold a
> client secret. Picking SPA here gives you a public client with no secret and the
> rest of this guide won't work.

Skip the quickstart Auth0 shows you. Go to **Settings** and set:

**Allowed Callback URLs**
```
https://recruit.vanceltech.com/auth/callback, http://localhost:3000/auth/callback
```

**Allowed Logout URLs**
```
https://recruit.vanceltech.com, http://localhost:3000
```

**Allowed Web Origins**
```
https://recruit.vanceltech.com, http://localhost:3000
```

Notes that will save you an hour:
- `/auth/callback`, **not** `/api/auth/callback`. SDK v4 moved these. Every
  pre-2025 tutorial says `/api/auth/*` and will send you in circles.
- No trailing slashes.
- Comma-separated, and Auth0 matches these **exactly**.
- If Vercel preview deployments need to log in, you'd add each preview URL here —
  they're generated per-deploy, so it's simpler to just test auth on prod + local.

**Record:** **Domain**, **Client ID**, **Client Secret** (Settings tab, bottom).

---

## Step 4 — Refresh token rotation

Same Application → **Settings → Advanced Settings → Grant Types**. Confirm both
**Authorization Code** and **Refresh Token** are ticked.

Then **Settings → Refresh Token Rotation**:

| Setting | Value |
|---|---|
| **Rotation** | **Enabled** |
| Reuse Interval | `3` seconds |
| **Absolute Expiration** | Enabled — `2592000` (30 days) |
| **Inactivity Expiration** | Enabled — `1296000` (15 days) |

**What rotation buys you.** Every refresh mints a new refresh token and invalidates
the old one. If a token is ever stolen and *both* the thief and the real user try to
use it, Auth0 detects the reuse and revokes the entire token family — the session
dies for everyone. Without rotation, a stolen refresh token is a permanent backdoor
that survives password changes.

The 3-second reuse interval exists for a real reason: network retries and React
double-renders can legitimately replay the same refresh once. Setting it to `0`
causes random logouts under flaky mobile networks.

---

## Step 5 — "Remember me" sessions

This is the feature you asked for. **Settings → Advanced → Session** (tenant-level):

| Setting | Value | Meaning |
|---|---|---|
| **Persistent Session** | **Enabled** | Cookie survives browser close. This *is* "remember me" |
| Inactivity timeout | `7` days | Idle this long → sign in again |
| Require login after | `30` days | Hard cap regardless of activity |

Auth0's documented limits, so you know your headroom: inactivity defaults to 3 days
(max 100); "require login after" defaults to 30 days (max 365).

Why not the maximums: a recruiter's laptop is a laptop, and it gets left on trains.
30 days absolute is a reasonable place to land for a tool holding candidate PII —
long enough that nobody's annoyed, short enough that a lost device isn't a
permanent open door. Push it to 90 if the team complains; I'd argue against 365.

> Persistent-cookie behaviour is ultimately up to the browser — Auth0 can't
> guarantee every browser honours it. Don't treat "remember me" as a hard promise
> in the UI copy.

---

## Step 6 — Google SSO

**Authentication → Social → Create Connection → Google**.

The default dev keys work instantly but are **shared, rate-limited, and show
Auth0's name on the consent screen** — not something to show a German staffing
client. For production, create your own:

1. <https://console.cloud.google.com/apis/credentials> → **Create OAuth client ID**
   → Web application.
2. Authorized redirect URI:
   ```
   https://recruitr-prod.eu.auth0.com/login/callback
   ```
   (your tenant domain — this is Auth0's callback, not yours).
3. Paste the Client ID + Secret into the Auth0 Google connection.
4. In the connection's **Applications** tab, enable it for **Recruitr Web**.

**Microsoft/Entra ID** works the same way if you want it — tell me and I'll extend
this section. It's the one enterprise clients most often ask for, and it's also the
natural first customer of Auth0 Organizations when you're ready.

---

## Step 7 — Custom claims Action

This is the seam that makes Organizations painless later. We stamp a `tenant_id`
into every token *now*, hardcoded to one value. When you go multi-tenant, this
Action changes and nothing else does — no data migration, no token format change.

We also stamp `email` onto the access token: Auth0 access tokens do not carry
`email` by default (that's an ID-token/userinfo claim), but the backend's admin
allowlist (`ADMIN_EMAILS`, see [qa.py](BE/app/api/v1/qa.py)) checks the *access*
token, since that's what every API call bears. Without this claim, `principal.email`
is always `None` and the allowlist can never match — no email will ever be
recognized as admin, no matter what's in `.env`.

**Actions → Library → Create Action → Build from scratch**
Name: `Add tenant and roles claims`, Trigger: **Login / Post Login**.

```js
/**
 * Stamps tenant + roles + email onto tokens.
 *
 * Single-tenant today: every user gets tenantId "default", matching the tenantId
 * already used by the outreach collections. When Recruitr goes multi-tenant this
 * becomes event.organization.id and the rest of the stack is unaffected — which is
 * the whole point of doing it now.
 *
 * Claims MUST be namespaced with a URL or Auth0 silently drops them.
 */
exports.onExecutePostLogin = async (event, api) => {
  const NS = 'https://recruit.vanceltech.com/';

  const tenantId = event.organization?.id ?? 'default';
  const roles = event.authorization?.roles ?? [];
  const email = event.user.email;

  // Both tokens: the ID token drives UI, the access token drives API authorization.
  api.idToken.setCustomClaim(NS + 'tenant_id', tenantId);
  api.accessToken.setCustomClaim(NS + 'tenant_id', tenantId);
  api.idToken.setCustomClaim(NS + 'roles', roles);
  api.accessToken.setCustomClaim(NS + 'roles', roles);
  api.idToken.setCustomClaim(NS + 'email', email);
  api.accessToken.setCustomClaim(NS + 'email', email);
};
```

**Deploy**, then **Actions → Triggers → post-login** and drag the Action into the
flow. It does nothing until it's in the flow — easy to miss.

> The namespace prefix is not decoration. Auth0 discards non-namespaced custom
> claims without warning, and you'll spend an afternoon wondering where your claim
> went.

---

## Step 8 — Environment variables

Generate the session-encryption secret first:

```bash
openssl rand -hex 32
```

### Vercel — frontend

**Project → Settings → Environment Variables.** Your Next app lives in `UI/`, so
make sure the project's **Root Directory** is set to `UI` (Settings → General).

| Name | Value | Environments |
|---|---|---|
| `AUTH0_DOMAIN` | `recruitr-prod.eu.auth0.com` | All |
| `AUTH0_CLIENT_ID` | from Step 3 | All |
| `AUTH0_CLIENT_SECRET` | from Step 3 | All |
| `AUTH0_SECRET` | `openssl` output above | All |
| `APP_BASE_URL` | `https://recruit.vanceltech.com` | Production |
| `AUTH0_AUDIENCE` | `https://api.recruit.vanceltech.com` | All |
| `AUTH0_SCOPE` | `openid profile email offline_access` | All |
| `API_BASE_URL` | `https://powerhaus-recruitr-5308215649.europe-west1.run.app` | All |

Two things worth understanding rather than just copying:

- **`offline_access` in the scope is what produces a refresh token.** Leave it out
  and "remember me" silently doesn't work — no error, users just get logged out.
- **`API_BASE_URL` has no `NEXT_PUBLIC_` prefix, deliberately.** Under BFF only the
  server calls the API. `NEXT_PUBLIC_*` gets inlined into the JS bundle and shipped
  to the browser — fine for a hostname, fatal for anything secret. The existing
  `NEXT_PUBLIC_API_URL` in [`UI/lib/api.ts`](UI/lib/api.ts) goes away when I wire
  the proxy.

For local dev I'll add `UI/.env.local` with the same names and
`APP_BASE_URL=http://localhost:3000`. Don't commit it — root `.gitignore` already
covers `.env*`.

### Cloud Run — backend

```bash
gcloud run services update powerhaus-recruitr \
  --region=europe-west1 \
  --update-env-vars \
AUTH0_DOMAIN=recruitr-prod.eu.auth0.com,\
AUTH0_AUDIENCE=https://api.recruit.vanceltech.com,\
AUTH0_ISSUER=https://recruitr-prod.eu.auth0.com/
```

> Note the asymmetry, because it *will* bite you: `AUTH0_DOMAIN` has **no** scheme
> and **no** trailing slash; `AUTH0_ISSUER` has **both**. That's not me being fussy
> — the issuer is compared character-for-character against the token's `iss` claim.
> Mismatch → every request 401s with `Invalid issuer`.

Your `cloudbuild.yaml` only sets `--image` on deploy, so env vars you set here
survive future builds. That's by design and it's correct.

---

## Step 9 — Backend CORS

[`BE/app/config.py`](BE/app/config.py) currently allows:

```python
default=["http://localhost:3000", "https://job-hunt-kappa-two.vercel.app"]
```

That `job-hunt-kappa-two` origin is a leftover from a different project and should
go. Under BFF, browsers don't call Cloud Run at all — Vercel's server does, and
server-to-server calls aren't subject to CORS — so this list shrinks rather than
grows. I'll handle the code; it's listed here so the change isn't a surprise.

---

## Step 10 — Rotate the committed Firecrawl key

[`BE/app/config.py:52`](BE/app/config.py) contains:

```python
FIRECRAWL_API_KEY: str = Field(default="", ...)  # set via env; never hardcode a key
```

A live credential, in git history, as a default value. Anyone with repo access —
now or in any future clone, fork, or contractor handover — has it.

1. Log into firecrawl.dev → revoke that key → issue a new one.
2. Set `FIRECRAWL_API_KEY` on Cloud Run and in your local `.env`.
3. I'll change the default to `""` so a missing key fails loudly instead of
   silently falling back to a burned one.

Rotating is what matters. Scrubbing git history is a bigger conversation (it rewrites
every commit hash) and it's moot once the key is dead.

---

## Verification checklist

Before handing back, confirm:

- [ ] Tenant region is **EU** (domain contains `.eu.auth0.com`)
- [ ] API identifier is exactly `https://api.recruit.vanceltech.com`
- [ ] API signing algorithm is **RS256**
- [ ] API **Allow Offline Access** is **ON**
- [ ] Application type is **Regular Web Application** (not SPA)
- [ ] Callback URL uses `/auth/callback` (not `/api/auth/callback`)
- [ ] Grant types include **Refresh Token**
- [ ] Refresh Token **Rotation** is enabled
- [ ] **Persistent Session** is enabled
- [ ] Google connection is enabled *for the Recruitr Web application*
- [ ] The Action is **deployed AND dragged into the post-login trigger**
- [ ] Firecrawl key rotated

Quick smoke test that the API exists and signs correctly — no code needed:

```bash
curl -s https://recruitr-prod.eu.auth0.com/.well-known/openid-configuration | jq '.issuer, .jwks_uri'
```

Should print your issuer (with trailing slash) and a JWKS URL. If this 404s, the
tenant domain is wrong.

---

## Hand back to me

Send me these and I'll start building:

```
AUTH0_DOMAIN=            # recruitr-prod.eu.auth0.com
AUTH0_CLIENT_ID=
AUTH0_AUDIENCE=          # https://api.recruit.vanceltech.com
```

**Do not paste `AUTH0_CLIENT_SECRET` or `AUTH0_SECRET` into the chat.** I never need
them — they go into Vercel's env var UI, by you, and nowhere else. If either ever
lands in a chat log or a commit, rotate it.

Also confirm: **is `recruit.vanceltech.com` already pointed at the Vercel project?**
If DNS isn't live yet, `APP_BASE_URL` won't resolve and the callback fails in a way
that looks like an Auth0 misconfiguration but isn't.

### Then I build

**Frontend** — SDK + middleware, `/auth/*` routes, the authenticated API proxy,
rewiring `lib/api.ts`, replacing the placeholder `signIn()` in
[`UI/components/pages/LoginPage.tsx`](UI/components/pages/LoginPage.tsx) with the
real redirect, enabling the SSO tiles, route protection so `/runs` et al. stop being
publicly reachable, and logout.

**Backend** — JWT verification dependency (PyJWT + JWKS with caching), applied
across every `/api/v1` router, `sub` → Mongo user upsert, `tenant_id` from the token
rather than trusted from the client, and tightened CORS.

**Database** — a `users` collection keyed on Auth0 `sub`, indexes, and `tenantId`
threaded through the collections that don't carry it yet (`candidates`,
`cv_candidates`, `runs` — `outreach_*` already has it).

### One thing I want to flag now

Right now **every endpoint on that Cloud Run URL is open to the internet** —
candidates, CVs, outreach, all of it, no auth at all. Anyone who knows the hostname
can read real candidates' names, emails, and phone numbers. Under GDPR that's a
reportable exposure today, not a to-do for after the Auth0 work lands.

Auth0 setup is the right fix and it's days away, not weeks. But if you want the door
shut *tonight*, I can put a shared-secret header check across the API in about an
hour — ugly, temporary, and strictly better than open. Your call; say the word and
I'll do it while you work through this guide.
