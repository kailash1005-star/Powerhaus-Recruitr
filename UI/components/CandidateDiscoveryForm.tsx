'use client';

import { useState } from 'react';
import { Icon } from './Icon';
import { discoverJobCandidates, type DiscoverFilters } from '@/lib/api';

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

const label: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)', marginBottom: 6, display: 'block' };
const field: React.CSSProperties = { width: '100%', height: 38, padding: '0 11px', borderRadius: 8, border: '1px solid var(--border-card)', fontSize: 14, fontFamily: 'inherit', background: '#FFF', boxSizing: 'border-box', color: 'var(--fg-primary)' };

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

export function CandidateDiscoveryForm({ pipelineId, jobId, jobTitle, jobLocation, companyName, onClose, onSubmitted }: Props) {
  const [f, setF] = useState<DiscoverFilters>({
    searchQuery: jobTitle || '',
    maxItems: 25,
    currentJobTitles: jobTitle ? [jobTitle] : [],
    locations: jobLocation ? [jobLocation] : [],
  });
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof DiscoverFilters>(k: K, v: DiscoverFilters[K]) => setF((p) => ({ ...p, [k]: v }));

  const submit = async () => {
    if (!f.searchQuery?.trim() && !(f.currentJobTitles?.length)) { setError('Add a search query or a current job title.'); return; }
    setBusy(true); setError(null);
    try {
      await discoverJobCandidates(pipelineId, jobId, f);
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

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'var(--bg-app, #F5F6FA)', zIndex: 100, display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 24px', borderBottom: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ width: 40, height: 40, borderRadius: 10, background: '#EEF2FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name="search" size={18} style={{ color: '#4F46E5' }} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>Discover candidates</div>
          <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>LinkedIn search for <b>{jobTitle}</b>{companyName ? ` · ${companyName}` : ''}</div>
        </div>
        <button onClick={onClose} style={{ width: 34, height: 34, border: 'none', background: 'transparent', borderRadius: 8, cursor: 'pointer', color: 'var(--fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Icon name="x" size={20} /></button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Essentials */}
          <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', marginBottom: 16 }}>Search</div>
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 14, marginBottom: 14 }}>
              <div>
                <label style={label}>Search query (fuzzy)</label>
                <input value={f.searchQuery || ''} onChange={(e) => set('searchQuery', e.target.value)} placeholder="e.g. AI Engineer" style={field} />
              </div>
              <div>
                <label style={label}>Max profiles</label>
                <input type="number" min={1} max={100} value={f.maxItems ?? 25} onChange={(e) => set('maxItems', Math.max(1, Math.min(100, parseInt(e.target.value) || 25)))} style={field} />
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div><label style={label}>Current job titles</label><TagInput value={f.currentJobTitles || []} onChange={(v) => set('currentJobTitles', v)} placeholder="Type + Enter" /></div>
              <div><label style={label}>Locations</label><TagInput value={f.locations || []} onChange={(v) => set('locations', v)} placeholder="e.g. Chennai" /></div>
            </div>
          </div>

          {/* Filters */}
          <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', marginBottom: 16 }}>Filters</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
              <div><label style={label}>Years of experience</label>{sel('yearsOfExperience', YEARS)}</div>
              <div><label style={label}>Seniority level</label>{sel('seniorityLevel', SENIORITY)}</div>
              <div><label style={label}>Function</label>{sel('function', FUNCTIONS)}</div>
              <div><label style={label}>Company headcount</label>{sel('companyHeadcount', HEADCOUNT)}</div>
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

          {/* Advanced */}
          <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, padding: 20 }}>
            <button onClick={() => setAdvanced((a) => !a)} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', padding: 0, width: '100%' }}>
              <Icon name={advanced ? 'chevron-down' : 'chevron-right'} size={16} /> Advanced &amp; exclusions
            </button>
            {advanced && (
              <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                <div><label style={label}>Current companies</label><TagInput value={f.currentCompanies || []} onChange={(v) => set('currentCompanies', v)} placeholder="Company name" /></div>
                <div><label style={label}>Past companies</label><TagInput value={f.pastCompanies || []} onChange={(v) => set('pastCompanies', v)} placeholder="Company name" /></div>
                <div><label style={label}>Past job titles</label><TagInput value={f.pastJobTitles || []} onChange={(v) => set('pastJobTitles', v)} placeholder="Title" /></div>
                <div><label style={label}>Schools</label><TagInput value={f.schools || []} onChange={(v) => set('schools', v)} placeholder="School" /></div>
                <div><label style={label}>Years at current company</label>{sel('yearsAtCurrentCompany', YEARS)}</div>
                <div><label style={label}>Company HQ locations</label><TagInput value={f.companyHqLocations || []} onChange={(v) => set('companyHqLocations', v)} placeholder="Location" /></div>
                <div><label style={label}>Industry IDs</label><TagInput value={f.industryIds || []} onChange={(v) => set('industryIds', v)} placeholder="LinkedIn industry id" /></div>
                <div><label style={label}>Exclude locations</label><TagInput value={f.excludeLocations || []} onChange={(v) => set('excludeLocations', v)} placeholder="Location" /></div>
                <div><label style={label}>Exclude current companies</label><TagInput value={f.excludeCurrentCompanies || []} onChange={(v) => set('excludeCurrentCompanies', v)} placeholder="Company" /></div>
                <div><label style={label}>Exclude past companies</label><TagInput value={f.excludePastCompanies || []} onChange={(v) => set('excludePastCompanies', v)} placeholder="Company" /></div>
                <div><label style={label}>Exclude current titles</label><TagInput value={f.excludeCurrentJobTitles || []} onChange={(v) => set('excludeCurrentJobTitles', v)} placeholder="Title" /></div>
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
                </div>
              </div>
            )}
          </div>

          {error && <div style={{ padding: '11px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 13, color: '#B91C1C' }}>{error}</div>}
        </div>
      </div>

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 24px', borderTop: '1px solid var(--border-default)', background: '#FFF', flexShrink: 0 }}>
        <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>
          Finds up to <b>{f.maxItems ?? 25}</b> LinkedIn profiles, then auto-enriches each (deep profile). Runs in the background.
        </div>
        <div style={{ flex: 1 }} />
        <button onClick={onClose} style={{ height: 40, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit' }}>Cancel</button>
        <button onClick={submit} disabled={busy} style={{ height: 40, padding: '0 22px', borderRadius: 8, fontSize: 14, fontWeight: 700, cursor: busy ? 'not-allowed' : 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: busy ? 0.7 : 1 }}>
          <Icon name={busy ? 'loader' : 'search'} size={16} /> {busy ? 'Starting…' : 'Run search'}
        </button>
      </div>
    </div>
  );
}
