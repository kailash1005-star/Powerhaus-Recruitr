'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  uploadCvs, fetchCvBatchStatus, fetchCvs, runMatchingText, runMatchingFile,
  draftOutreach, enrollOutreach, fetchMatchRuns, cvDownloadUrl,
  type MatchResult, type MatchedCandidate, type SavedMatchRun,
} from '@/lib/api';

const MAX_JDS = 3;

const card: React.CSSProperties = {
  background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20,
};
const label: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
};
const primaryBtn = (disabled: boolean): React.CSSProperties => ({
  height: 38, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600,
  cursor: disabled ? 'not-allowed' : 'pointer', border: 'none',
  background: disabled ? 'var(--fg-subtle)' : 'var(--primary)', color: '#FFF',
  fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: disabled ? 0.7 : 1,
});

interface JdSlot { id: string; text: string; file: File | null; fileName: string | null; }
type SlotResult = { ok: true; data: MatchResult } | { ok: false; error: string };

function newSlot(): JdSlot {
  return { id: Math.random().toString(36).slice(2), text: '', file: null, fileName: null };
}

function ScoreBar({ value }: { value: number }) {
  const color = value >= 75 ? 'var(--status-success)' : value >= 50 ? 'var(--status-info)' : 'var(--status-warning)';
  return (
    <div style={{ width: '100%', height: 6, background: 'var(--bg-app)', borderRadius: 9999, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', background: color }} />
    </div>
  );
}

function CandidateCard({ c, rank, onReachOut }: { c: MatchedCandidate; rank: number; onReachOut: (c: MatchedCandidate) => void }) {
  const contact = c.contact || {};
  return (
    <div style={{ ...card, marginBottom: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, background: 'var(--primary)', color: '#FFF',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700, flexShrink: 0,
          }}>{rank}</div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>{c.fullName || 'Unnamed candidate'}</div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
              {c.currentTitle || '—'}{c.location ? ` · ${c.location}` : ''}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'right', minWidth: 70 }}>
          <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--fg-primary)', lineHeight: 1 }}>{c.score}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>match</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, margin: '14px 0' }}>
        {Object.entries(c.subscores || {}).map(([k, v]) => (
          <div key={k}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--fg-muted)', textTransform: 'capitalize' }}>{k.replace(/([A-Z])/g, ' $1')}</span>
              <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-secondary)' }}>{Math.round(v)}</span>
            </div>
            <ScoreBar value={v} />
          </div>
        ))}
      </div>

      {c.reasons?.length > 0 && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.6 }}>
          {c.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {c.gaps?.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--status-danger)' }}>Gaps: {c.gaps.join('; ')}</div>
      )}

      <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-default)', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', fontSize: 13 }}>
        {contact.email && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="mail" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.email}</span>}
        {contact.phone && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="phone" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.phone}</span>}
        {!contact.email && !contact.phone && <span style={{ color: 'var(--fg-subtle)' }}>No contact details parsed</span>}
        <div style={{ flex: 1 }} />
        <a
          href={cvDownloadUrl(c.candidateId)}
          download
          title="Download this candidate's CV"
          style={{
            height: 32, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
            cursor: 'pointer', border: '1px solid var(--border-card)', textDecoration: 'none',
            background: '#FFF', color: 'var(--fg-secondary)',
            display: 'inline-flex', alignItems: 'center', gap: 6,
          }}
        >
          <Icon name="download" size={14} />Download CV
        </a>
        <button
          onClick={() => onReachOut(c)}
          disabled={!contact.email}
          title={contact.email ? 'Draft an outreach email' : 'No email parsed for this candidate'}
          style={{
            height: 32, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
            cursor: contact.email ? 'pointer' : 'not-allowed', border: 'none',
            background: contact.email ? 'var(--primary)' : 'var(--fg-subtle)', color: '#FFF',
            fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6, opacity: contact.email ? 1 : 0.6,
          }}
        >
          <Icon name="mail" size={14} />Reach out
        </button>
      </div>
    </div>
  );
}

function EmailModal({ candidate, roleTitle, onClose }: { candidate: MatchedCandidate; roleTitle?: string; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [to, setTo] = useState(candidate.contact?.email || '');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [sendEnabled, setSendEnabled] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await draftOutreach(candidate.candidateId, roleTitle);
        if (!alive) return;
        setTo(d.to || candidate.contact?.email || '');
        setSubject(d.subject);
        setBody(d.body);
        setSendEnabled(d.sendEnabled);
      } catch (e: any) {
        if (alive) setErr(e.message || 'Failed to draft email');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [candidate, roleTitle]);

  const onCopy = () => {
    navigator.clipboard?.writeText(`Subject: ${subject}\n\n${body}`);
    setNote('Copied to clipboard ✓');
  };

  const onTrack = async () => {
    setErr(null); setNote(null); setTracking(true);
    try {
      const res = await enrollOutreach({
        email: to,
        name: candidate.fullName || undefined,
        title: candidate.currentTitle || undefined,
        roleTitle,
        audience: 'candidate',
        campaignName: roleTitle,
        candidateId: candidate.candidateId,
      });
      setNote(res.sent
        ? `Sent & tracking in Outreach → Candidates ✓`
        : `Added to Outreach → Candidates ✓ ${res.note ? `(${res.note})` : ''}`);
    } catch (e: any) {
      setErr(e.message || 'Failed to add to outreach');
    } finally {
      setTracking(false);
    }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: '#FFF', borderRadius: 12, width: '100%', maxWidth: 620, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 10px 40px rgba(0,0,0,0.2)' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-default)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Reach out to {candidate.fullName || 'candidate'}</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--fg-muted)' }}><Icon name="x" size={18} /></button>
        </div>

        <div style={{ padding: 20 }}>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 30, color: 'var(--fg-muted)' }}>
              <Icon name="loader" size={22} /><div style={{ marginTop: 8, fontSize: 14 }}>Drafting a professional email…</div>
            </div>
          ) : (
            <>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>To</label>
              <input value={to} onChange={(e) => setTo(e.target.value)} style={{ width: '100%', height: 36, padding: '0 10px', borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 14px', boxSizing: 'border-box', fontFamily: 'inherit' }} />

              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>Subject</label>
              <input value={subject} onChange={(e) => setSubject(e.target.value)} style={{ width: '100%', height: 36, padding: '0 10px', borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 14px', boxSizing: 'border-box', fontFamily: 'inherit' }} />

              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>Message</label>
              <textarea value={body} onChange={(e) => setBody(e.target.value)} style={{ width: '100%', minHeight: 220, padding: 12, borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 8px', boxSizing: 'border-box', fontFamily: 'inherit', lineHeight: 1.6, resize: 'vertical' }} />

              {!sendEnabled && (
                <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 8 }}>
                  Direct SMTP send isn’t configured. Use <strong>Add to Outreach</strong> to track this candidate in the CRM and deliver via Smartlead once connected.
                </div>
              )}
              {note && <div style={{ fontSize: 13, color: 'var(--status-success)', marginBottom: 8 }}>{note}</div>}
              {err && <div style={{ fontSize: 13, color: 'var(--status-danger)', marginBottom: 8 }}>{err}</div>}

              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 6, flexWrap: 'wrap' }}>
                <button onClick={onCopy} style={{ height: 38, padding: '0 16px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', background: 'var(--bg-app)', color: 'var(--fg-secondary)', border: '1px solid var(--border-card)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                  <Icon name="copy" size={15} />Copy
                </button>
                <button onClick={onTrack} disabled={tracking || !to} style={{ height: 38, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: (tracking || !to) ? 'not-allowed' : 'pointer', border: 'none', background: !to ? 'var(--fg-subtle)' : 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 7, opacity: !to ? 0.6 : 1 }}>
                  <Icon name="user-plus" size={15} />{tracking ? 'Adding…' : (sendEnabled ? 'Send & track' : 'Add to Outreach')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtRunDate(d?: string | null): string {
  if (!d) return '';
  return new Date(d).toLocaleString('en-CA', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function PastRunsSection({
  runs, expanded, setExpanded, onReachOut,
}: {
  runs: SavedMatchRun[];
  expanded: string | null;
  setExpanded: (id: string | null) => void;
  onReachOut: (c: MatchedCandidate, roleTitle?: string) => void;
}) {
  if (runs.length === 0) return null;
  return (
    <div style={{ ...card, marginTop: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <span style={label}>Past runs · saved matches</span>
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{runs.length} run{runs.length !== 1 ? 's' : ''}</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {runs.map((run) => {
          const isOpen = expanded === run._id;
          return (
            <div key={run._id} style={{ border: '1px solid var(--border-default)', borderRadius: 8, overflow: 'hidden' }}>
              <button
                onClick={() => setExpanded(isOpen ? null : run._id)}
                style={{
                  width: '100%', textAlign: 'left', cursor: 'pointer', background: isOpen ? 'var(--bg-app)' : '#FFF',
                  border: 'none', padding: '12px 14px', fontFamily: 'inherit',
                  display: 'flex', alignItems: 'center', gap: 12,
                }}
              >
                <Icon name={isOpen ? 'chevron-down' : 'chevron-right'} size={16} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {run.jdTitle || run.jdFileName || 'Untitled role'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                    {fmtRunDate(run.createdAt)} · {run.candidatesConsidered} considered · top {run.results?.length || 0}
                  </div>
                </div>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--primary)', background: '#EFF3FB', borderRadius: 9999, padding: '3px 10px', flexShrink: 0 }}>
                  {run.results?.length || 0} candidates
                </span>
              </button>

              {isOpen && (
                <div style={{ padding: 14, borderTop: '1px solid var(--border-default)', background: '#FAFAFA' }}>
                  {run.jdText && (
                    <details style={{ marginBottom: 12 }}>
                      <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)' }}>
                        View pasted job description
                      </summary>
                      <pre style={{
                        whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--fg-secondary)', lineHeight: 1.6,
                        marginTop: 8, padding: 12, background: '#FFF', border: '1px solid var(--border-card)',
                        borderRadius: 6, fontFamily: 'inherit', maxHeight: 220, overflow: 'auto',
                      }}>{run.jdText}</pre>
                    </details>
                  )}
                  {(run.results || []).length === 0
                    ? <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>No candidates were matched in this run.</div>
                    : run.results.map((c, i) => (
                        <CandidateCard key={c.candidateId} c={c} rank={i + 1}
                          onReachOut={(cand) => onReachOut(cand, run.jdTitle || undefined)} />
                      ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function MatchingPage() {
  // CV corpus
  const [corpusCount, setCorpusCount] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [batchMsg, setBatchMsg] = useState<string | null>(null);
  const cvInputRef = useRef<HTMLInputElement>(null);

  // JD slots (up to MAX_JDS roles)
  const [slots, setSlots] = useState<JdSlot[]>([newSlot()]);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<SlotResult[] | null>(null);
  const [activeTab, setActiveTab] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Outreach email modal
  const [emailTarget, setEmailTarget] = useState<{ candidate: MatchedCandidate; roleTitle?: string } | null>(null);

  // Past runs history
  const [pastRuns, setPastRuns] = useState<SavedMatchRun[]>([]);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  const refreshCorpus = useCallback(async () => {
    try { setCorpusCount((await fetchCvs(1, 1)).total); } catch { /* ignore */ }
  }, []);
  const refreshPastRuns = useCallback(async () => {
    try { setPastRuns((await fetchMatchRuns(1, 20)).items); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refreshCorpus(); refreshPastRuns(); }, [refreshCorpus, refreshPastRuns]);

  // ── CV upload + poll ──
  const onCvFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setBatchMsg(`Uploading ${files.length} file(s)…`);
    try {
      const { batchId, received } = await uploadCvs(Array.from(files));
      setBatchMsg(`Processing ${received} CV(s)…`);
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await fetchCvBatchStatus(batchId);
        const done = (st.counts.embedded || 0) + (st.counts.failed || 0);
        setBatchMsg(`Processed ${done}/${st.total} · embedded ${st.counts.embedded || 0}${st.counts.failed ? ` · failed ${st.counts.failed}` : ''}`);
        if (st.complete) break;
      }
      setBatchMsg((m) => (m ? m + ' — done ✓' : 'Done ✓'));
      refreshCorpus();
    } catch (e: any) {
      setBatchMsg(`Upload failed: ${e.message || e}`);
    } finally {
      setUploading(false);
      if (cvInputRef.current) cvInputRef.current.value = '';
    }
  };

  // ── JD slot helpers ──
  const updateSlot = (id: string, patch: Partial<JdSlot>) =>
    setSlots((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const addSlot = () => setSlots((prev) => (prev.length < MAX_JDS ? [...prev, newSlot()] : prev));
  const removeSlot = (id: string) => setSlots((prev) => (prev.length > 1 ? prev.filter((s) => s.id !== id) : prev));

  // ── Run matching for each role (independent, against the shared pool) ──
  const runMatch = async () => {
    setError(null);
    setResults(null);
    const active = slots.filter((s) => s.file || s.text.trim());
    if (active.length === 0) { setError('Add at least one job description (paste text or upload a file).'); return; }
    setRunning(true);
    try {
      const settled = await Promise.all(active.map(async (s): Promise<SlotResult> => {
        try {
          const data = s.file ? await runMatchingFile(s.file) : await runMatchingText(s.text);
          return { ok: true, data };
        } catch (e: any) {
          return { ok: false, error: e.message || 'matching failed' };
        }
      }));
      setResults(settled);
      setActiveTab(0);
      refreshPastRuns();
    } finally {
      setRunning(false);
    }
  };

  const activeRoleCount = slots.filter((s) => s.file || s.text.trim()).length;

  return (
    <>
      <TopBar title="Candidate Matching" showSearch={false} />

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>

          {/* ── Step 1: CV corpus ── */}
          <div style={{ ...card, marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={label}>Step 1 · Candidate CV pool</span>
              <span style={{ fontSize: 13, color: 'var(--fg-secondary)' }}>
                {corpusCount === null ? '…' : <><strong>{corpusCount}</strong> CV(s) in pool</>}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <input ref={cvInputRef} type="file" multiple accept=".pdf,.docx,.txt,.html" style={{ display: 'none' }} onChange={(e) => onCvFiles(e.target.files)} />
              <button style={primaryBtn(uploading)} disabled={uploading} onClick={() => cvInputRef.current?.click()}>
                <Icon name="upload" size={16} />{uploading ? 'Uploading…' : 'Upload CVs'}
              </button>
              {batchMsg && <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>{batchMsg}</span>}
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-subtle)', marginTop: 8 }}>
              Accepts PDF, DOCX, TXT, HTML. Upload your candidate dump once; every role below is matched against this shared pool.
            </div>
          </div>

          {/* ── Step 2: up to 3 JDs / roles ── */}
          <div style={{ ...card, marginBottom: 20 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={label}>Step 2 · Job descriptions · up to {MAX_JDS} roles</span>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>{slots.length}/{MAX_JDS}</span>
            </div>

            {slots.map((s, idx) => (
              <div key={s.id} style={{ marginBottom: 14, padding: 14, border: '1px solid var(--border-default)', borderRadius: 8, background: 'var(--bg-app)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)' }}>Role {idx + 1}</span>
                  {slots.length > 1 && (
                    <button onClick={() => removeSlot(s.id)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--fg-muted)', display: 'inline-flex', alignItems: 'center' }}>
                      <Icon name="x" size={15} />
                    </button>
                  )}
                </div>
                <textarea
                  value={s.text}
                  onChange={(e) => updateSlot(s.id, { text: e.target.value })}
                  placeholder={`Paste the job description for role ${idx + 1}…`}
                  disabled={!!s.file}
                  style={{
                    width: '100%', minHeight: 90, padding: 10, borderRadius: 6, fontSize: 13,
                    border: '1px solid var(--border-card)', fontFamily: 'inherit', color: 'var(--fg-primary)',
                    resize: 'vertical', boxSizing: 'border-box', outline: 'none', background: s.file ? 'var(--bg-nav-active)' : '#FFF',
                  }}
                />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
                  <label style={{
                    height: 32, padding: '0 12px', borderRadius: 6, fontSize: 12, fontWeight: 500,
                    cursor: 'pointer', background: '#FFF', color: 'var(--fg-secondary)', border: '1px solid var(--border-card)',
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                  }}>
                    <input type="file" accept=".pdf,.docx,.txt,.html" style={{ display: 'none' }}
                      onChange={(e) => updateSlot(s.id, { file: e.target.files?.[0] || null, fileName: e.target.files?.[0]?.name || null })} />
                    <Icon name="file-text" size={14} />{s.fileName || 'Upload JD file'}
                  </label>
                  {s.file && (
                    <button onClick={() => updateSlot(s.id, { file: null, fileName: null })} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--fg-muted)', fontSize: 12 }}>clear file</button>
                  )}
                </div>
              </div>
            ))}

            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4, flexWrap: 'wrap' }}>
              {slots.length < MAX_JDS && (
                <button onClick={addSlot} style={{
                  height: 36, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: 'pointer',
                  background: 'var(--bg-app)', color: 'var(--fg-secondary)', border: '1px dashed var(--border-card)',
                  fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 7,
                }}>
                  <Icon name="plus" size={15} />Add another role
                </button>
              )}
              <div style={{ flex: 1 }} />
              <button style={primaryBtn(running)} disabled={running} onClick={runMatch}>
                <Icon name="sparkles" size={16} />
                {running ? 'Matching…' : `Candidate Matching${activeRoleCount > 1 ? ` (${activeRoleCount} roles)` : ''}`}
              </button>
            </div>
            {error && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--status-danger)' }}>{error}</div>}
          </div>

          {/* ── Results ── */}
          {running && (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--fg-muted)' }}>
              <Icon name="loader" size={24} />
              <div style={{ marginTop: 10, fontSize: 14 }}>Matching candidates for {activeRoleCount} role(s)…</div>
            </div>
          )}

          {results && !running && (
            <div>
              {/* Role tabs (only when more than one role) */}
              {results.length > 1 && (
                <div style={{ display: 'flex', gap: 6, marginBottom: 16, flexWrap: 'wrap' }}>
                  {results.map((r, i) => {
                    const title = r.ok ? (r.data.jdTitle || `Role ${i + 1}`) : `Role ${i + 1}`;
                    const active = activeTab === i;
                    return (
                      <button key={i} onClick={() => setActiveTab(i)} style={{
                        height: 34, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                        border: '1px solid ' + (active ? 'var(--primary)' : 'var(--border-card)'),
                        background: active ? 'var(--primary)' : '#FFF', color: active ? '#FFF' : 'var(--fg-secondary)',
                        fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6,
                      }}>
                        {title}{!r.ok && <Icon name="alert-triangle" size={13} />}
                      </button>
                    );
                  })}
                </div>
              )}

              {/* Active role result */}
              {(() => {
                const r = results[activeTab];
                if (!r) return null;
                if (!r.ok) return <div style={{ ...card, color: 'var(--status-danger)' }}>This role failed: {r.error}</div>;
                const data = r.data;
                return (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 14, flexWrap: 'wrap', gap: 8 }}>
                      <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>
                        Top {data.results.length} for {data.jdTitle || 'this role'}
                      </h2>
                      <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>{data.candidatesConsidered} candidate(s) considered</span>
                    </div>
                    {data.requirements?.mustHaveSkills && data.requirements.mustHaveSkills.length > 0 && (
                      <div style={{ marginBottom: 14, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Must-have:</span>
                        {data.requirements.mustHaveSkills.map((s) => (
                          <span key={s} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 9999, background: '#EFF6FF', color: 'var(--status-info)', fontWeight: 600 }}>{s}</span>
                        ))}
                      </div>
                    )}
                    {data.results.length === 0
                      ? <div style={{ ...card, textAlign: 'center', color: 'var(--fg-muted)' }}>No candidates matched. Upload CVs to the pool first.</div>
                      : data.results.map((c, i) => (
                          <CandidateCard key={c.candidateId} c={c} rank={i + 1}
                            onReachOut={(cand) => setEmailTarget({ candidate: cand, roleTitle: data.jdTitle || undefined })} />
                        ))}
                  </div>
                );
              })()}
            </div>
          )}

          {/* ── Past runs (saved matches) ── */}
          <PastRunsSection
            runs={pastRuns}
            expanded={expandedRun}
            setExpanded={setExpandedRun}
            onReachOut={(cand, roleTitle) => setEmailTarget({ candidate: cand, roleTitle })}
          />

        </div>
      </div>

      {emailTarget && (
        <EmailModal candidate={emailTarget.candidate} roleTitle={emailTarget.roleTitle} onClose={() => setEmailTarget(null)} />
      )}

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
