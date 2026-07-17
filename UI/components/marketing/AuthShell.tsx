'use client';

import Link from 'next/link';
import { type ReactNode } from 'react';
import { Logo } from './shared';

/** Centered auth card with the navy brand panel on the left at desktop widths. */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="mk-auth">
      <div className="mk-auth-brand">
        <Link href="/" style={{ textDecoration: 'none' }}>
          <Logo onDark />
        </Link>
        <div>
          <h2 style={{ fontSize: 26, fontWeight: 600, lineHeight: 1.3, maxWidth: 380, margin: 0 }}>
            The agentic hiring lifecycle, on the stack you already own.
          </h2>
          <p style={{ fontSize: 14, lineHeight: 1.5, color: 'rgba(255,255,255,0.7)', maxWidth: 380, marginTop: 14 }}>
            Source, re-mine, rank and reach out — every decision with a reason attached, and a human
            always in the loop.
          </p>
        </div>
        <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', margin: 0 }}>
          GDPR · EU AI Act ready · Your data stays yours.
        </p>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={{ width: '100%', maxWidth: 380 }}>
          <div className="mk-auth-logo-sm" style={{ justifyContent: 'center', marginBottom: 24 }}>
            <Link href="/" style={{ textDecoration: 'none' }}>
              <Logo />
            </Link>
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.01em', margin: 0 }}>{title}</h1>
          <p style={{ fontSize: 14, color: 'var(--fg-muted)', marginTop: 6 }}>{subtitle}</p>
          <div style={{ marginTop: 24 }}>{children}</div>
          {footer && (
            <div style={{ marginTop: 20, fontSize: 13, textAlign: 'center', color: 'var(--fg-muted)' }}>
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
