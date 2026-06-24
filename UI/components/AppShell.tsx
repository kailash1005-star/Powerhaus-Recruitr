'use client';

import { Sidebar } from './Sidebar';
import { LanguageProvider } from '@/lib/i18n';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <LanguageProvider>
      <div
        style={{
          position: 'fixed',
          inset: 0,
          display: 'flex',
          background: 'var(--bg-app)',
          fontFamily: 'var(--font-sans)',
          color: 'var(--fg-primary)',
        }}
      >
        <Sidebar />
        <main
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 0,
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          {children}
        </main>
      </div>
    </LanguageProvider>
  );
}
