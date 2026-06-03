'use client';

import { useState, useEffect, useCallback } from 'react';
import { Icon } from './Icon';
import {
  fetchPipelines, createPipeline, addJobToPipeline,
  type Pipeline, type RunJob,
} from '@/lib/api';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  job: RunJob | null;
  /** The company doc fields backing this job (passed in so we can prefill). */
  companyDefaults?: {
    companyName?: string;
    companyDomain?: string;
    companyIndustry?: string;
    matchedIndustry?: string | null;
    companyLocation?: string;
    linkedinSlug?: string | null;
    website?: string;
  };
  /** Called after the job is added so the caller can refresh. */
  onAdded: (pipelineId: string) => void;
}

type Mode = 'existing' | 'create';

export function AddToPipelineModal({ isOpen, onClose, job, companyDefaults, onAdded }: Props) {
  const [mode, setMode] = useState<Mode>('existing');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Pipeline[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [form, setForm] = useState({
    companyName: '', companyDomain: '', companyIndustry: '',
    matchedIndustry: '', companyLocation: '', linkedinSlug: '', website: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset / prefill on open
  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setBusy(false);
    setSelectedId(null);
    setQuery(companyDefaults?.companyName || '');
    setForm({
      companyName: companyDefaults?.companyName || '',
      companyDomain: companyDefaults?.companyDomain || '',
      companyIndustry: companyDefaults?.companyIndustry || '',
      matchedIndustry: companyDefaults?.matchedIndustry || '',
      companyLocation: companyDefaults?.companyLocation || '',
      linkedinSlug: companyDefaults?.linkedinSlug || '',
      website: companyDefaults?.website || '',
    });
    setMode('existing');
  }, [isOpen, companyDefaults]);

  // Typeahead debounce
  useEffect(() => {
    if (!isOpen || mode !== 'existing') return;
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const r = await fetchPipelines(1, 8, query || undefined);
        setResults(r.pipelines);
        // Auto-select an exact-domain match if the recruiter's company already has a pipeline
        const target = (companyDefaults?.companyDomain || '').toLowerCase();
        const exact = target ? r.pipelines.find((p) => p.companyDomain.toLowerCase() === target) : null;
        if (exact) setSelectedId(exact._id);
      } catch (e: any) {
        setError(e.message);
      } finally {
        setSearching(false);
      }
    }, 200);
    return () => clearTimeout(t);
  }, [isOpen, mode, query, companyDefaults?.companyDomain]);

  const submitExisting = useCallback(async () => {
    if (!job || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await addJobToPipeline(selectedId, job._id);
      onAdded(selectedId);
    } catch (e: any) {
      setError(e.message || 'Failed to add job');
    } finally {
      setBusy(false);
    }
  }, [job, selectedId, onAdded]);

  const submitCreate = useCallback(async () => {
    if (!job) return;
    if (!form.companyName.trim() || !form.companyDomain.trim()) {
      setError('Company name and domain are required');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const pipeline = await createPipeline({
        companyName: form.companyName.trim(),
        companyDomain: form.companyDomain.trim(),
        companyIndustry: form.companyIndustry || undefined,
        matchedIndustry: form.matchedIndustry || undefined,
        companyLocation: form.companyLocation || undefined,
        linkedinSlug: form.linkedinSlug || undefined,
        website: form.website || undefined,
      });
      await addJobToPipeline(pipeline._id, job._id);
      onAdded(pipeline._id);
    } catch (e: any) {
      const msg = e?.message || '';
      // Backend returns {error: "pipeline_already_exists", pipelineId} on conflict
      try {
        const parsed = JSON.parse(msg.split(' → ')[0] || msg);
        if (parsed?.detail?.error === 'pipeline_already_exists') {
          setError('A pipeline already exists for that company. Switch to "Existing pipeline" to use it.');
          setBusy(false);
          return;
        }
      } catch {}
      setError(msg || 'Failed to create pipeline');
    } finally {
      setBusy(false);
    }
  }, [job, form, onAdded]);

  if (!isOpen || !job) return null;

  return (
    <div onClick={() => (busy ? null : onClose())} style={{
      position: 'fixed', inset: 0, zIndex: 70, background: 'rgba(0,0,0,0.4)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: '100%', maxWidth: 540, background: '#FFF', borderRadius: 12, padding: 24,
        boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 36, height: 36, borderRadius: 9999,
            background: 'var(--fg-primary)1A', color: 'var(--fg-primary)',
          }}><Icon name="user-plus" size={18} /></span>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>Add to candidate pipeline</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{job.title} · {job.company}</div>
          </div>
        </div>

        {/* Mode chips */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          {(['existing', 'create'] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              style={{
                padding: '6px 12px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                cursor: 'pointer',
                border: '1px solid',
                borderColor: mode === m ? 'var(--fg-primary)' : 'var(--border-card)',
                background: mode === m ? 'var(--fg-primary)' : 'var(--bg-app)',
                color: mode === m ? '#FFF' : 'var(--fg-secondary)',
                fontFamily: 'inherit',
              }}
            >
              {m === 'existing' ? 'Existing pipeline' : 'Create new pipeline'}
            </button>
          ))}
        </div>

        {mode === 'existing' ? (
          <>
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by company name or domain…"
              style={{
                width: '100%', height: 36, padding: '0 12px', fontSize: 13,
                border: '1px solid var(--border-card)', borderRadius: 6, outline: 'none',
                fontFamily: 'inherit', boxSizing: 'border-box',
              }}
            />
            <div style={{ maxHeight: 260, overflow: 'auto', marginTop: 10, border: '1px solid var(--border-card)', borderRadius: 8 }}>
              {searching ? (
                <div style={{ padding: 20, textAlign: 'center', color: 'var(--fg-muted)' }}>
                  <Icon name="loader" size={16} />
                </div>
              ) : results.length === 0 ? (
                <div style={{ padding: 20, textAlign: 'center', fontSize: 13, color: 'var(--fg-muted)' }}>
                  No matching pipelines. Switch to <strong>Create new</strong>.
                </div>
              ) : results.map((p) => (
                <div
                  key={p._id}
                  onClick={() => setSelectedId(p._id)}
                  style={{
                    padding: '10px 12px', cursor: 'pointer',
                    borderBottom: '1px solid var(--border-card)',
                    background: selectedId === p._id ? '#F3F4F6' : '#FFF',
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{p.companyName}</div>
                  <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                    {p.companyDomain} · {p.jobs.length} job{p.jobs.length !== 1 ? 's' : ''} · {p.acceptedCount} accepted
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Field label="Company name *" value={form.companyName} onChange={(v) => setForm({ ...form, companyName: v })} />
            <Field label="Company domain *" value={form.companyDomain} onChange={(v) => setForm({ ...form, companyDomain: v })} placeholder="example.com" />
            <Field label="Industry" value={form.companyIndustry} onChange={(v) => setForm({ ...form, companyIndustry: v })} />
            <Field label="Matched industry (for candidate search)" value={form.matchedIndustry} onChange={(v) => setForm({ ...form, matchedIndustry: v })} />
            <Field label="Location (HQ)" value={form.companyLocation} onChange={(v) => setForm({ ...form, companyLocation: v })} />
            <Field label="LinkedIn slug" value={form.linkedinSlug} onChange={(v) => setForm({ ...form, linkedinSlug: v })} />
            <Field label="Website" value={form.website} onChange={(v) => setForm({ ...form, website: v })} />
          </div>
        )}

        {error && (
          <div style={{
            marginTop: 12, padding: '10px 12px', fontSize: 12, color: '#B91C1C',
            background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 6,
          }}>{error}</div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 16 }}>
          <button onClick={onClose} disabled={busy} style={{
            height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500,
            cursor: busy ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)',
            background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
          }}>Cancel</button>
          <button
            onClick={mode === 'existing' ? submitExisting : submitCreate}
            disabled={busy || (mode === 'existing' && !selectedId)}
            style={{
              height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
              cursor: busy ? 'not-allowed' : 'pointer', border: 'none',
              background: 'var(--fg-primary)', color: '#FFF', fontFamily: 'inherit',
              display: 'inline-flex', alignItems: 'center', gap: 6,
              opacity: (busy || (mode === 'existing' && !selectedId)) ? 0.6 : 1,
            }}
          >
            {busy ? <Icon name="loader" size={14} /> : <Icon name="plus" size={14} />}
            {mode === 'existing' ? 'Add to pipeline' : 'Create + add'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label, value, onChange, placeholder,
}: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          height: 32, padding: '0 10px', fontSize: 13, fontFamily: 'inherit',
          border: '1px solid var(--border-card)', borderRadius: 6, outline: 'none',
          boxSizing: 'border-box',
        }}
      />
    </label>
  );
}
