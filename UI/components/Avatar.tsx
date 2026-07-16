'use client';

import { useState } from 'react';

/** Initials for a person, from whatever name parts we happen to have. */
function initialsOf(name?: string | null, first?: string | null, last?: string | null): string {
  const a = (first || '').trim();
  const b = (last || '').trim();
  if (a || b) return `${a[0] ?? ''}${b[0] ?? ''}`.toUpperCase() || '?';
  const parts = (name || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return '?';
  return `${parts[0][0] ?? ''}${parts.length > 1 ? parts[parts.length - 1][0] : ''}`.toUpperCase();
}

/** Deterministic tint so initials avatars are distinguishable in a list without
 *  being random on every render. Muted on purpose — an avatar is an identifier,
 *  not a status, and must not compete with the score colours beside it. */
const TINTS = [
  ['#E0E7FF', '#3730A3'], ['#DBEAFE', '#1E40AF'], ['#D1FAE5', '#065F46'],
  ['#FCE7F3', '#9D174D'], ['#FEF3C7', '#92400E'], ['#EDE9FE', '#5B21B6'],
  ['#CFFAFE', '#155E75'], ['#FEE2E2', '#991B1B'],
];
function tintFor(seed: string): [string, string] {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return TINTS[h % TINTS.length] as [string, string];
}

export interface AvatarProps {
  /** LinkedIn CDN photo. Public, but SIGNED AND EXPIRING (~weeks), so a dead link
   *  is expected on older records — we fall back to initials rather than show a
   *  broken image. */
  src?: string | null;
  name?: string | null;
  firstName?: string | null;
  lastName?: string | null;
  size?: number;
  /** Ring colour, e.g. to echo a match band. */
  ring?: string;
  title?: string;
  style?: React.CSSProperties;
}

export function Avatar({
  src, name, firstName, lastName, size = 40, ring, title, style,
}: AvatarProps) {
  const [broken, setBroken] = useState(false);
  const initials = initialsOf(name, firstName, lastName);
  const [bg, fg] = tintFor(name || `${firstName ?? ''}${lastName ?? ''}` || '?');
  const showPhoto = !!src && !broken;

  const base: React.CSSProperties = {
    width: size, height: size, borderRadius: 9999, flexShrink: 0,
    border: ring ? `2px solid ${ring}` : '1px solid var(--border-card)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    overflow: 'hidden', background: bg, color: fg,
    fontSize: Math.max(10, Math.round(size * 0.36)), fontWeight: 700,
    letterSpacing: '0.02em', userSelect: 'none',
    ...style,
  };

  if (!showPhoto) {
    return <div style={base} title={title ?? name ?? undefined} aria-label={name ?? undefined}>{initials}</div>;
  }

  return (
    <div style={base} title={title ?? name ?? undefined}>
      <img
        src={src!}
        alt={name ? `${name} — LinkedIn profile photo` : 'Profile photo'}
        width={size}
        height={size}
        loading="lazy"
        decoding="async"
        // The CDN is fine without a referer, and sending one leaks our app URL to
        // LinkedIn on every avatar render.
        referrerPolicy="no-referrer"
        onError={() => setBroken(true)}
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
    </div>
  );
}
