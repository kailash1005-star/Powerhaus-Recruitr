'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { CandidateCard, EmailModal, card, label, fmtRunDate } from '../matching/shared';
import { MatchProfileSlideOut } from '../matching/MatchProfileSlideOut';
import { fetchMatchRun, type SavedMatchRun, type MatchedCandidate } from '@/lib/api';

interface Props { runId: string }

function logTime(ts?: string): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

const LOG_COLORS: Record<string, string> = {
  info: '#D1D5DB',
  warn: '#FBBF24',
  error: '#F87171',
};

/** Live, auto-scrolling terminal of the streaming match run. */
function LogStream({ run, running }: { run: SavedMatchRun; running: boolean }) {
  const logs = run.logs || [];
  const prog = run.progress;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs.length]);

  const pct = prog && prog.total ? Math.round((prog.processed / prog.total) * 100) : 0;

  return (
    <div style={{ border: '1px solid var(--border-card)', borderRadius: 10, overflow: 'hidden', marginBottom: 16, background: '#0B1020' }}>
      {/* Header + progress */}
      <div style={{ padding: '10px 14px', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name={running ? 'loader' : 'terminal'} size={14} style={{ color: running ? '#818CF8' : '#6B7280' }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: '#E5E7EB', letterSpacing: '0.03em' }}>
          {running ? 'Matching in progress' : 'Run log'}
        </span>
        {prog && prog.total > 0 && (
          <span style={{ fontSize: 12, color: '#9CA3AF', marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>
            {prog.processed}/{prog.total} processed · {prog.considered} matched
          </span>
        )}
      </div>
      {prog && prog.total > 0 && (
        <div style={{ height: 3, background: 'rgba(255,255,255,0.08)' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: running ? '#6366F1' : '#22C55E', transition: 'width 300ms' }} />
        </div>
      )}
      {/* Log lines */}
      <div ref={scrollRef} style={{ maxHeight: 260, overflowY: 'auto', padding: '10px 14px', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, lineHeight: 1.7 }}>
        {logs.length === 0 ? (
          <div style={{ color: '#6B7280' }}>Waiting for the first candidate…</div>
        ) : (
          logs.map((l, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              <span style={{ color: '#4B5563', flexShrink: 0 }}>{logTime(l.ts)}</span>
              <span style={{ color: LOG_COLORS[l.level || 'info'] || LOG_COLORS.info }}>{l.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export function MatchingRunDetailPage({ runId }: Props) {
  const [run, setRun] = useState<SavedMatchRun | null>(null);
  const [state, setState] = useState<'loading' | 'running' | 'done' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [emailTarget, setEmailTarget] = useState<{ candidate: MatchedCandidate; roleTitle?: string } | null>(null);
  const [profileTarget, setProfileTarget] = useState<MatchedCandidate | null>(null);

  const poll = useCallback(async () => {
    let alive = true;
    // ~15 min ceiling at 1.5s cadence — enough for a large per-candidate queue.
    for (let i = 0; i < 600; i++) {
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
      await new Promise((res) => setTimeout(res, 1500));
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
          {(() => {
            const running = state === 'loading' || state === 'running';
            // Pipeline runs stream a per-candidate queue with a live log; CV runs don't.
            const streaming = !!(run && (run.source === 'pipeline' || (run.logs?.length ?? 0) > 0 || run.progress));

            // Temporarily hide the run log stream — flip back to true to restore.
            const SHOW_LOGS = false;

            return (
              <>
                {/* Live streaming log (pipeline) */}
                {SHOW_LOGS && streaming && running && run && <LogStream run={run} running />}

                {/* Collapsed log once done */}
                {SHOW_LOGS && streaming && !running && run && (run.logs?.length ?? 0) > 0 && (
                  <details style={{ marginBottom: 16 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)' }}>
                      View run log ({run.logs!.length} lines)
                    </summary>
                    <div style={{ marginTop: 8 }}>
                      <LogStream run={run} running={false} />
                    </div>
                  </details>
                )}

                {/* Loading spinner while running (logs hidden) */}
                {(!SHOW_LOGS ? running : !streaming && running) && (
                  <div style={{ textAlign: 'center', padding: 50, color: 'var(--fg-muted)' }}>
                    <Icon name="loader" size={24} />
                    <div style={{ marginTop: 12, fontSize: 14 }}>Loading match run…</div>
                  </div>
                )}

                {state === 'error' && (
                  <div style={{ ...card, color: 'var(--status-danger)' }}>{error || 'This match run failed.'}</div>
                )}

                {/* Results — partial while running (arrive on the spot), final when done */}
                {results.map((c, i) => (
                  <CandidateCard key={c.candidateId} c={c} rank={i + 1}
                    onReachOut={(cand) => setEmailTarget({ candidate: cand, roleTitle: title })}
                    onOpen={c.source === 'pipeline' ? (cand) => setProfileTarget(cand) : undefined} />
                ))}

                {/* Empty final state */}
                {state === 'done' && results.length === 0 && (
                  <div style={{ ...card, textAlign: 'center', color: 'var(--fg-muted)' }}>No candidates were matched in this run.</div>
                )}
              </>
            );
          })()}
        </div>
      </div>

      {emailTarget && (
        <EmailModal candidate={emailTarget.candidate} roleTitle={emailTarget.roleTitle} onClose={() => setEmailTarget(null)} />
      )}

      <MatchProfileSlideOut
        matched={profileTarget}
        roleTitle={title}
        onClose={() => setProfileTarget(null)}
      />
    </>
  );
}
