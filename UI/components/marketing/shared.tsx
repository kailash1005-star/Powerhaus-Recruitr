'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';

/**
 * Primitives shared by the landing page and login. These mirror the app's
 * design language — near-black primary actions, navy brand bands, borders
 * instead of drop shadows — but at marketing scale (see the .mk-* block in
 * globals.css for the type ramp and the responsive collapse).
 */

/** Near-black, matching Button.tsx's primary. Navy is reserved for brand bands. */
export const ACTION = '#0F0F0F';
export const ACTION_HOVER = '#1F1F1F';

export function Logo({ onDark = false }: { onDark?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span
        style={{
          width: 26,
          height: 26,
          borderRadius: 6,
          background: onDark ? 'rgba(255,255,255,0.14)' : 'var(--bg-chip)',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        {/* The logo mark is a fixed near-black; invert it on the navy panels. */}
        <img
          src="/logo-mark.svg"
          width="15"
          height="15"
          alt=""
          style={onDark ? { filter: 'brightness(0) invert(1)' } : undefined}
        />
      </span>
      <span
        style={{
          fontSize: 16,
          fontWeight: 700,
          letterSpacing: '-0.01em',
          color: onDark ? 'var(--primary-fg)' : 'var(--fg-primary)',
        }}
      >
        Recruitr
      </span>
    </span>
  );
}

type CtaTone = 'action' | 'outline' | 'onDark' | 'onDarkOutline';

const CTA_TONES: Record<CtaTone, { rest: React.CSSProperties; hoverBg: string }> = {
  action: {
    rest: { background: ACTION, color: '#FFFFFF', border: `1px solid ${ACTION}` },
    hoverBg: ACTION_HOVER,
  },
  outline: {
    rest: { background: 'var(--bg-app)', color: 'var(--fg-primary)', border: '1px solid var(--border-strong)' },
    hoverBg: 'var(--bg-row-hover)',
  },
  onDark: {
    rest: { background: '#FFFFFF', color: 'var(--primary)', border: '1px solid #FFFFFF' },
    hoverBg: 'rgba(255,255,255,0.9)',
  },
  onDarkOutline: {
    rest: { background: 'transparent', color: '#FFFFFF', border: '1px solid rgba(255,255,255,0.28)' },
    hoverBg: 'rgba(255,255,255,0.1)',
  },
};

/** A link that looks like a Button. Buttons navigate here, so <a> not <button>. */
export function Cta({
  href,
  children,
  tone = 'action',
  size = 'lg',
}: {
  href: string;
  children: ReactNode;
  tone?: CtaTone;
  size?: 'sm' | 'lg';
}) {
  const [hover, setHover] = useState(false);
  const spec = CTA_TONES[tone];

  return (
    <Link
      href={href}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        height: size === 'lg' ? 44 : 36,
        padding: size === 'lg' ? '0 20px' : '0 14px',
        borderRadius: 6,
        fontSize: size === 'lg' ? 14 : 13,
        fontWeight: 600,
        textDecoration: 'none',
        whiteSpace: 'nowrap',
        transition: 'background 120ms var(--easing-standard)',
        ...spec.rest,
        ...(hover ? { background: spec.hoverBg } : null),
      }}
    >
      {children}
    </Link>
  );
}

/** A light section with an optional eyebrow + heading block. */
export function Section({
  id,
  eyebrow,
  title,
  subtitle,
  children,
}: {
  id?: string;
  eyebrow?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} className="mk-section">
      <div className="mk-container">
        <div style={{ maxWidth: 660 }}>
          {eyebrow && <p className="mk-eyebrow">{eyebrow}</p>}
          <h2 className="mk-h2">{title}</h2>
          {subtitle && <p className="mk-lead" style={{ marginTop: 14 }}>{subtitle}</p>}
        </div>
        <div style={{ marginTop: 40 }}>{children}</div>
      </div>
    </section>
  );
}

/** Small outlined pill — used for the hero badge and stat sourcing. */
export function Pill({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        fontWeight: 500,
        padding: '5px 10px',
        borderRadius: 9999,
        background: 'var(--bg-app)',
        border: '1px solid var(--border-card)',
        color: 'var(--fg-muted)',
      }}
    >
      {children}
    </span>
  );
}
