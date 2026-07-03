'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { card, label, primaryBtn } from '../matching/shared';
import {
  uploadCvs, fetchCvBatchStatus, fetchCvs, runMatchingText, runMatchingFile,
} from '@/lib/api';

const MAX_JDS = 3;

interface JdSlot { id: string; text: string; file: File | null; fileName: string | null; }

function newSlot(): JdSlot {
  return { id: Math.random().toString(36).slice(2), text: '', file: null, fileName: null };
}

export function MatchingNewPage() {
  const router = useRouter();

  // CV corpus
  const [corpusCount, setCorpusCount] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [batchMsg, setBatchMsg] = useState<string | null>(null);
  const cvInputRef = useRef<HTMLInputElement>(null);

  // JD slots (up to MAX_JDS roles)
  const [slots, setSlots] = useState<JdSlot[]>([newSlot()]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshCorpus = useCallback(async () => {
    try { setCorpusCount((await fetchCvs(1, 1)).total); } catch { /* ignore */ }
  }, []);
  useEffect(() => { refreshCorpus(); }, [refreshCorpus]);

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

  // ── Run matching for each role → navigate to the result screen(s) ──
  const runMatch = async () => {
    setError(null);
    const active = slots.filter((s) => s.file || s.text.trim());
    if (active.length === 0) { setError('Add at least one job description (paste text or upload a file).'); return; }
    setRunning(true);
    try {
      const ids: string[] = [];
      for (const s of active) {
        const data = s.file ? await runMatchingFile(s.file) : await runMatchingText(s.text);
        if (data.matchRunId) ids.push(data.matchRunId);
      }
      // One role → open its result screen; several → back to the list where they all appear.
      if (ids.length === 1) router.push(`/matching/${ids[0]}`);
      else router.push('/matching');
    } catch (e: any) {
      setError(e.message || 'matching failed');
      setRunning(false);
    }
  };

  const activeRoleCount = slots.filter((s) => s.file || s.text.trim()).length;

  return (
    <>
      <TopBar
        titleNode={
          <Link
            href="/matching"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', textDecoration: 'none' }}
          >
            <Icon name="arrow-left" size={16} />
            Back to Candidate Matching
          </Link>
        }
        showSearch={false}
      />

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
                <Icon name={running ? 'loader' : 'sparkles'} size={16} />
                {running ? 'Matching…' : `Candidate Matching${activeRoleCount > 1 ? ` (${activeRoleCount} roles)` : ''}`}
              </button>
            </div>
            {error && <div style={{ marginTop: 12, fontSize: 13, color: 'var(--status-danger)' }}>{error}</div>}
          </div>

        </div>
      </div>
    </>
  );
}
