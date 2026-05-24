'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { fetchRuns, type Run } from '@/lib/api';

const STATUS_FILTERS = [
  { key: 'all',       label: 'All Runs' },
  { key: 'active',    label: 'Active' },
  { key: 'completed', label: 'Completed' },
  { key: 'cancelled', label: 'Cancelled' },
];

function fmtDate(d: string | null) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-CA', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function RunStatusDot({ status }: { status: Run['status'] }) {
  const map: Record<string, { bg: string; pulse?: boolean; label: string }> = {
    active:    { bg: 'var(--status-success)', pulse: true, label: 'Active' },
    completed: { bg: 'var(--status-info)',    label: 'Completed' },
    paused:    { bg: 'var(--status-warning)', label: 'Paused' },
    cancelled: { bg: 'var(--status-danger)',  label: 'Cancelled' },
  };
  const cfg = map[status] ?? { bg: 'var(--fg-muted)', label: status };
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600,
      background: cfg.bg + '1A', color: cfg.bg,
      border: `1px solid ${cfg.bg}40`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: 9999, background: cfg.bg,
        animation: cfg.pulse ? 'pulse 2s infinite' : undefined,
      }} />
      {cfg.label}
    </span>
  );
}

const chipStyle = (active: boolean): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', padding: '5px 12px',
  borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: 'pointer',
  border: '1px solid', transition: 'all 120ms', userSelect: 'none',
  textDecoration: 'none',
  borderColor: active ? 'var(--fg-primary)' : 'var(--border-card)',
  background: active ? 'var(--fg-primary)' : 'var(--bg-app)',
  color: active ? '#FFF' : 'var(--fg-secondary)',
});

export function RunsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const statusFilter = searchParams.get('status') || 'all';

  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const LIMIT = 10;

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchRuns(page, LIMIT);
      const filtered =
        statusFilter === 'all' ? data : data.filter((r) => r.status === statusFilter);
      setRuns(filtered);
      setHasMore(data.length === LIMIT);
    } catch (e: any) {
      setError(e.message || 'Failed to fetch runs');
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Auto-refresh every 8s when any run is active
  useEffect(() => {
    const hasActive = runs.some((r) => r.status === 'active');
    if (!hasActive) return;
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [runs, load]);

  return (
    <>
      <TopBar
        title="Runs"
        actions={
          <Link href="/icp" style={{ textDecoration: 'none' }}>
            <button style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              height: 32, padding: '0 14px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, cursor: 'pointer', border: 'none',
              background: 'var(--fg-primary)', color: '#FFF', fontFamily: 'inherit',
            }}>
              <Icon name="plus" size={14} />
              New Run
            </button>
          </Link>
        }
      />

      {/* Filter chips */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
      }}>
        {STATUS_FILTERS.map((f) => (
          <Link
            key={f.key}
            href={f.key === 'all' ? '/runs' : `/runs?status=${f.key}`}
            style={chipStyle(statusFilter === f.key)}
          >
            {f.label}
          </Link>
        ))}
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
              <div style={{ marginTop: 12, fontSize: 14 }}>Loading runs...</div>
            </div>
          </div>
        ) : error ? (
          <div style={{ padding: '20px 24px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        ) : runs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 24px', border: '1px solid var(--border-card)', borderRadius: 10, background: '#FAFAFA' }}>
            <Icon name="search" size={36} style={{ color: 'var(--fg-muted)', marginBottom: 16 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>No runs yet</div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginBottom: 20 }}>
              Start your first run by clicking New Run.
            </div>
            <Link href="/icp" style={{ textDecoration: 'none' }}>
              <button style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', background: 'var(--fg-primary)', color: '#FFF', fontFamily: 'inherit' }}>
                + New Run
              </button>
            </Link>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {runs.map((run) => {
                const id = (run.id || run._id) as string;
                const stats = run.stats;
                const isHovered = hover === id;
                return (
                  <button
                    key={id}
                    onClick={() => router.push(`/runs/${id}`)}
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
                          <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {run.title}
                          </span>
                          <RunStatusDot status={run.status} />
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, color: 'var(--fg-muted)' }}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <Icon name="clock" size={12} />
                            {fmtDate(run.runStartedAt)}
                          </span>
                          {run.runConfig.searchTitles?.length > 0 && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="briefcase" size={12} />
                              {run.runConfig.searchTitles.slice(0, 2).join(', ')}
                              {run.runConfig.searchTitles.length > 2 && ` +${run.runConfig.searchTitles.length - 2}`}
                            </span>
                          )}
                          {run.runConfig.searchLocations?.length > 0 && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="map-pin" size={12} />
                              {run.runConfig.searchLocations.join(', ')}
                            </span>
                          )}
                          {run.runConfig.siteName?.length > 0 && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="building-2" size={12} />
                              {run.runConfig.siteName.join(', ')}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Right — stats */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexShrink: 0 }}>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-primary)' }}>
                            {stats.totalJobsScraped}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Jobs
                          </div>
                        </div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--status-success)' }}>
                            {stats.acceptedCompanies ?? 0}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Accepted
                          </div>
                        </div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--status-danger)' }}>
                            {stats.rejectedCompanies ?? 0}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                            Rejected
                          </div>
                        </div>
                        <Icon name="chevron-right" size={18} style={{ color: isHovered ? 'var(--fg-primary)' : 'var(--fg-muted)', transition: 'color 120ms' }} />
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>

            {/* Pagination */}
            {(runs.length > 0 || page > 1) && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: page === 1 ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: page === 1 ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                >
                  <Icon name="chevron-left" size={14} />
                  Previous
                </button>
                <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Page {page}</span>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={!hasMore}
                  style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: !hasMore ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: !hasMore ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                >
                  Next
                  <Icon name="chevron-right" size={14} />
                </button>
              </div>
            )}
          </>
        )}
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
