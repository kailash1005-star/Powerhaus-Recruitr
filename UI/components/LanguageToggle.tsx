'use client';

import { useI18n, type Lang } from '@/lib/i18n';

/**
 * Compact EN | DE segmented toggle for the top-right of the TopBar.
 * Switches the app language via the i18n context (persisted to localStorage).
 */
export function LanguageToggle() {
  const { lang, setLang } = useI18n();

  const options: { value: Lang; label: string }[] = [
    { value: 'en', label: 'EN' },
    { value: 'de', label: 'DE' },
  ];

  return (
    <div
      role="group"
      aria-label="Language"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        border: '1px solid var(--border-card)',
        borderRadius: 6,
        overflow: 'hidden',
        background: 'var(--bg-app)',
        height: 32,
      }}
    >
      {options.map((opt) => {
        const active = lang === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => setLang(opt.value)}
            aria-pressed={active}
            style={{
              height: '100%',
              padding: '0 10px',
              fontSize: 12,
              fontWeight: 600,
              letterSpacing: '0.03em',
              cursor: 'pointer',
              border: 'none',
              fontFamily: 'inherit',
              background: active ? 'var(--primary)' : 'transparent',
              color: active ? 'var(--bg-app)' : 'var(--fg-muted)',
              transition: 'background 120ms, color 120ms',
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
