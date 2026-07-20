'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { CandidateCard, EmailModal, card, fmtRunDate } from '../matching/shared';
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

/** Recruiter-facing names, keyed by component. Owned here rather than read from
 *  `component.label` so runs stored before the copy was fixed — which still carry
 *  "Semantic similarity" — render in plain language too. */
const DIM_LABEL: Record<string, string> = {
  semantic: 'profile fit',
  skillCoverage: 'must-have skills',
  experience: 'experience',
  location: 'location',
};

/** Run-level headline.
 *
 * The figures are the OUTCOME of the run, not the model's weights. A recruiter
 * opening this asks "did we find anyone?" — the previous version answered "here is
 * how the score is weighted", which is the machine's business, not theirs. The
 * weighting survives as one quiet line underneath: context, not headline.
 */
function RunSummary({ run }: { run: SavedMatchRun }) {
  const analysis = run.analysis;
  const all = analysis?.candidates || [];
  const excluded = analysis?.excluded || [];
  if (!all.length) return null;

  const strong = all.filter((c) => c.score >= 75).length;
  const look = all.filter((c) => c.score >= 60 && c.score < 75).length;
  const below = all.filter((c) => c.score < 60).length;
  // Applicability is a property of the JD, so any candidate's breakdown answers it.
  const components = all[0]?.breakdown?.components || [];

  const stat = (n: number, k: string, s: string, dim = false) => (
    <div style={{ padding: '18px 26px 16px', borderLeft: '1px solid var(--band-line)', minWidth: 0 }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 34, fontWeight: 600, lineHeight: 1,
        letterSpacing: '-0.03em', fontVariantNumeric: 'tabular-nums',
        color: dim ? 'var(--fg-subtle)' : 'var(--primary)',
      }}>{n}</div>
      <div style={{
        fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em',
        color: 'var(--fg-muted)', marginTop: 9,
      }}>{k}</div>
      <div style={{ fontSize: 12, color: 'var(--fg-subtle)', marginTop: 3 }}>{s}</div>
    </div>
  );

  return (
    <div style={{
      background: 'var(--band)', borderTop: '1px solid var(--band-line)',
      borderBottom: '1px solid var(--band-line)', margin: '0 0 22px',
    }}>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
      }}>
        {stat(strong, 'Strong matches', 'Worth contacting today')}
        {stat(look, 'Worth a look', 'Some requirements unproven')}
        {stat(below, 'Below the bar', 'Missing must-have skills', true)}
        {stat(all.length, 'Reviewed in full', 'Every candidate sourced', true)}
      </div>

      <div style={{
        padding: '11px 0 12px', fontSize: 11.5, color: 'var(--fg-subtle)',
        borderTop: '1px solid var(--band-line)',
      }}>
        Scoring weight —{' '}
        {components.filter((c) => c.applicable).map((c, i, arr) => (
          <span key={c.key}>
            <strong style={{ color: 'var(--fg-muted)', fontWeight: 600 }}>
              {DIM_LABEL[c.key] ?? c.label} {Math.round(c.weight * 100)}%
            </strong>
            {i < arr.length - 1 ? ' · ' : ''}
          </span>
        ))}
        {components.some((c) => !c.applicable) && (
          <>{' · '}{components.filter((c) => !c.applicable)
              .map((c) => (DIM_LABEL[c.key] ?? c.label).toLowerCase()).join(', ')} not scored,
          this role names none.</>
        )}
        {excluded.length > 0 && (
          <> · <strong style={{ color: '#9A3412', fontWeight: 600 }}>{excluded.length}</strong> could not be
          enriched, so were never scored.</>
        )}
      </div>
    </div>
  );
}

export function MatchingRunDetailPage({ runId }: Props) {
  const [run, setRun] = useState<SavedMatchRun | null>(null);
  const [state, setState] = useState<'loading' | 'running' | 'done' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
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
  const topResults = run?.results || [];
  const allScored = run?.analysis?.candidates || [];
  // Older runs stored only their top-N, so "view all" is offered only when the
  // run actually carries analysis for candidates beyond that window.
  const canShowAll = allScored.length > topResults.length;
  const results = showAll && canShowAll ? allScored : topResults;

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

      {/* Full-bleed: the old 900px centred column wasted most of a desktop screen
          and squeezed the evidence panel into a scroller. */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        <div style={{ maxWidth: 1560, margin: '0 auto', padding: '22px 28px 60px' }}>

          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 24, flexWrap: 'wrap' }}>
            <div>
              <h2 style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em', color: 'var(--fg-primary)', margin: 0, textWrap: 'balance' }}>{title}</h2>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 5 }}>
                {fmtRunDate(run?.createdAt)}
                {run?.candidatesConsidered ? (
                  <> · <strong style={{ color: 'var(--fg-secondary)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{run.candidatesConsidered}</strong> candidates scored</>
                ) : ''}
                {run?.source === 'pipeline' ? ' · from candidate pipeline' : ''}
              </div>
            </div>
          </div>

          {/* Must-have chips */}
          {run?.requirements?.mustHaveSkills && run.requirements.mustHaveSkills.length > 0 && (
            <div style={{ margin: '16px 0 18px', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{
                fontSize: 11, fontWeight: 600, color: 'var(--fg-subtle)',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: 2,
              }}>Must have</span>
              {run.requirements.mustHaveSkills.map((s) => (
                <span key={s} style={{
                  fontSize: 12, fontWeight: 500, padding: '3px 9px', borderRadius: 5,
                  background: 'var(--bg-chip)', color: 'var(--fg-secondary)',
                  border: '1px solid var(--border-card)',
                }}>{s}</span>
              ))}
            </div>
          )}

          {/* JD parsed to no must-haves — the ranking is similarity-only and the
              recruiter must know before trusting it. Computed by the backend since
              match-scoring-6; was persisted but never shown. */}
          {run?.requirementsWarning && (
            <div style={{
              display: 'flex', gap: 10, alignItems: 'flex-start', margin: '0 0 16px',
              padding: '11px 14px', borderRadius: 8, fontSize: 12.5, lineHeight: 1.55,
              background: '#FFFBEB', border: '1px solid #FDE68A', color: '#92400E',
            }}>
              <Icon name="alert-triangle" size={15} style={{ flexShrink: 0, marginTop: 1 }} />
              <span><strong style={{ fontWeight: 600 }}>Check this ranking:</strong> {run.requirementsWarning}</span>
            </div>
          )}

          {/* QA audit outcome — the adversarial second reader's verdict on this run. */}
          {run?.qa && run.qa.status === 'completed' && (
            <div style={{
              display: 'flex', gap: 8, alignItems: 'center', margin: '0 0 16px',
              padding: '9px 14px', borderRadius: 8, fontSize: 12.5,
              background: (run.qa.fnCorrected || 0) > 0 ? '#EFF6FF' : 'var(--bg-chip)',
              border: `1px solid ${(run.qa.fnCorrected || 0) > 0 ? '#BFDBFE' : 'var(--border-card)'}`,
              color: 'var(--fg-secondary)',
            }}>
              <Icon name="shield" size={14} style={{ flexShrink: 0, color: (run.qa.fnCorrected || 0) > 0 ? '#2563EB' : 'var(--fg-muted)' }} />
              <span>
                Every result was double-checked by the QA auditor
                {(run.qa.fnCorrected || 0) > 0
                  ? <> — <strong style={{ fontWeight: 600 }}>{run.qa.fnCorrected} score{run.qa.fnCorrected === 1 ? '' : 's'} corrected upward</strong> on verified evidence the scorer missed</>
                  : ' — no scoring mistakes found'}
                {(run.qa.fpFlagsRaised || 0) > 0 && <>, {run.qa.fpFlagsRaised} credited skill{run.qa.fpFlagsRaised === 1 ? '' : 's'} flagged for review</>}.
              </span>
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

                {/* What the run found, before any individual candidate */}
                {!running && run && <RunSummary run={run} />}

                {/* Results — partial while running (arrive on the spot), final when done */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {results.map((c, i) => (
                    <CandidateCard key={c.candidateId} c={c} rank={i + 1}
                      onReachOut={(cand) => setEmailTarget({ candidate: cand, roleTitle: title })}
                      onOpen={c.source === 'pipeline' ? (cand) => setProfileTarget(cand) : undefined} />
                  ))}
                </div>

                {/* The rest of the ranking — the evidence for what the top-N beat */}
                {!running && canShowAll && (
                  <button
                    onClick={() => setShowAll((s) => !s)}
                    style={{
                      width: '100%', marginTop: 22, padding: 13, borderRadius: 9, cursor: 'pointer',
                      border: '1px dashed var(--border-strong)', background: '#FFF',
                      fontFamily: 'inherit', fontSize: 13, fontWeight: 600, color: 'var(--primary)',
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                    }}
                  >
                    <Icon name={showAll ? 'chevron-up' : 'chevron-down'} size={15} />
                    {showAll
                      ? `Show only the top ${topResults.length}`
                      : `View all ${allScored.length} scored candidates`}
                  </button>
                )}

                {!running && !canShowAll && topResults.length > 0 && (
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', textAlign: 'center', padding: '16px 0 0' }}>
                    This run was scored before per-candidate analysis was recorded, so only its
                    top {topResults.length} were kept. Re-run the match to see the full ranking.
                  </div>
                )}

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
