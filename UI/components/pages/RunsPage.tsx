'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { fetchRuns, deleteRun, renameRun, type Run } from '@/lib/api';
import { runDisplayName } from '@/lib/runTitle';

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
  borderColor: active ? 'var(--primary)' : 'var(--border-card)',
  background: active ? 'var(--primary)' : 'var(--bg-app)',
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

  // Rename + delete state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<Run | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const startEdit = (id: string, title: string) => {
    setActionError(null);
    setEditingId(id);
    setEditTitle(title);
  };

  const saveEdit = async (id: string) => {
    const next = editTitle.trim();
    setEditingId(null);
    const current = runs.find((r) => (r.id || r._id) === id);
    if (!next || (current && current.title === next)) return;
    setBusyId(id);
    setActionError(null);
    try {
      await renameRun(id, next);
      setRuns((prev) =>
        prev.map((r) => ((r.id || r._id) === id ? { ...r, title: next } : r)),
      );
    } catch (e: any) {
      setActionError(e.message || 'Failed to rename run');
    } finally {
      setBusyId(null);
    }
  };

  const confirmDeleteRun = async () => {
    if (!confirmDelete) return;
    const id = (confirmDelete.id || confirmDelete._id) as string;
    setBusyId(id);
    setActionError(null);
    try {
      await deleteRun(id);
      setRuns((prev) => prev.filter((r) => (r.id || r._id) !== id));
      setConfirmDelete(null);
    } catch (e: any) {
      setActionError(e.message || 'Failed to delete run');
    } finally {
      setBusyId(null);
    }
  };

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
              background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
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
              <button style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit' }}>
                + New Run
              </button>
            </Link>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {runs.map((run, idx) => {
                const id = (run.id || run._id) as string;
                const stats = run.stats;
                const isHovered = hover === id;
                const isEditing = editingId === id;
                const runNumber = (page - 1) * LIMIT + idx + 1;
                return (
                  <div
                    key={id}
                    role="button"
                    tabIndex={0}
                    onClick={() => { if (!isEditing) router.push(`/runs/${id}`); }}
                    onKeyDown={(e) => { if (!isEditing && e.key === 'Enter') router.push(`/runs/${id}`); }}
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
                          {isEditing ? (
                            <input
                              autoFocus
                              value={editTitle}
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => setEditTitle(e.target.value)}
                              onBlur={() => saveEdit(id)}
                              onKeyDown={(e) => {
                                e.stopPropagation();
                                if (e.key === 'Enter') saveEdit(id);
                                if (e.key === 'Escape') setEditingId(null);
                              }}
                              style={{
                                fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)',
                                fontFamily: 'inherit', padding: '2px 8px', borderRadius: 6,
                                border: '1px solid var(--fg-primary)', outline: 'none',
                                background: '#FFF', minWidth: 240,
                              }}
                            />
                          ) : (
                            <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {runDisplayName(run)}
                            </span>
                          )}
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
                        {/* Row actions — rename + delete */}
                        <div
                          style={{
                            display: 'flex', alignItems: 'center', gap: 4,
                            opacity: isHovered || isEditing ? 1 : 0,
                            transition: 'opacity 120ms',
                          }}
                        >
                          <button
                            title="Rename run"
                            disabled={busyId === id}
                            onClick={(e) => { e.stopPropagation(); startEdit(id, runDisplayName(run)); }}
                            style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 30, height: 30, borderRadius: 6, cursor: 'pointer',
                              border: '1px solid var(--border-card)', background: '#FFF',
                              color: 'var(--fg-secondary)',
                            }}
                          >
                            <Icon name="pencil" size={14} />
                          </button>
                          <button
                            title="Delete run"
                            disabled={busyId === id}
                            onClick={(e) => { e.stopPropagation(); setActionError(null); setConfirmDelete(run); }}
                            style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 30, height: 30, borderRadius: 6, cursor: 'pointer',
                              border: '1px solid var(--border-card)', background: '#FFF',
                              color: 'var(--status-danger)',
                            }}
                          >
                            <Icon name={busyId === id ? 'loader' : 'trash-2'} size={14} />
                          </button>
                        </div>
                        <Icon name="chevron-right" size={18} style={{ color: isHovered ? 'var(--fg-primary)' : 'var(--fg-muted)', transition: 'color 120ms' }} />
                      </div>
                    </div>
                  </div>
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

      {/* Action error toast */}
      {actionError && (
        <div style={{
          position: 'fixed', bottom: 20, right: 20, zIndex: 60,
          padding: '12px 16px', background: '#FEF2F2', border: '1px solid #FECACA',
          borderRadius: 8, fontSize: 13, color: '#B91C1C', maxWidth: 380,
          boxShadow: '0 6px 24px rgba(0,0,0,0.12)',
        }}>
          {actionError}
        </div>
      )}

      {/* Delete confirmation modal */}
      {confirmDelete && (
        <div
          onClick={() => busyId ? null : setConfirmDelete(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 70,
            background: 'rgba(0,0,0,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: '100%', maxWidth: 440, background: '#FFF', borderRadius: 12,
              padding: 24, boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 36, height: 36, borderRadius: 9999,
                background: 'var(--status-danger)1A', color: 'var(--status-danger)',
              }}>
                <Icon name="trash-2" size={18} />
              </span>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)' }}>
                Delete this run?
              </div>
            </div>
            <div style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.5, marginBottom: 20 }}>
              <strong>{runDisplayName(confirmDelete)}</strong> and all of its data — jobs, prospects,
              outreach, and any companies unique to this run — will be permanently deleted.
              This cannot be undone.
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button
                disabled={!!busyId}
                onClick={() => setConfirmDelete(null)}
                style={{
                  height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                  cursor: busyId ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)',
                  background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
                }}
              >
                Cancel
              </button>
              <button
                disabled={!!busyId}
                onClick={confirmDeleteRun}
                style={{
                  height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                  cursor: busyId ? 'not-allowed' : 'pointer', border: 'none',
                  background: 'var(--status-danger)', color: '#FFF', fontFamily: 'inherit',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                {busyId ? <Icon name="loader" size={14} /> : <Icon name="trash-2" size={14} />}
                Delete run
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
