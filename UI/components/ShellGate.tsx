'use client';

import { usePathname } from 'next/navigation';
import { AppShell } from './AppShell';

/**
 * Picks the frame for a route.
 *
 * The app proper lives in AppShell — a fixed, full-viewport shell with the left
 * rail, where scrolling happens inside each page. The marketing routes (landing,
 * login) have no sidebar and must scroll as a whole page. Since globals.css locks
 * `html, body { overflow: hidden }` for the app shell, marketing gets its own
 * fixed scroll container rather than relying on the document to scroll.
 */
const MARKETING_ROUTES = new Set(['/', '/login']);

export function ShellGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? '';

  if (MARKETING_ROUTES.has(pathname)) {
    return (
      <div
        style={{
          position: 'fixed',
          inset: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          background: 'var(--bg-app)',
          fontFamily: 'var(--font-sans)',
          color: 'var(--fg-primary)',
        }}
      >
        {children}
      </div>
    );
  }

  return <AppShell>{children}</AppShell>;
}
