'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { Icon } from '../Icon';
import { AuthShell } from '../marketing/AuthShell';
import { ACTION, ACTION_HOVER } from '../marketing/shared';
import { useState } from 'react';

/* ── Login ────────────────────────────────────────────────────────────────────
   Auth0 Universal Login, via the BFF model.

   This page deliberately does NOT collect credentials. Under BFF the browser
   must never hold a password or a token, and Universal Login is a full redirect
   to Auth0's own hosted page — so every control here is a link to /auth/login,
   the route the SDK mounts in middleware.ts.

   (The password form this page used to mock up would have required Auth0's
   Resource Owner Password grant: it puts credentials through our origin, blocks
   SSO and MFA, and Auth0 disables it by default. Universal Login is the reason
   MFA / device trust / SSO can be switched on later as dashboard settings rather
   than frontend rewrites.)

   The SSO tiles are links with a `connection` param rather than onClick buttons
   because `connection` tells Auth0 which identity provider to jump straight to,
   skipping its account picker.

   Connection names (Auth0 → Authentication → Social):
     google-oauth2  — Google
     windowslive    — Microsoft personal accounts
   Enterprise Entra ID / SAML gets a per-client connection name — that's where
   Organizations come in later. */

/** Auth0 sends the user back here with ?error=… when a callback fails. */
const ERROR_COPY: Record<string, string> = {
  access_denied: 'Access denied. Your account may not have permission to sign in.',
  unauthorized: 'Access denied. Your account may not have permission to sign in.',
  login_required: 'Your session expired. Please sign in again.',
  consent_required: 'Additional consent is required to continue.',
};

function SsoButton({
  label,
  connection,
  returnTo,
  children,
}: {
  label: string;
  connection: string;
  returnTo: string;
  children: React.ReactNode;
}) {
  const [hover, setHover] = useState(false);
  const href = `/auth/login?connection=${encodeURIComponent(connection)}&returnTo=${encodeURIComponent(returnTo)}`;

  return (
    <a
      href={href}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        width: '100%',
        height: 40,
        borderRadius: 6,
        border: '1px solid var(--border-strong)',
        background: hover ? 'var(--bg-row-hover)' : 'var(--bg-app)',
        fontSize: 13,
        fontWeight: 500,
        color: 'var(--fg-primary)',
        textDecoration: 'none',
        transition: 'background 120ms var(--easing-standard)',
      }}
    >
      {children}
      {label}
    </a>
  );
}

function GoogleMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.6 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.3 0-9.6-3.4-11.3-8.1l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.2-4.1 5.6l6.2 5.2C41.4 35.9 44 30.5 44 24c0-1.3-.1-2.3-.4-3.5z" />
    </svg>
  );
}

function LoginContent() {
  const params = useSearchParams();
  const [hover, setHover] = useState(false);

  // Where to land after login. Middleware sets returnTo on the deep link it
  // bounced; default to the app's home.
  const returnTo = params.get('returnTo') || '/runs';
  const error = params.get('error');
  const errorMessage = error ? (ERROR_COPY[error] ?? 'Sign-in failed. Please try again.') : null;

  const emailHref = `/auth/login?returnTo=${encodeURIComponent(returnTo)}`;

  return (
    <AuthShell
      title="Welcome back"
      subtitle="Sign in to your workspace"
      footer={
        <>
          Need an account?{' '}
          <Link href="/#pilot" style={{ color: 'var(--fg-primary)', fontWeight: 500 }}>
            Talk to us about a pilot
          </Link>
        </>
      }
    >
      {errorMessage && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            marginBottom: 16,
            padding: '10px 12px',
            borderRadius: 6,
            background: 'var(--status-danger-bg)',
            border: '1px solid var(--status-danger)',
            fontSize: 13,
            color: 'var(--fg-primary)',
          }}
        >
          <Icon name="alert-circle" size={15} style={{ color: 'var(--status-danger)', marginTop: 1 }} />
          {errorMessage}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <SsoButton label="Continue with Google" connection="google-oauth2" returnTo={returnTo}>
          <GoogleMark />
        </SsoButton>
        <SsoButton label="Continue with Microsoft" connection="windowslive" returnTo={returnTo}>
          <Icon name="grid-2x2" size={15} style={{ color: 'var(--fg-muted)' }} />
        </SsoButton>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          margin: '18px 0',
          fontSize: 11,
          color: 'var(--fg-subtle)',
        }}
      >
        <span style={{ flex: 1, height: 1, background: 'var(--border-default)' }} />
        or
        <span style={{ flex: 1, height: 1, background: 'var(--border-default)' }} />
      </div>

      <a
        href={emailHref}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          width: '100%',
          height: 40,
          borderRadius: 6,
          border: `1px solid ${ACTION}`,
          background: hover ? ACTION_HOVER : ACTION,
          color: '#FFFFFF',
          fontSize: 14,
          fontWeight: 600,
          textDecoration: 'none',
          transition: 'background 120ms var(--easing-standard)',
        }}
      >
        <Icon name="mail" size={15} />
        Continue with email
      </a>

      <p style={{ fontSize: 11, color: 'var(--fg-subtle)', marginTop: 16, textAlign: 'center', lineHeight: 1.5 }}>
        You&apos;ll be redirected to our secure sign-in page.
      </p>
    </AuthShell>
  );
}

export function LoginPage() {
  // useSearchParams needs a Suspense boundary or the whole route opts out of
  // static rendering and `next build` fails.
  return (
    <Suspense fallback={<AuthShell title="Welcome back" subtitle="Sign in to your workspace"><div /></AuthShell>}>
      <LoginContent />
    </Suspense>
  );
}
