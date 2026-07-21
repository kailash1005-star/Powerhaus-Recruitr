'use client';

import { useState } from 'react';
import { Icon } from './Icon';
import {
  discoverCombined, suggestJobFilters,
  type ApolloDiscoverFilters, type DiscoverFilters, type SearchBrief, type SearchStrategy,
} from '@/lib/api';

interface Props {
  pipelineId: string;
  jobId: string;
  jobTitle: string;
  jobLocation?: string;
  companyName?: string;
  onClose: () => void;
  onSubmitted: () => void;
}

// Enum options are the actor's authoritative { code, title } pairs (from its
// input schema). The <select> value is the CODE the actor requires; the title
// is what the user sees. '' = Any.
type Opt = { v: string; t: string };
const YEARS: Opt[] = [
  { v: '', t: '' }, { v: '1', t: 'Less than 1 year' }, { v: '2', t: '1 to 2 years' },
  { v: '3', t: '3 to 5 years' }, { v: '4', t: '6 to 10 years' }, { v: '5', t: 'More than 10 years' },
];
const SENIORITY: Opt[] = [
  { v: '', t: '' }, { v: '110', t: 'Entry Level' }, { v: '120', t: 'Senior' }, { v: '130', t: 'Strategic' },
  { v: '200', t: 'Entry Level Manager' }, { v: '210', t: 'Experienced Manager' }, { v: '220', t: 'Director' },
  { v: '300', t: 'Vice President' }, { v: '310', t: 'CXO' }, { v: '320', t: 'Owner / Partner' }, { v: '100', t: 'In Training' },
];
const FUNCTIONS: Opt[] = [
  { v: '', t: '' }, { v: '1', t: 'Accounting' }, { v: '2', t: 'Administrative' }, { v: '3', t: 'Arts and Design' },
  { v: '4', t: 'Business Development' }, { v: '5', t: 'Community and Social Services' }, { v: '6', t: 'Consulting' },
  { v: '7', t: 'Education' }, { v: '8', t: 'Engineering' }, { v: '9', t: 'Entrepreneurship' }, { v: '10', t: 'Finance' },
  { v: '11', t: 'Healthcare Services' }, { v: '12', t: 'Human Resources' }, { v: '13', t: 'Information Technology' },
  { v: '14', t: 'Legal' }, { v: '15', t: 'Marketing' }, { v: '16', t: 'Media and Communication' },
  { v: '17', t: 'Military and Protective Services' }, { v: '18', t: 'Operations' }, { v: '19', t: 'Product Management' },
  { v: '20', t: 'Program and Project Management' }, { v: '21', t: 'Purchasing' }, { v: '22', t: 'Quality Assurance' },
  { v: '23', t: 'Real Estate' }, { v: '24', t: 'Research' }, { v: '25', t: 'Sales' }, { v: '26', t: 'Customer Success and Support' },
];
const HEADCOUNT: Opt[] = [
  { v: '', t: '' }, { v: 'A', t: 'Self-Employed' }, { v: 'B', t: '1-10' }, { v: 'C', t: '11-50' }, { v: 'D', t: '51-200' },
  { v: 'E', t: '201-500' }, { v: 'F', t: '501-1,000' }, { v: 'G', t: '1,001-5,000' }, { v: 'H', t: '5,001-10,000' }, { v: 'I', t: '10,001+' },
];
// profileLanguages enum (names sent verbatim to the actor).
const LANGUAGES = ['Arabic', 'English', 'Spanish', 'Portuguese', 'Chinese', 'French', 'Italian', 'Russian', 'German', 'Dutch', 'Turkish', 'Tagalog', 'Polish', 'Korean', 'Japanese', 'Malay', 'Norwegian', 'Danish', 'Romanian', 'Swedish', 'Bahasa Indonesia', 'Czech'];
const WORK_MODELS: Opt[] = [
  { v: '', t: 'Not specified' }, { v: 'onsite', t: 'Onsite' }, { v: 'hybrid', t: 'Hybrid' }, { v: 'remote', t: 'Remote' },
];
// Apollo person_seniorities enum — { code sent to Apollo, label shown }.
const APOLLO_SENIORITIES: { v: string; t: string }[] = [
  { v: 'owner', t: 'Owner' }, { v: 'founder', t: 'Founder' }, { v: 'c_suite', t: 'C-Suite' },
  { v: 'partner', t: 'Partner' }, { v: 'vp', t: 'VP' }, { v: 'head', t: 'Head' },
  { v: 'director', t: 'Director' }, { v: 'manager', t: 'Manager' }, { v: 'senior', t: 'Senior' },
  { v: 'entry', t: 'Entry' }, { v: 'intern', t: 'Intern' },
];

const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)', marginBottom: 6, display: 'block' };
const field: React.CSSProperties = { width: '100%', height: 38, padding: '0 11px', borderRadius: 8, border: '1px solid var(--border-card)', fontSize: 14, fontFamily: 'inherit', background: '#FFF', boxSizing: 'border-box', color: 'var(--fg-primary)' };
const card: React.CSSProperties = { background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, padding: 20 };
const cardTitle: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', marginBottom: 16 };

function TagInput({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState('');
  const add = () => { const t = text.trim(); if (t && !value.includes(t)) onChange([...value, t]); setText(''); };
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', minHeight: 38, padding: '5px 8px', borderRadius: 8, border: '1px solid var(--border-card)', background: '#FFF' }}>
      {value.map((v) => (
        <span key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: 'var(--accent-soft, #EEF0FE)', color: 'var(--primary)', borderRadius: 6, padding: '3px 8px', fontSize: 12.5, fontWeight: 600 }}>
          {v}
          <button onClick={() => onChange(value.filter((x) => x !== v))} style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--primary)', display: 'inline-flex', padding: 0 }}><Icon name="x" size={12} /></button>
        </span>
      ))}
      <input
        value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); } }}
        onBlur={add}
        placeholder={value.length ? '' : placeholder}
        style={{ flex: 1, minWidth: 90, border: 'none', outline: 'none', fontSize: 14, fontFamily: 'inherit', background: 'transparent', height: 26, color: 'var(--fg-primary)' }}
      />
    </div>
  );
}

/** The AI's one-line justification for a field, shown under its input. */
function Why({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <div style={{ display: 'flex', gap: 5, marginTop: 5, fontSize: 11.5, lineHeight: 1.45, color: 'var(--fg-muted)' }}>
      <Icon name="sparkles" size={11} style={{ color: '#7C3AED', flexShrink: 0, marginTop: 2 }} />
      <span>{text}</span>
    </div>
  );
}

/** The on/off toggle in each engine section header. */
function EngineToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button" role="switch" aria-checked={on}
      onClick={() => onChange(!on)}
      style={{ width: 42, height: 24, borderRadius: 999, border: 'none', cursor: 'pointer', padding: 2, background: on ? 'var(--primary)' : '#CBD5E1', transition: 'background .12s', flexShrink: 0 }}
    >
      <span style={{ display: 'block', width: 20, height: 20, borderRadius: '50%', background: '#FFF', transform: on ? 'translateX(18px)' : 'translateX(0)', transition: 'transform .12s', boxShadow: '0 1px 2px rgba(0,0,0,0.2)' }} />
    </button>
  );
}

/**
 * Unified candidate discovery — ONE screen for both engines.
 *
 * Step 1 collects an optional brief; step 2 shows the AI's focus title and lets
 * the recruiter review/edit the LinkedIn (Apify) and Apollo inputs the Strategist
 * proposed for each engine, toggle either off, and fire them CONCURRENTLY. The
 * four LinkedIn-inferred filters (years / seniority / function / headcount) live
 * under Advanced and default to Any — they narrow on sparse LinkedIn-derived data
 * and hurt recall, so the AI only pre-fills one when the JD clearly supports it.
 */
export function CandidateDiscoveryForm({ pipelineId, jobId, jobTitle, jobLocation, companyName, onClose, onSubmitted }: Props) {
  // 'brief' = tell the AI about the role; 'filters' = review what it proposed.
  const [step, setStep] = useState<'brief' | 'filters'>('brief');
  const [brief, setBrief] = useState<SearchBrief>({});
  const [strategy, setStrategy] = useState<SearchStrategy | null>(null);
  // Apify (LinkedIn) filters.
  const [f, setF] = useState<DiscoverFilters>({
    searchQuery: jobTitle || '',
    maxItems: 25,
    currentJobTitles: jobTitle ? [jobTitle] : [],
    locations: jobLocation ? [jobLocation] : [],
    autoBroaden: true,
  });
  // Apollo people-search filters.
  const [af, setAf] = useState<ApolloDiscoverFilters>({
    titles: jobTitle ? [jobTitle] : [],
    locations: jobLocation ? [jobLocation] : [],
    skills: [],
    seniorities: [],
  });
  const [engines, setEngines] = useState<{ apify: boolean; apollo: boolean }>({ apify: true, apollo: true });
  const [advanced, setAdvanced] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof DiscoverFilters>(k: K, v: DiscoverFilters[K]) => setF((p) => ({ ...p, [k]: v }));
  const setA = <K extends keyof ApolloDiscoverFilters>(k: K, v: ApolloDiscoverFilters[K]) => setAf((p) => ({ ...p, [k]: v }));
  const setB = <K extends keyof SearchBrief>(k: K, v: SearchBrief[K]) => setBrief((p) => ({ ...p, [k]: v }));

  // field name → the AI's reason for it, for the inline Why() hints.
  const why = (name: string) => strategy?.rationale.find((r) => r.field === name)?.why;

  /** Ask the Strategist to propose filters for BOTH engines, then move to review. */
  const analyze = async () => {
    setThinking(true); setError(null);
    try {
      const { strategy: s } = await suggestJobFilters(pipelineId, jobId, brief);
      setStrategy(s);
      // The proposal replaces the literal prefill, but maxItems/autoBroaden are
      // the recruiter's controls, not the AI's — carry them over.
      setF((p) => ({ ...p, ...s.filters, maxItems: p.maxItems, autoBroaden: p.autoBroaden }));
      setAf((p) => ({
        ...p,
        titles: s.apolloPlan?.titles?.length ? s.apolloPlan.titles : (p.titles || []),
        skills: s.apolloPlan?.qKeywords || [],
        locations: s.apolloPlan?.locations?.length ? s.apolloPlan.locations : (p.locations || []),
        seniorities: s.apolloPlan?.seniorities || [],
      }));
      setStep('filters');
    } catch (e: any) {
      setError(e?.message || 'Could not generate suggestions — you can still search manually.');
    } finally {
      setThinking(false);
    }
  };

  const submit = async () => {
    if (!engines.apify && !engines.apollo) { setError('Turn on at least one search engine.'); return; }
    if (engines.apify && !f.searchQuery?.trim() && !(f.currentJobTitles?.length)) {
      setError('LinkedIn needs a search query or a current job title (or switch it off).'); return;
    }
    if (engines.apollo && !(af.titles?.length) && !(af.skills?.length)) {
      setError('Apollo needs a job title or a key skill (or switch it off).'); return;
    }
    setBusy(true); setError(null);
    try {
      await discoverCombined(pipelineId, jobId, {
        // The Apify block carries the brief + ladder + anchor so the recovery
        // loop has the role's intent, and the adjacent titles for a thin result.
        apify: {
          ...f, brief,
          broadeningLadder: strategy?.broadeningLadder,
          domainAnchor: strategy?.domainAnchor,
          adjacentTitles: strategy?.adjacentTitles,
        },
        apollo: af,
        engines,
      });
      onSubmitted();
    } catch (e: any) {
      setError(e?.message || 'Failed to start discovery');
      setBusy(false);
    }
  };

  const sel = (k: keyof DiscoverFilters, opts: Opt[]) => (
    <select value={(f[k] as string) || ''} onChange={(e) => set(k, (e.target.value || undefined) as any)} style={{ ...field, cursor: 'pointer' }}>
      {opts.map((o) => <option key={o.v} value={o.v}>{o.t || 'Any'}</option>)}
    </select>
  );

  const errorBox = error && (
    <div style={{ padding: '11px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 13, color: '#B91C1C' }}>{error}</div>
  );

  const engineHeader = (opts: { icon: string; color: string; bg: string; title: string; subtitle: string; on: boolean; onToggle: (v: boolean) => void }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 11, marginBottom: opts.on ? 16 : 0 }}>
      <span style={{ width: 32, height: 32, borderRadius: 8, background: opts.bg, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, opacity: opts.on ? 1 : 0.5 }}>
        <Icon name={opts.icon} size={17} style={{ color: opts.color }} />
      </span>
      <div style={{ flex: 1, minWidth: 0, opacity: opts.on ? 1 : 0.6 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--fg-primary)' }}>{opts.title}</div>
        <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{opts.subtitle}</div>
      </div>
      <EngineToggle on={opts.on} onChange={opts.onToggle} />
    </div>
  );

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'var(--bg-app, #F5F6FA)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 24px', borderBottom: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ width: 40, height: 40, borderRadius: 10, background: '#EEF2FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={step === 'brief' ? 'sparkles' : 'search'} size={18} style={{ color: '#4F46E5' }} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>
            {step === 'brief' ? 'Tell the AI about this role' : 'Review the search'}
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>LinkedIn + Apollo for <b>{jobTitle}</b>{companyName ? ` · ${companyName}` : ''}</div>
        </div>
        <button onClick={onClose} style={{ width: 34, height: 34, border: 'none', background: 'transparent', borderRadius: 8, cursor: 'pointer', color: 'var(--fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="x" size={20} /></button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 18 }}>

          {step === 'brief' ? (
            <>
              <div style={{ ...card, background: 'linear-gradient(180deg,#FAFAFF,#FFF)', borderColor: '#DDD6FE' }}>
                <div style={{ display: 'flex', gap: 11 }}>
                  <Icon name="sparkles" size={16} style={{ color: '#7C3AED', flexShrink: 0, marginTop: 2 }} />
                  <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--fg-secondary)' }}>
                    A job title like <b>{jobTitle}</b> is written in employer language — often nobody actually
                    carries it as their title, which is why searches come back empty. The AI reads the job
                    description, translates it into the titles real people use, and proposes inputs for
                    <b> both LinkedIn and Apollo</b> — which are then searched together.
                    <div style={{ marginTop: 7, color: 'var(--fg-muted)' }}>
                      Everything below is optional. Anything you add makes the suggestions sharper.
                    </div>
                  </div>
                </div>
              </div>

              <div style={card}>
                <div style={cardTitle}>The role</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label style={label}>Seniority you want</label>
                    <input value={brief.seniorityHint || ''} onChange={(e) => setB('seniorityHint', e.target.value)} placeholder="e.g. Senior, or Head of" style={field} />
                  </div>
                  <div>
                    <label style={label}>Minimum years of experience</label>
                    <input type="number" min={0} max={40} value={brief.minYears ?? ''} onChange={(e) => setB('minYears', e.target.value ? parseFloat(e.target.value) : undefined)} placeholder="e.g. 6" style={field} />
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                  <div><label style={label}>Must-have skills</label><TagInput value={brief.mustHaveSkills || []} onChange={(v) => setB('mustHaveSkills', v)} placeholder="e.g. SAP FICO" /></div>
                  <div><label style={label}>Nice-to-have skills</label><TagInput value={brief.niceToHaveSkills || []} onChange={(v) => setB('niceToHaveSkills', v)} placeholder="Type + Enter" /></div>
                </div>
              </div>

              <div style={card}>
                <div style={cardTitle}>Where to look</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label style={label}>Target companies (poach from)</label>
                    <TagInput value={brief.targetCompanies || []} onChange={(v) => setB('targetCompanies', v)} placeholder="Competitor name" />
                  </div>
                  <div>
                    <label style={label}>Companies to avoid</label>
                    <TagInput value={brief.excludeCompanies || []} onChange={(v) => setB('excludeCompanies', v)} placeholder="Company name" />
                  </div>
                  <div><label style={label}>Target industries</label><TagInput value={brief.targetIndustries || []} onChange={(v) => setB('targetIndustries', v)} placeholder="e.g. Manufacturing" /></div>
                  <div><label style={label}>Languages required</label><TagInput value={brief.languages || []} onChange={(v) => setB('languages', v)} placeholder="e.g. German" /></div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, alignItems: 'end' }}>
                  <div>
                    <label style={label}>Work model</label>
                    <select value={brief.workModel || ''} onChange={(e) => setB('workModel', e.target.value as any)} style={{ ...field, cursor: 'pointer' }}>
                      {WORK_MODELS.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
                    </select>
                  </div>
                  <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--fg-secondary)', cursor: 'pointer', height: 38 }}>
                    <input type="checkbox" checked={!!brief.openToRelocation} onChange={(e) => setB('openToRelocation', e.target.checked || undefined)} />
                    Candidates may relocate
                  </label>
                </div>
              </div>

              <div style={card}>
                <div style={cardTitle}>Anything else?</div>
                <textarea
                  value={brief.notes || ''} onChange={(e) => setB('notes', e.target.value)}
                  placeholder="Context the job description doesn't capture — e.g. 'the last hire came from a Big 4 consultancy', or 'avoid pure support profiles'."
                  style={{ ...field, height: 84, padding: '10px 11px', resize: 'vertical', lineHeight: 1.5 }}
                />
              </div>

              {errorBox}
            </>
          ) : (
            <>
              {/* What the AI concluded */}
              {strategy && strategy.confidence > 0 && (
                <div style={{ ...card, background: 'linear-gradient(180deg,#FAFAFF,#FFF)', borderColor: '#DDD6FE' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 11 }}>
                    <Icon name="sparkles" size={15} style={{ color: '#7C3AED' }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)' }}>{strategy.focusTitle || strategy.interpretedRole}</div>
                      {strategy.interpretedRole && strategy.focusTitle && strategy.interpretedRole !== strategy.focusTitle && (
                        <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{strategy.interpretedRole}</div>
                      )}
                    </div>
                    <span title="How confident the AI is in these filters" style={{ fontSize: 11.5, fontWeight: 700, padding: '3px 9px', borderRadius: 999, background: strategy.confidence >= 0.7 ? '#DCFCE7' : strategy.confidence >= 0.4 ? '#FEF3C7' : '#FEE2E2', color: strategy.confidence >= 0.7 ? '#166534' : strategy.confidence >= 0.4 ? '#92400E' : '#991B1B' }}>
                      {Math.round(strategy.confidence * 100)}% confident
                    </span>
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--fg-secondary)' }}>{strategy.titleReasoning}</div>
                  {strategy.warnings.length > 0 && (
                    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {strategy.warnings.map((w, i) => (
                        <div key={i} style={{ display: 'flex', gap: 7, fontSize: 12.5, lineHeight: 1.5, color: '#92400E' }}>
                          <Icon name="alert-triangle" size={12} style={{ flexShrink: 0, marginTop: 3 }} /> {w}
                        </div>
                      ))}
                    </div>
                  )}
                  {strategy.broadeningLadder.length > 0 && (
                    <details style={{ marginTop: 13 }}>
                      <summary style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--primary)', cursor: 'pointer' }}>
                        If this finds nobody, the AI will try {strategy.broadeningLadder.length} broader searches
                      </summary>
                      <ol style={{ margin: '10px 0 0', paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {strategy.broadeningLadder.map((s) => (
                          <li key={s.step} style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg-muted)' }}>{s.detail || s.action}</li>
                        ))}
                      </ol>
                      <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.5, color: 'var(--fg-muted)' }}>
                        Retries widen the net (seniority, location, language) — the job titles above are never
                        changed without your say-so.
                      </div>
                    </details>
                  )}
                  {(strategy.adjacentTitles?.length ?? 0) > 0 && (
                    <div style={{ marginTop: 12, fontSize: 12.5, lineHeight: 1.6, color: 'var(--fg-muted)' }}>
                      <b style={{ color: 'var(--fg-secondary)' }}>Held in reserve (searched only if you approve):</b>{' '}
                      {strategy.adjacentTitles.join(' · ')} — neighbouring specialties, offered as one-click
                      options if the exact-specialty pool turns out thin.
                    </div>
                  )}
                </div>
              )}

              {strategy && strategy.confidence === 0 && (
                <div style={{ padding: '11px 14px', borderRadius: 8, background: '#FEF3C7', border: '1px solid #FDE68A', fontSize: 13, lineHeight: 1.5, color: '#92400E' }}>
                  {strategy.titleReasoning} Review the inputs below before searching.
                </div>
              )}

              {/* ── LinkedIn (Apify) ──────────────────────────────────────── */}
              <div style={{ ...card, opacity: engines.apify ? 1 : 0.85 }}>
                {engineHeader({
                  icon: 'linkedin', color: '#0A66C2', bg: '#EFF6FF',
                  title: 'LinkedIn (Apify)', subtitle: 'Exact-title, LinkedIn-native sourcing · deep profiles',
                  on: engines.apify, onToggle: (v) => setEngines((p) => ({ ...p, apify: v })),
                })}
                {engines.apify && (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14, marginBottom: 14 }}>
                      <div>
                        <label style={label}>Search query (fuzzy)</label>
                        <input value={f.searchQuery || ''} onChange={(e) => set('searchQuery', e.target.value)} placeholder="e.g. SAP EWM" style={field} />
                        <Why text={why('searchQuery')} />
                      </div>
                      <div>
                        <label style={label}>Max profiles</label>
                        <input type="number" min={1} max={100} value={f.maxItems ?? 25} onChange={(e) => set('maxItems', Math.max(1, Math.min(100, parseInt(e.target.value) || 25)))} style={field} />
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                      <div>
                        <label style={label}>Current job titles</label>
                        <TagInput value={f.currentJobTitles || []} onChange={(v) => set('currentJobTitles', v)} placeholder="Type + Enter" />
                        <Why text={why('currentJobTitles')} />
                      </div>
                      <div>
                        <label style={label}>Locations</label>
                        <TagInput value={f.locations || []} onChange={(v) => set('locations', v)} placeholder="e.g. Koblenz, Germany" />
                        <Why text={why('locations')} />
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: 20, marginTop: 16, flexWrap: 'wrap' }}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--fg-secondary)', cursor: 'pointer' }}>
                        <input type="checkbox" checked={!!f.recentlyChangedJobs} onChange={(e) => set('recentlyChangedJobs', e.target.checked || undefined)} /> Recently changed jobs
                      </label>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--fg-secondary)', cursor: 'pointer' }}>
                        <input type="checkbox" checked={!!f.recentlyPostedOnLinkedin} onChange={(e) => set('recentlyPostedOnLinkedin', e.target.checked || undefined)} /> Recently posted on LinkedIn
                      </label>
                    </div>
                  </>
                )}
              </div>

              {/* ── Apollo ────────────────────────────────────────────────── */}
              <div style={{ ...card, opacity: engines.apollo ? 1 : 0.85 }}>
                {engineHeader({
                  icon: 'users', color: '#7C3AED', bg: '#F5F3FF',
                  title: 'Apollo', subtitle: 'Fast, broad people-search · contact revealed on demand',
                  on: engines.apollo, onToggle: (v) => setEngines((p) => ({ ...p, apollo: v })),
                })}
                {engines.apollo && (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                      <div><label style={label}>Job titles</label><TagInput value={af.titles || []} onChange={(v) => setA('titles', v)} placeholder="Type + Enter" /></div>
                      <div><label style={label}>Locations</label><TagInput value={af.locations || []} onChange={(v) => setA('locations', v)} placeholder="e.g. Germany" /></div>
                    </div>
                    <div style={{ marginBottom: 14 }}>
                      <label style={label}>Key skills <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>(matched as keywords — keep to the 1–3 that define the role)</span></label>
                      <TagInput value={af.skills || []} onChange={(v) => setA('skills', v)} placeholder="e.g. SAP EWM · SAP LES" />
                    </div>
                    <div>
                      <label style={label}>Seniority <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>— optional</span></label>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                        {APOLLO_SENIORITIES.map((s) => {
                          const on = (af.seniorities || []).includes(s.v);
                          return (
                            <button
                              key={s.v} type="button"
                              onClick={() => setA('seniorities', on ? (af.seniorities || []).filter((x) => x !== s.v) : [...(af.seniorities || []), s.v])}
                              style={{ padding: '5px 11px', borderRadius: 999, fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', border: on ? '1px solid var(--primary)' : '1px solid var(--border-card)', background: on ? 'var(--accent-soft, #EEF0FE)' : '#FFF', color: on ? 'var(--primary)' : 'var(--fg-secondary)' }}
                            >{s.t}</button>
                          );
                        })}
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Agentic recovery (applies to both engines) */}
              <div style={{ ...card, padding: '16px 20px' }}>
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
                  <input type="checkbox" checked={f.autoBroaden !== false} onChange={(e) => set('autoBroaden', e.target.checked)} style={{ marginTop: 3 }} />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>Keep trying if the search finds nobody</div>
                    <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg-muted)', marginTop: 3 }}>
                      Instead of returning an empty list, each engine relaxes its filters and searches again,
                      stopping as soon as it finds candidates. Each retry is a paid search, so it stops early once
                      the filters are broad enough that zero means nobody's there.
                    </div>
                  </div>
                </label>
              </div>

              {/* Advanced — incl. the LinkedIn inferred filters (default Any) */}
              {engines.apify && (
                <div style={card}>
                  <button onClick={() => setAdvanced((a) => !a)} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', padding: 0, width: '100%' }}>
                    <Icon name={advanced ? 'chevron-down' : 'chevron-right'} size={16} /> Advanced — narrow filters &amp; exclusions <span style={{ fontWeight: 400, color: 'var(--fg-muted)', fontSize: 12 }}>(LinkedIn)</span>
                  </button>
                  {advanced && (
                    <>
                      <div style={{ marginTop: 16, padding: '10px 12px', borderRadius: 8, background: '#FFFBEB', border: '1px solid #FDE68A', fontSize: 12, lineHeight: 1.5, color: '#92400E' }}>
                        These four are LinkedIn-<b>inferred</b> and often blank or wrong, so each one you set silently
                        drops matching people. Leave them <b>Any</b> unless you really need to narrow — the AI only
                        pre-fills one when the role clearly calls for it.
                      </div>
                      <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                        <div><label style={label}>Years of experience</label>{sel('yearsOfExperience', YEARS)}<Why text={why('yearsOfExperience')} /></div>
                        <div><label style={label}>Seniority level</label>{sel('seniorityLevel', SENIORITY)}<Why text={why('seniorityLevel')} /></div>
                        <div><label style={label}>Function</label>{sel('function', FUNCTIONS)}<Why text={why('function')} /></div>
                        <div><label style={label}>Company headcount</label>{sel('companyHeadcount', HEADCOUNT)}<Why text={why('companyHeadcount')} /></div>
                      </div>
                      <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                        <div><label style={label}>Current companies</label><TagInput value={f.currentCompanies || []} onChange={(v) => set('currentCompanies', v)} placeholder="Company name" /><Why text={why('currentCompanies')} /></div>
                        <div><label style={label}>Past companies</label><TagInput value={f.pastCompanies || []} onChange={(v) => set('pastCompanies', v)} placeholder="Company name" /></div>
                        <div><label style={label}>Past job titles</label><TagInput value={f.pastJobTitles || []} onChange={(v) => set('pastJobTitles', v)} placeholder="Title" /><Why text={why('pastJobTitles')} /></div>
                        <div><label style={label}>Schools</label><TagInput value={f.schools || []} onChange={(v) => set('schools', v)} placeholder="School" /></div>
                        <div><label style={label}>Years at current company</label>{sel('yearsAtCurrentCompany', YEARS)}</div>
                        <div><label style={label}>Company HQ locations</label><TagInput value={f.companyHqLocations || []} onChange={(v) => set('companyHqLocations', v)} placeholder="Location" /></div>
                        <div><label style={label}>Industry IDs</label><TagInput value={f.industryIds || []} onChange={(v) => set('industryIds', v)} placeholder="LinkedIn industry id" /></div>
                        <div><label style={label}>Exclude locations</label><TagInput value={f.excludeLocations || []} onChange={(v) => set('excludeLocations', v)} placeholder="Location" /></div>
                        <div><label style={label}>Exclude current companies</label><TagInput value={f.excludeCurrentCompanies || []} onChange={(v) => set('excludeCurrentCompanies', v)} placeholder="Company" /></div>
                        <div><label style={label}>Exclude past companies</label><TagInput value={f.excludePastCompanies || []} onChange={(v) => set('excludePastCompanies', v)} placeholder="Company" /></div>
                        <div><label style={label}>Exclude current titles</label><TagInput value={f.excludeCurrentJobTitles || []} onChange={(v) => set('excludeCurrentJobTitles', v)} placeholder="Title" /><Why text={why('excludeCurrentJobTitles')} /></div>
                        <div><label style={label}>Exclude past titles</label><TagInput value={f.excludePastJobTitles || []} onChange={(v) => set('excludePastJobTitles', v)} placeholder="Title" /></div>
                        <div><label style={label}>Exclude schools</label><TagInput value={f.excludeSchools || []} onChange={(v) => set('excludeSchools', v)} placeholder="School" /></div>
                        <div><label style={label}>Exclude industry IDs</label><TagInput value={f.excludeIndustryIds || []} onChange={(v) => set('excludeIndustryIds', v)} placeholder="LinkedIn industry id" /></div>
                        <div><label style={label}>Exclude seniority</label>{sel('excludeSeniorityLevel', SENIORITY)}</div>
                        <div><label style={label}>Exclude function</label>{sel('excludeFunction', FUNCTIONS)}</div>
                        <div style={{ gridColumn: '1 / -1' }}>
                          <label style={label}>Profile languages</label>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                            {LANGUAGES.map((lng) => {
                              const on = (f.profileLanguages || []).includes(lng);
                              return (
                                <button
                                  key={lng} type="button"
                                  onClick={() => set('profileLanguages', on ? (f.profileLanguages || []).filter((x) => x !== lng) : [...(f.profileLanguages || []), lng])}
                                  style={{ padding: '5px 11px', borderRadius: 999, fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', border: on ? '1px solid var(--primary)' : '1px solid var(--border-card)', background: on ? 'var(--accent-soft, #EEF0FE)' : '#FFF', color: on ? 'var(--primary)' : 'var(--fg-secondary)' }}
                                >{lng}</button>
                              );
                            })}
                          </div>
                          <Why text={why('profileLanguages')} />
                        </div>
                      </div>
                    </>
                  )}
                </div>
              )}

              {errorBox}
            </>
          )}
        </div>
      </div>

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 24px', borderTop: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>
          {step === 'brief'
            ? 'Reading the job description costs nothing and finds no candidates yet — you review everything before the search runs.'
            : <>Runs {engines.apify && engines.apollo ? 'LinkedIn + Apollo together' : engines.apify ? 'LinkedIn' : engines.apollo ? 'Apollo' : 'nothing — turn on an engine'} in the background, then merges the results.</>}
        </div>
        <div style={{ flex: 1 }} />
        {step === 'brief' ? (
          <>
            <button onClick={() => setStep('filters')} disabled={thinking} style={{ height: 40, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: thinking ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit' }}>Skip, I'll filter myself</button>
            <button onClick={analyze} disabled={thinking} style={{ height: 40, padding: '0 22px', borderRadius: 8, fontSize: 14, fontWeight: 700, cursor: thinking ? 'not-allowed' : 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: thinking ? 0.7 : 1 }}>
              <Icon name={thinking ? 'loader' : 'sparkles'} size={16} /> {thinking ? 'Reading the role…' : 'Suggest filters'}
            </button>
          </>
        ) : (
          <>
            <button onClick={() => setStep('brief')} disabled={busy} style={{ height: 40, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit' }}>Back</button>
            <button onClick={submit} disabled={busy} style={{ height: 40, padding: '0 22px', borderRadius: 8, fontSize: 14, fontWeight: 700, cursor: busy ? 'not-allowed' : 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: busy ? 0.7 : 1 }}>
              <Icon name={busy ? 'loader' : 'search'} size={16} /> {busy ? 'Starting…' : 'Run search'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
