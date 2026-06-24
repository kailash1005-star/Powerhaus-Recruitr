'use client';

/**
 * Lightweight i18n for the app. No external dependency — a typed dictionary
 * plus a context. The active language is persisted to localStorage and mirrored
 * onto <html lang>. Translate a string with the `t()` returned by useI18n().
 *
 * To localize more UI: add a key to BOTH `en` and `de` below, then call
 * `t('your.key')` in the component (which must be a client component).
 */
import { createContext, useContext, useEffect, useState, useCallback } from 'react';

export type Lang = 'en' | 'de';

const STORAGE_KEY = 'recruitr.lang';

// ── Dictionaries ────────────────────────────────────────────────────────────
const en = {
  'group.assistant': 'Assistant',
  'group.pipeline': 'Pipeline',
  'group.outreach': 'Outreach',
  'group.monitor': 'Monitor',
  'group.system': 'System',

  'nav.agent': 'AI Engineer',
  'nav.runs': 'Email Campaigns',
  'nav.candidates': 'Candidates',
  'nav.matching': 'Candidate Matching',
  'nav.outreach': 'Outreach',
  'nav.dashboards': 'Dashboards',
  'nav.settings': 'Settings',
  'nav.integrations': 'Integrations',

  'badge.soon': 'Soon',
  'sidebar.workspace': 'Workspace',
  'sidebar.freeTrial': 'Free Trial',

  'topbar.search': 'Search…',
} as const;

type Dict = Record<keyof typeof en, string>;

const de: Dict = {
  'group.assistant': 'Assistent',
  'group.pipeline': 'Pipeline',
  'group.outreach': 'Ansprache',
  'group.monitor': 'Überwachung',
  'group.system': 'System',

  'nav.agent': 'KI-Ingenieur',
  'nav.runs': 'E-Mail-Kampagnen',
  'nav.candidates': 'Kandidaten',
  'nav.matching': 'Kandidaten-Matching',
  'nav.outreach': 'Ansprache',
  'nav.dashboards': 'Dashboards',
  'nav.settings': 'Einstellungen',
  'nav.integrations': 'Integrationen',

  'badge.soon': 'Bald',
  'sidebar.workspace': 'Arbeitsbereich',
  'sidebar.freeTrial': 'Kostenlose Testphase',

  'topbar.search': 'Suchen…',
};

const DICTS: Record<Lang, Dict> = { en, de };

export type TKey = keyof typeof en;

// ── Context ─────────────────────────────────────────────────────────────────
interface I18nContextValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: TKey) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  // Start at 'en' on the server/first paint, then hydrate from localStorage to
  // avoid a hydration mismatch.
  const [lang, setLangState] = useState<Lang>('en');

  useEffect(() => {
    const saved = (typeof window !== 'undefined' && window.localStorage.getItem(STORAGE_KEY)) as Lang | null;
    if (saved === 'en' || saved === 'de') setLangState(saved);
  }, []);

  useEffect(() => {
    if (typeof document !== 'undefined') document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, l);
  }, []);

  const t = useCallback((key: TKey) => DICTS[lang][key] ?? en[key] ?? key, [lang]);

  return <I18nContext.Provider value={{ lang, setLang, t }}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error('useI18n must be used within <LanguageProvider>');
  return ctx;
}
