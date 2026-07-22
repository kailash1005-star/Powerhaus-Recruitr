'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { CreatePipelineModal } from '../CreatePipelineModal';
import { fetchPipelines, deletePipeline, pipelineDisplayName, type Pipeline } from '@/lib/api';

function fmtDate(d: string | null | undefined) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-CA', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function pipelineHasRunning(p: Pipeline): boolean {
  return p.jobs?.some((j) => j.searchStatus === 'queued' || j.searchStatus === 'running') ?? false;
}

export function PipelinesPage() {
  const router = useRouter();
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [hover, setHover] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Pipeline | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const LIMIT = 20;

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchPipelines(page, LIMIT);
      setPipelines(data.pipelines);
      setPages(data.pages);
      setTotal(data.total);
    } catch (e: any) {
      setError(e.message || 'Failed to fetch pipelines');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  // Auto-refresh while any pipeline has an active search
  useEffect(() => {
    const anyActive = pipelines.some(pipelineHasRunning);
    if (!anyActive) return;
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [pipelines, load]);

  const confirmDeletePipeline = async () => {
    if (!confirmDelete) return;
    setBusyId(confirmDelete._id);
    setActionError(null);
    try {
      await deletePipeline(confirmDelete._id);
      setPipelines((prev) => prev.filter((p) => p._id !== confirmDelete._id));
      setConfirmDelete(null);
    } catch (e: any) {
      setActionError(e.message || 'Failed to delete pipeline');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <TopBar title="Candidate Pipelines" />

      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
      }}>
        <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
          Pipelines are created from <strong>Runs → Results</strong>. Open a run, pick a job, then
          “Add to candidate pipeline”.
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--fg-muted)', marginRight: 12 }}>
          {total} pipeline{total !== 1 ? 's' : ''}
        </span>
        <button
          onClick={() => setShowCreate(true)}
          style={{
            height: 34, padding: '0 14px', borderRadius: 8, fontSize: 13,
            fontWeight: 600, cursor: 'pointer', border: 'none',
            background: '#4F46E5', color: '#FFF', fontFamily: 'inherit',
            display: 'inline-flex', alignItems: 'center', gap: 6,
            whiteSpace: 'nowrap',
          }}
        >
          <Icon name="plus" size={14} />
          Create Pipeline
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {loading && pipelines.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--fg-muted)' }}>
            <div style={{ textAlign: 'center' }}>
              <Icon name="loader" size={24} />
              <div style={{ marginTop: 12, fontSize: 14 }}>Loading pipelines...</div>
            </div>
          </div>
        ) : error ? (
          <div style={{ padding: '20px 24px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        ) : pipelines.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px 24px', border: '1px solid var(--border-card)', borderRadius: 10, background: '#FAFAFA' }}>
            <Icon name="users" size={36} style={{ color: 'var(--fg-muted)', marginBottom: 16 }} />
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>No candidate pipelines yet</div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
              Open a completed run, pick an accepted job, and click “Add to candidate pipeline”.
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
              {pipelines.map((p) => {
                const isHovered = hover === p._id;
                const running = pipelineHasRunning(p);
                return (
                  <div
                    key={p._id}
                    role="button"
                    tabIndex={0}
                    onClick={() => router.push(`/candidates/${p._id}`)}
                    onKeyDown={(e) => { if (e.key === 'Enter') router.push(`/candidates/${p._id}`); }}
                    onMouseEnter={() => setHover(p._id)}
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
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {pipelineDisplayName(p)}
                          </span>
                          {running && (
                            <span style={{
                              display: 'inline-flex', alignItems: 'center', gap: 5,
                              padding: '3px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600,
                              background: 'var(--status-success)1A', color: 'var(--status-success)',
                              border: `1px solid var(--status-success)40`,
                            }}>
                              <span style={{
                                width: 6, height: 6, borderRadius: 9999,
                                background: 'var(--status-success)', animation: 'pulse 2s infinite',
                              }} />
                              Searching
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, color: 'var(--fg-muted)' }}>
                          {p.companyDomain && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="globe" size={12} /> {p.companyDomain}
                            </span>
                          )}
                          {p.companyIndustry && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="briefcase" size={12} /> {p.companyIndustry}
                            </span>
                          )}
                          {p.companyLocation && (
                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <Icon name="map-pin" size={12} /> {p.companyLocation}
                            </span>
                          )}
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <Icon name="clock" size={12} /> Updated {fmtDate(p.updatedAt)}
                          </span>
                        </div>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexShrink: 0 }}>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-primary)' }}>{p.jobs?.length ?? 0}</div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Jobs</div>
                        </div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--status-success)' }}>{p.acceptedCount}</div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Accepted</div>
                        </div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--status-danger)' }}>{p.rejectedCount}</div>
                          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Rejected</div>
                        </div>
                        <div style={{
                          display: 'flex', alignItems: 'center', gap: 4,
                          opacity: isHovered ? 1 : 0, transition: 'opacity 120ms',
                        }}>
                          <button
                            title="Delete pipeline"
                            onClick={(e) => { e.stopPropagation(); setActionError(null); setConfirmDelete(p); }}
                            style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 30, height: 30, borderRadius: 6, cursor: 'pointer',
                              border: '1px solid var(--border-card)', background: '#FFF',
                              color: 'var(--status-danger)',
                            }}
                          >
                            <Icon name="trash-2" size={14} />
                          </button>
                        </div>
                        <Icon name="chevron-right" size={18} style={{ color: isHovered ? 'var(--fg-primary)' : 'var(--fg-muted)' }} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {pages > 1 && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page === 1}
                  style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: page === 1 ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: page === 1 ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                >
                  <Icon name="chevron-left" size={14} /> Previous
                </button>
                <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Page {page} of {pages}</span>
                <button
                  onClick={() => setPage((p) => Math.min(pages, p + 1))}
                  disabled={page === pages}
                  style={{ height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, cursor: page === pages ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: page === pages ? 'var(--fg-muted)' : 'var(--fg-primary)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                >
                  Next <Icon name="chevron-right" size={14} />
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {actionError && (
        <div style={{
          position: 'fixed', bottom: 20, right: 20, zIndex: 60,
          padding: '12px 16px', background: '#FEF2F2', border: '1px solid #FECACA',
          borderRadius: 8, fontSize: 13, color: '#B91C1C', maxWidth: 380,
          boxShadow: '0 6px 24px rgba(0,0,0,0.12)',
        }}>{actionError}</div>
      )}

      {confirmDelete && (
        <div
          onClick={() => busyId ? null : setConfirmDelete(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 70, background: 'rgba(0,0,0,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
          }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{
            width: '100%', maxWidth: 440, background: '#FFF', borderRadius: 12,
            padding: 24, boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 36, height: 36, borderRadius: 9999,
                background: 'var(--status-danger)1A', color: 'var(--status-danger)',
              }}><Icon name="trash-2" size={18} /></span>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)' }}>Delete this pipeline?</div>
            </div>
            <div style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.5, marginBottom: 20 }}>
              <strong>{pipelineDisplayName(confirmDelete)}</strong>, all jobs in this pipeline, and every
              candidate sourced for them will be permanently deleted. This cannot be undone.
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
              >Cancel</button>
              <button
                disabled={!!busyId}
                onClick={confirmDeletePipeline}
                style={{
                  height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                  cursor: busyId ? 'not-allowed' : 'pointer', border: 'none',
                  background: 'var(--status-danger)', color: '#FFF', fontFamily: 'inherit',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                {busyId ? <Icon name="loader" size={14} /> : <Icon name="trash-2" size={14} />}
                Delete pipeline
              </button>
            </div>
          </div>
        </div>
      )}

      <CreatePipelineModal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(p, jobId) => {
          setShowCreate(false);
          // Straight into the flow: the job's candidates page auto-opens the
          // AI-prefilled search questionnaire (?search=1) — no extra clicks.
          if (jobId) router.push(`/candidates/${p._id}/jobs/${jobId}?search=1`);
          else load();
        }}
      />

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
