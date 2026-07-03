'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { CandidateCard, EmailModal, card, label, fmtRunDate } from '../matching/shared';
import { fetchMatchRun, type SavedMatchRun, type MatchedCandidate } from '@/lib/api';

interface Props { runId: string }

export function MatchingRunDetailPage({ runId }: Props) {
  const [run, setRun] = useState<SavedMatchRun | null>(null);
  const [state, setState] = useState<'loading' | 'running' | 'done' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [emailTarget, setEmailTarget] = useState<{ candidate: MatchedCandidate; roleTitle?: string } | null>(null);

  const poll = useCallback(async () => {
    let alive = true;
    for (let i = 0; i < 200; i++) {
      if (!alive) return;
      try {
        const r = await fetchMatchRun(runId);
        if (!alive) return;
        setRun(r);
        // CV runs have no status → treat as done; pipeline runs carry a status.
        if (!r.status || r.status === 'completed') { setState('done'); return; }
        if (r.status === 'failed') { setError(r.error || 'This match run failed.'); setState('error'); return; }
        setState('running');
      } catch (e: any) {
        if (!alive) return;
        setError(e.message || 'Failed to load match run');
        setState('error');
        return;
      }
      await new Promise((res) => setTimeout(res, 2000));
    }
    return () => { alive = false; };
  }, [runId]);

  useEffect(() => { poll(); }, [poll]);

  const title = run?.jdTitle || run?.jdFileName || 'Match run';
  const results = run?.results || [];

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

          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
            <div>
              <h2 style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>{title}</h2>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 4 }}>
                {fmtRunDate(run?.createdAt)}
                {run?.candidatesConsidered ? ` · ${run.candidatesConsidered} considered` : ''}
                {results.length ? ` · top ${results.length}` : ''}
                {run?.source === 'pipeline' ? ' · from candidate pipeline' : ''}
              </div>
            </div>
          </div>

          {/* Must-have chips */}
          {run?.requirements?.mustHaveSkills && run.requirements.mustHaveSkills.length > 0 && (
            <div style={{ marginBottom: 14, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Must-have:</span>
              {run.requirements.mustHaveSkills.map((s) => (
                <span key={s} style={{ fontSize: 12, padding: '3px 9px', borderRadius: 9999, background: '#EFF6FF', color: 'var(--status-info)', fontWeight: 600 }}>{s}</span>
              ))}
            </div>
          )}

          {/* JD text */}
          {run?.jdText && (
            <details style={{ marginBottom: 16 }}>
              <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)' }}>
                View job description
              </summary>
              <pre style={{
                whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--fg-secondary)', lineHeight: 1.6,
                marginTop: 8, padding: 12, background: '#FFF', border: '1px solid var(--border-card)',
                borderRadius: 6, fontFamily: 'inherit', maxHeight: 260, overflow: 'auto',
              }}>{run.jdText}</pre>
            </details>
          )}

          {/* Body */}
          {(state === 'loading' || state === 'running') && (
            <div style={{ textAlign: 'center', padding: 50, color: 'var(--fg-muted)' }}>
              <Icon name="loader" size={24} />
              <div style={{ marginTop: 12, fontSize: 14 }}>
                {state === 'running'
                  ? 'Enriching & matching candidates… this runs in the background.'
                  : 'Loading match run…'}
              </div>
            </div>
          )}
          {state === 'error' && (
            <div style={{ ...card, color: 'var(--status-danger)' }}>{error || 'This match run failed.'}</div>
          )}
          {state === 'done' && (
            results.length === 0
              ? <div style={{ ...card, textAlign: 'center', color: 'var(--fg-muted)' }}>No candidates were matched in this run.</div>
              : results.map((c, i) => (
                  <CandidateCard key={c.candidateId} c={c} rank={i + 1}
                    onReachOut={(cand) => setEmailTarget({ candidate: cand, roleTitle: title })} />
                ))
          )}
        </div>
      </div>

      {emailTarget && (
        <EmailModal candidate={emailTarget.candidate} roleTitle={emailTarget.roleTitle} onClose={() => setEmailTarget(null)} />
      )}
    </>
  );
}
