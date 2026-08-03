'use client';

import { useState, useEffect } from 'react';
import { Icon } from './Icon';
import { LocationAutocomplete } from './LocationAutocomplete';
import {
  discoverCombined, suggestJobFilters,
  type DiscoverFilters, type SearchBrief, type SearchStrategy,
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
const LANGUAGES = ['Arabic', 'English', 'Spanish', 'Portuguese', 'Chinese', 'French', 'Italian', 'Russian', 'German', 'Dutch', 'Turkish', 'Tagalog', 'Polish', 'Korean', 'Japanese', 'Malay', 'Norwegian', 'Danish', 'Romanian', 'Swedish', 'Bahasa Indonesia', 'Czech'];

const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)', marginBottom: 6, display: 'block' };
const field: React.CSSProperties = { width: '100%', height: 38, padding: '0 11px', borderRadius: 8, border: '1px solid var(--border-card)', fontSize: 14, fontFamily: 'inherit', background: '#FFF', boxSizing: 'border-box', color: 'var(--fg-primary)' };
const card: React.CSSProperties = { background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, padding: 20 };

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

export function CandidateDiscoveryForm({ pipelineId, jobId, jobTitle, jobLocation, companyName, onClose, onSubmitted }: Props) {
  const [strategy, setStrategy] = useState<SearchStrategy | null>(null);
  const [f, setF] = useState<DiscoverFilters>({
    searchQuery: jobTitle || '',
    maxItems: 25,
    currentJobTitles: jobTitle ? [jobTitle] : [],
    locations: jobLocation ? [jobLocation] : [],
    autoBroaden: true,
  });
  const [skills, setSkills] = useState<string[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const [thinking, setThinking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof DiscoverFilters>(k: K, v: DiscoverFilters[K]) => setF((p) => ({ ...p, [k]: v }));

  const why = (name: string) => strategy?.rationale.find((r) => r.field === name)?.why;

  // Automatically request AI filter suggestions on mount
  useEffect(() => {
    let mounted = true;
    (async () => {
      setThinking(true);
      setError(null);
      try {
        const { strategy: s } = await suggestJobFilters(pipelineId, jobId, {});
        if (!mounted) return;
        setStrategy(s);
        setF((p) => ({ ...p, ...s.filters, maxItems: p.maxItems, autoBroaden: p.autoBroaden }));
        setSkills(s.apolloPlan?.qKeywords?.length ? s.apolloPlan.qKeywords : []);
      } catch (e: any) {
        if (!mounted) return;
        setError(e?.message || 'Could not generate suggestions — you can review & edit filters manually.');
      } finally {
        if (mounted) setThinking(false);
      }
    })();
    return () => { mounted = false; };
  }, [pipelineId, jobId]);

  const submit = async () => {
    if (!f.searchQuery?.trim() && !(f.currentJobTitles?.length)) {
      setError('Add a search query or at least one job title to search for.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await discoverCombined(pipelineId, jobId, {
        apify: {
          ...f,
          brief: { mustHaveSkills: skills },
          broadeningLadder: strategy?.broadeningLadder,
          domainAnchor: strategy?.domainAnchor,
          adjacentTitles: strategy?.adjacentTitles,
        },
        apollo: {},
        engines: { apify: true, apollo: false },
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

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'var(--bg-app, #F5F6FA)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 24px', borderBottom: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ width: 40, height: 40, borderRadius: 10, background: '#EEF2FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="search" size={18} style={{ color: '#4F46E5' }} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>
            Review the search
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>
            LinkedIn search for <b>{jobTitle}</b>{companyName ? ` · ${companyName}` : ''}
          </div>
        </div>
        <button onClick={onClose} style={{ width: 34, height: 34, border: 'none', background: 'transparent', borderRadius: 8, cursor: 'pointer', color: 'var(--fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="x" size={20} />
        </button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 18 }}>

          {/* AI Thinking Overlay */}
          {thinking && (
            <div style={{ ...card, background: 'linear-gradient(180deg,#FAFAFF,#FFF)', borderColor: '#C7D2FE', display: 'flex', alignItems: 'center', gap: 14, padding: '20px 24px' }}>
              <span style={{ width: 36, height: 36, borderRadius: 10, background: '#EEF2FF', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: '#4F46E5', flexShrink: 0 }}>
                <Icon name="loader" size={20} />
              </span>
              <div>
                <div style={{ fontSize: 14, fontWeight: 700, color: '#1E293B' }}>
                  Generating LinkedIn Search Strategy…
                </div>
                <div style={{ fontSize: 12.5, color: '#64748B', marginTop: 2 }}>
                  Analyzing job description for <b>{jobTitle}</b> and translating into real profile titles and Boolean search queries.
                </div>
              </div>
            </div>
          )}

          {/* AI Conclusion & Insights */}
          {!thinking && strategy && strategy.confidence > 0 && (
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
            </div>
          )}

          {/* ── Search targets ──────────────────────────────────────────── */}
          <div style={card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)' }}>Search targets</div>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 600, padding: '3px 9px', borderRadius: 999, background: '#EFF6FF', color: '#0A66C2' }}>
                <Icon name="linkedin" size={12} /> LinkedIn
              </span>
            </div>

            {/* The three axes a recruiter actually thinks in. Previously the
                domain axis was labelled "Search query (fuzzy)", which told
                nobody that it is the field carrying what the person WORKS ON
                — the only reliable signal when titles vary (a person selling
                SAP Retail may be titled Account Executive, Client Partner,
                or just Principal Consultant). */}
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14, marginBottom: 14 }}>
              <div>
                <label style={label}>
                  Domain <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>— what they work on</span>
                </label>
                <input value={f.searchQuery || ''} onChange={(e) => set('searchQuery', e.target.value)} placeholder="e.g. ('IT-Systemadministrator' OR 'System Administrator')" style={field} />
                <div style={{ fontSize: 11.5, color: 'var(--fg-muted)', marginTop: 4 }}>
                  Matched against the whole profile, not just the title. Supports
                  AND / OR / NOT and &quot;quoted phrases&quot;.
                </div>
                <Why text={why('searchQuery')} />
              </div>
              <div>
                <label style={label}>Max profiles</label>
                <input type="number" min={1} max={100} value={f.maxItems ?? 25} onChange={(e) => set('maxItems', Math.max(1, Math.min(100, parseInt(e.target.value) || 25)))} style={field} />
              </div>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={label}>
                Job titles <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>— what they do</span>
              </label>
              <TagInput value={f.currentJobTitles || []} onChange={(v) => set('currentJobTitles', v)} placeholder="Type + Enter" />
              <div style={{ fontSize: 11.5, color: 'var(--fg-muted)', marginTop: 4 }}>
                Narrows to these titles. Leave empty to search on the domain alone —
                useful when the right people title themselves inconsistently.
              </div>
              <Why text={why('currentJobTitles')} />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div>
                <label style={label}>
                  Locations <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>— where</span>
                </label>
                <LocationAutocomplete value={f.locations || []} onChange={(v) => set('locations', v)} placeholder="Type a city, e.g. Koblenz" />
                <Why text={why('locations')} />
              </div>
              <div>
                <label style={label}>Key skills <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>(1–3 that define the role)</span></label>
                <TagInput value={skills} onChange={setSkills} placeholder="e.g. SAP FICO · S/4HANA" />
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
          </div>

          {/* Agentic recovery */}
          <div style={{ ...card, padding: '16px 20px' }}>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, cursor: 'pointer' }}>
              <input type="checkbox" checked={f.autoBroaden !== false} onChange={(e) => set('autoBroaden', e.target.checked)} style={{ marginTop: 3 }} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>Keep trying if the search finds nobody</div>
                <div style={{ fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg-muted)', marginTop: 3 }}>
                  Instead of returning an empty list, the search relaxes its filters and tries again,
                  stopping as soon as it finds candidates. Each retry is a paid search, so it stops early once
                  the filters are broad enough that zero means nobody's there.
                </div>
              </div>
            </label>
          </div>

          {/* Advanced — LinkedIn inferred filters + exclusions (default Any) */}
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
                  <div><label style={label}>Industry IDs <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>(e.g. 4=Software, 96=IT, 43=Finance)</span></label><TagInput value={f.industryIds || []} onChange={(v) => set('industryIds', v)} placeholder="Code, e.g. 4" /><Why text={why('industryIds')} /></div>
                  <div><label style={label}>Exclude industry IDs</label><TagInput value={f.excludeIndustryIds || []} onChange={(v) => set('excludeIndustryIds', v)} placeholder="Code, e.g. 4" /></div>
                  <div><label style={label}>Current companies</label><TagInput value={f.currentCompanies || []} onChange={(v) => set('currentCompanies', v)} placeholder="Company name" /><Why text={why('currentCompanies')} /></div>
                  <div><label style={label}>Past companies</label><TagInput value={f.pastCompanies || []} onChange={(v) => set('pastCompanies', v)} placeholder="Company name" /></div>
                  <div><label style={label}>Past job titles</label><TagInput value={f.pastJobTitles || []} onChange={(v) => set('pastJobTitles', v)} placeholder="Title" /><Why text={why('pastJobTitles')} /></div>
                  <div><label style={label}>Schools</label><TagInput value={f.schools || []} onChange={(v) => set('schools', v)} placeholder="School" /></div>
                  <div><label style={label}>Years at current company</label>{sel('yearsAtCurrentCompany', YEARS)}</div>
                  <div><label style={label}>Company HQ locations</label><TagInput value={f.companyHqLocations || []} onChange={(v) => set('companyHqLocations', v)} placeholder="Location" /></div>
                  <div><label style={label}>Exclude locations</label><TagInput value={f.excludeLocations || []} onChange={(v) => set('excludeLocations', v)} placeholder="Location" /></div>
                  <div><label style={label}>Exclude current companies</label><TagInput value={f.excludeCurrentCompanies || []} onChange={(v) => set('excludeCurrentCompanies', v)} placeholder="Company" /></div>
                  <div><label style={label}>Exclude past companies</label><TagInput value={f.excludePastCompanies || []} onChange={(v) => set('excludePastCompanies', v)} placeholder="Company" /></div>
                  <div><label style={label}>Exclude current titles</label><TagInput value={f.excludeCurrentJobTitles || []} onChange={(v) => set('excludeCurrentJobTitles', v)} placeholder="Title" /><Why text={why('excludeCurrentJobTitles')} /></div>
                  <div><label style={label}>Exclude past titles</label><TagInput value={f.excludePastJobTitles || []} onChange={(v) => set('excludePastJobTitles', v)} placeholder="Title" /></div>
                  <div><label style={label}>Exclude schools</label><TagInput value={f.excludeSchools || []} onChange={(v) => set('excludeSchools', v)} placeholder="School" /></div>
                  <div><label style={label}>Exclude function</label>{sel('excludeFunction', FUNCTIONS)}</div>
                  <div><label style={label}>Exclude company HQ locations</label><TagInput value={f.excludeCompanyHqLocations || []} onChange={(v) => set('excludeCompanyHqLocations', v)} placeholder="Location" /></div>
                  <div style={{ gridColumn: '1 / -1' }}>
                    <label style={label}>Exclude seniority level <span style={{ fontWeight: 400, color: 'var(--fg-muted)' }}>— pick any number, e.g. hide entry-level AND executives at once</span></label>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
                      {SENIORITY.filter((o) => o.v).map((o) => {
                        const on = (f.excludeSeniorityLevel || []).includes(o.v);
                        return (
                          <button
                            key={o.v} type="button"
                            onClick={() => set('excludeSeniorityLevel', on
                              ? (f.excludeSeniorityLevel || []).filter((x) => x !== o.v)
                              : [...(f.excludeSeniorityLevel || []), o.v])}
                            style={{ padding: '5px 11px', borderRadius: 999, fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', border: on ? '1px solid var(--primary)' : '1px solid var(--border-card)', background: on ? 'var(--accent-soft, #EEF0FE)' : '#FFF', color: on ? 'var(--primary)' : 'var(--fg-secondary)' }}
                          >{o.t}</button>
                        );
                      })}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--fg-muted)', marginTop: 4 }}>
                      This is the one exclusion the AI never sets on its own — it's manual only, because it's LinkedIn's
                      classification of the person, not their title, so it's reliable enough to be worth setting by hand
                      when you want it, unlike the fields above.
                    </div>
                  </div>
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

          {errorBox}
        </div>
      </div>

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 24px', borderTop: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>
          Runs the LinkedIn search in the background, screens every result against the role, and QA-verifies the specialty before showing them.
        </div>
        <div style={{ flex: 1 }} />
        <button onClick={onClose} disabled={busy} style={{ height: 40, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit' }}>
          Cancel
        </button>
        <button onClick={submit} disabled={busy || thinking} style={{ height: 40, padding: '0 22px', borderRadius: 8, fontSize: 14, fontWeight: 700, cursor: busy || thinking ? 'not-allowed' : 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: busy || thinking ? 0.7 : 1 }}>
          <Icon name={busy ? 'loader' : 'search'} size={16} /> {busy ? 'Starting…' : 'Run search'}
        </button>
      </div>
    </div>
  );
}
