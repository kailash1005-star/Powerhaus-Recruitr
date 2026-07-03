'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { fmtRunDate } from '../matching/shared';
import { fetchMatchRuns, type SavedMatchRun } from '@/lib/api';

const LIMIT = 20;

function StatusDot({ status }: { status?: SavedMatchRun['status'] }) {
  const map: Record<string, { bg: string; pulse?: boolean; label: string }> = {
    running:   { bg: 'var(--status-warning)', pulse: true, label: 'Running' },
    completed: { bg: 'var(--status-info)', label: 'Completed' },
    failed:    { bg: 'var(--status-danger)', label: 'Failed' },
  };
  const cfg = map[status || 'completed'];
  if (!cfg) return null;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600,
      background: cfg.bg + '1A', color: cfg.bg, border: `1px solid ${cfg.bg}40`,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 9999, background: cfg.bg, animation: cfg.pulse ? 'pulse 2s infinite' : undefined }} />
      {cfg.label}
    </span>
  );
}

export function MatchingPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<SavedMatchRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [hover, setHover] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchMatchRuns(page, LIMIT);
      setRuns(data.items);
      setHasMore(data.items.length === LIMIT);
    } catch (e: any) {
      setError(e.message || 'Failed to fetch match runs');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  // Auto-refresh while any run is still processing (pipeline runs).
  useEffect(() => {
    if (!runs.some((r) => r.status === 'running')) return;
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [runs, load]);

  const newBtn = (
    <Link href="/matching/new" style={{ textDecoration: 'none' }}>
      <button style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        height: 32, padding: '0 14px', borderRadius: 6, fontSize: 13,
        fontWeight: 500, cursor: 'pointer', border: 'none',
        background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
      }}>
        <Icon name="sparkles" size={14} />
        Candidate Matching
      </button>
    </Link>
  );

  return (
    <>
      <TopBar title="Candidate Matching" showSearch={false} actions={newBtn} />

      {/* Count strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
      }}>
        <span style={{ fontSize: 12, color: 'var(--fg-muted)', fontWeight: 500 }}>Past runs · saved matches</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
          {runs.length} run{runs.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {loading && runs.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--fg-muted)' }}>
            <div style={{ textAlign: 'center' }}>
              <Icon name="loader" size={24} />
              <div style={{ marginTop: 12, fontSize: 14 }}>Loading match runs...</div>
            </div>
          </div>
        ) : error ? (
          <div style={{ padding: '20px 24px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        ) : runs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 24px', border: '1px solid var(--border-card)', borderRadius: 10, background: '#FAFAFA' }}>
            <Icon name="sparkles" size={36} style={{ color: 'var(--fg-muted)', marginBottom: 16 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>No match runs yet</div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginBottom: 20 }}>
              Start a run with Candidate Matching, or run a match from a job’s candidate list.
            </div>
            {newBtn}
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {runs.map((run, idx) => {
                const id = run._id;
                const isHovered = hover === id;
                const runNumber = (page - 1) * LIMIT + idx + 1;
                const title = run.jdTitle || run.jdFileName || 'Untitled role';
                const count = run.results?.length || 0;
                return (
                  <div
                    key={id}
                    role="button"
                    tabIndex={0}
                    onClick={() => router.push(`/matching/${id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') router.push(`/matching/${id}`); }}
                    onMouseEnter={() => setHover(id)}
                    onMouseLeave={() => setHover(null)}
                    style={{
                      width: '100%', textAlign: 'left',
                      background: isHovered ? '#F9FAFB' : '#FFFFFF',
                      border: `1px solid ${isHovered ? '#D1D5DB' : 'var(--border-card)'}`,
                      borderRadius: 10, padding: '16px 20px',
                      cursor: 'pointer', transition: 'all 120ms', fontFamily: 'inherit',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
                      {/* Left */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            minWidth: 26, height: 26, padding: '0 7px', borderRadius: 7,
                            background: 'var(--bg-chip, #F3F4F6)', color: 'var(--fg-secondary)',
                            fontSize: 12, fontWeight: 700, fontVariantNumeric: 'tabular-nums',
                            border: '1px solid var(--border-card)', flexShrink: 0,
                          }}>
                            {runNumber}
                          </span>
                          <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {title}
                          </span>
                          <StatusDot status={run.status} />
                          {run.source === 'pipeline' && (
                            <span title="Run from a candidate pipeline" style={{
                              display: 'inline-flex', alignItems: 'center', gap: 4,
                              fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 9999,
                              background: '#EEF2FF', color: '#4F46E5', border: '1px solid #C7D2FE',
                            }}>
                              <Icon name="users" size={10} />Pipeline
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, color: 'var(--fg-muted)' }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <Icon name="clock" size={12} />
                            {fmtRunDate(run.createdAt)}
                          </span>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <Icon name="users" size={12} />
                            {run.candidatesConsidered} considered · top {count}
                          </span>
                        </div>
                      </div>

                      {/* Right */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--primary)', background: '#EFF3FB', borderRadius: 9999, padding: '3px 10px' }}>
                          {count} candidate{count !== 1 ? 's' : ''}
                        </span>
                        <Icon name="chevron-right" size={18} style={{ color: isHovered ? 'var(--fg-primary)' : 'var(--fg-muted)', transition: 'color 120ms' }} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: page === 1 ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: page === 1 ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                <Icon name="chevron-left" size={14} />Previous
              </button>
              <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Page {page}</span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={!hasMore}
                style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: !hasMore ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: !hasMore ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
              >
                Next<Icon name="chevron-right" size={14} />
              </button>
            </div>
          </>
        )}
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
