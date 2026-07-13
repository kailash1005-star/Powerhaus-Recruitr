'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  fetchPipeline, rerunPipelineJob, removeJobFromPipeline,
  type Pipeline, type PipelineJob, type PipelineJobSearchStatus,
} from '@/lib/api';

interface Props { pipelineId: string }

function fmtDate(d: string | null | undefined) {
  if (!d) return '—';
  return new Date(d).toLocaleString();
}

function SearchStatusBadge({ status }: { status: PipelineJobSearchStatus }) {
  const map: Record<PipelineJobSearchStatus, { bg: string; label: string; pulse?: boolean }> = {
    awaiting_input: { bg: 'var(--fg-muted)',      label: 'Needs search' },
    queued:    { bg: 'var(--status-warning)', label: 'Queued',     pulse: true },
    running:   { bg: 'var(--status-success)', label: 'Searching',  pulse: true },
    completed: { bg: 'var(--status-info)',    label: 'Completed' },
    failed:    { bg: 'var(--status-danger)',  label: 'Failed' },
  };
  const cfg = map[status] ?? map.queued;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600,
      background: cfg.bg + '1A', color: cfg.bg, border: `1px solid ${cfg.bg}40`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: 9999, background: cfg.bg,
        animation: cfg.pulse ? 'pulse 2s infinite' : undefined,
      }} />
      {cfg.label}
    </span>
  );
}

export function PipelineDetailPage({ pipelineId }: Props) {
  const router = useRouter();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<PipelineJob | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await fetchPipeline(pipelineId);
      setPipeline(data);
    } catch (e: any) {
      setError(e.message || 'Failed to fetch pipeline');
    } finally {
      setLoading(false);
    }
  }, [pipelineId]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  // Poll while any job is queued or running
  useEffect(() => {
    const active = pipeline?.jobs.some(
      (j) => j.searchStatus === 'queued' || j.searchStatus === 'running',
    );
    if (!active) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [pipeline, load]);

  const onRerun = async (jobId: string) => {
    setBusyJob(jobId);
    setActionError(null);
    try {
      await rerunPipelineJob(pipelineId, jobId);
      await load();
    } catch (e: any) {
      setActionError(e.message || 'Failed to rerun');
    } finally {
      setBusyJob(null);
    }
  };

  const onRemove = async () => {
    if (!confirmRemove) return;
    const jobId = confirmRemove.jobId;
    setBusyJob(jobId);
    setActionError(null);
    try {
      await removeJobFromPipeline(pipelineId, jobId);
      setConfirmRemove(null);
      await load();
    } catch (e: any) {
      setActionError(e.message || 'Failed to remove');
    } finally {
      setBusyJob(null);
    }
  };

  return (
    <>
      <TopBar title={pipeline ? pipeline.companyName : 'Pipeline'} />

      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '12px 24px',
        borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)',
      }}>
        <Link href="/candidates" style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          fontSize: 13, color: 'var(--fg-secondary)', textDecoration: 'none',
        }}>
          <Icon name="chevron-left" size={14} /> Back to Candidates
        </Link>
        <div style={{ flex: 1 }} />
        {pipeline && (
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            {pipeline.jobs.length} job{pipeline.jobs.length !== 1 ? 's' : ''} ·{' '}
            {pipeline.totalCandidates} candidate{pipeline.totalCandidates !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--fg-muted)' }}>
            <Icon name="loader" size={24} />
          </div>
        ) : error ? (
          <div style={{ padding: '20px 24px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        ) : !pipeline ? null : (
          <>
            <div style={{
              border: '1px solid var(--border-card)', borderRadius: 10,
              padding: '16px 20px', marginBottom: 20, background: '#FFFFFF',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <Icon name="building-2" size={18} style={{ color: 'var(--fg-muted)' }} />
                <span style={{ fontSize: 15, fontWeight: 600 }}>{pipeline.companyName}</span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12, color: 'var(--fg-muted)' }}>
                <span><Icon name="globe" size={11} /> {pipeline.companyDomain}</span>
                {pipeline.companyIndustry && <span><Icon name="briefcase" size={11} /> {pipeline.companyIndustry}</span>}
                {pipeline.companyLocation && <span><Icon name="map-pin" size={11} /> {pipeline.companyLocation}</span>}
              </div>
            </div>

            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-secondary)', marginBottom: 10 }}>
              Jobs in this pipeline
            </div>

            {pipeline.jobs.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '40px 24px', border: '1px dashed var(--border-card)', borderRadius: 10, background: '#FAFAFA', fontSize: 13, color: 'var(--fg-muted)' }}>
                Add jobs from a run’s results page using <strong>Add to candidate pipeline</strong>.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {pipeline.jobs.map((j) => {
                  const isBusy = busyJob === j.jobId;
                  const isAwaiting = j.searchStatus === 'awaiting_input';
                  const canRerun = j.searchStatus === 'completed' || j.searchStatus === 'failed';
                  // "awaiting_input" jobs open the candidates page too — that's
                  // where the Apify search questionnaire auto-opens.
                  const canOpen = j.searchStatus === 'completed' || isAwaiting;
                  return (
                    <div
                      key={j.jobId}
                      style={{
                        background: '#FFFFFF', border: '1px solid var(--border-card)',
                        borderRadius: 10, padding: '14px 18px',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
                            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)' }}>
                              {j.jobTitle || 'Untitled job'}
                            </span>
                            <SearchStatusBadge status={j.searchStatus} />
                            {j.appliedIndustryFallback && (
                              <span title="Industry-relaxed search (zero results with industry → retried without)" style={{
                                fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                                background: 'var(--bg-app)', color: 'var(--fg-muted)',
                                border: '1px solid var(--border-default)',
                              }}>
                                Industry-relaxed
                              </span>
                            )}
                          </div>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12, color: 'var(--fg-muted)' }}>
                            {j.jobLocation && <span><Icon name="map-pin" size={11} /> {j.jobLocation}</span>}
                            <span>Added {fmtDate(j.addedAt)}</span>
                            {j.lastSearchedAt && <span>Last search {fmtDate(j.lastSearchedAt)}</span>}
                          </div>
                          {j.searchError && (
                            <div style={{ marginTop: 6, fontSize: 12, color: 'var(--status-danger)' }}>
                              {j.searchError}
                            </div>
                          )}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexShrink: 0 }}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--status-success)' }}>{j.acceptedCount}</div>
                            <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Accepted</div>
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--status-danger)' }}>{j.rejectedCount}</div>
                            <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Rejected</div>
                          </div>
                          {!isAwaiting && (
                            <button
                              disabled={!canRerun || isBusy}
                              title={canRerun ? 'Re-run candidate search (appends new, skips already-rejected)' : 'Search in progress'}
                              onClick={(e) => { e.stopPropagation(); onRerun(j.jobId); }}
                              style={{
                                height: 32, padding: '0 12px', borderRadius: 6, fontSize: 12, fontWeight: 500,
                                cursor: canRerun && !isBusy ? 'pointer' : 'not-allowed',
                                border: '1px solid var(--border-card)', background: '#FFF',
                                color: canRerun ? 'var(--fg-primary)' : 'var(--fg-muted)',
                                fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 5,
                              }}
                            >
                              <Icon name={isBusy ? 'loader' : 'refresh-ccw'} size={12} /> Re-run
                            </button>
                          )}
                          <button
                            title="Remove this job from the pipeline"
                            disabled={isBusy}
                            onClick={(e) => { e.stopPropagation(); setConfirmRemove(j); }}
                            style={{
                              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              width: 30, height: 30, borderRadius: 6, cursor: isBusy ? 'not-allowed' : 'pointer',
                              border: '1px solid var(--border-card)', background: '#FFF',
                              color: 'var(--status-danger)',
                            }}
                          >
                            <Icon name="x" size={14} />
                          </button>
                          <button
                            disabled={!canOpen}
                            onClick={(e) => { e.stopPropagation(); router.push(`/candidates/${pipelineId}/jobs/${j.jobId}`); }}
                            title={isAwaiting ? 'Open the LinkedIn search questionnaire' : 'View candidates'}
                            style={{
                              height: 32, padding: '0 14px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                              cursor: canOpen ? 'pointer' : 'not-allowed', border: 'none',
                              background: canOpen ? 'var(--primary)' : 'var(--bg-app)',
                              color: canOpen ? '#FFF' : 'var(--fg-muted)',
                              fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6,
                            }}
                          >
                            {isAwaiting
                              ? <><Icon name="search" size={14} /> Search candidates</>
                              : <>View candidates <Icon name="chevron-right" size={14} /></>}
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
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

      {confirmRemove && (
        <div onClick={() => busyJob ? null : setConfirmRemove(null)} style={{
          position: 'fixed', inset: 0, zIndex: 70, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
        }}>
          <div onClick={(e) => e.stopPropagation()} style={{
            width: '100%', maxWidth: 440, background: '#FFF', borderRadius: 12, padding: 24,
            boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 36, height: 36, borderRadius: 9999,
                background: 'var(--status-danger)1A', color: 'var(--status-danger)',
              }}><Icon name="x" size={18} /></span>
              <div style={{ fontSize: 16, fontWeight: 600 }}>Remove job from pipeline?</div>
            </div>
            <div style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.5, marginBottom: 20 }}>
              <strong>{confirmRemove.jobTitle}</strong> and any candidates surfaced only by this job
              will be removed. Candidates surfaced by other jobs in this pipeline are kept.
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <button onClick={() => setConfirmRemove(null)} disabled={!!busyJob} style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                cursor: busyJob ? 'not-allowed' : 'pointer', border: '1px solid var(--border-card)',
                background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
              }}>Cancel</button>
              <button onClick={onRemove} disabled={!!busyJob} style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                cursor: busyJob ? 'not-allowed' : 'pointer', border: 'none',
                background: 'var(--status-danger)', color: '#FFF', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}>
                {busyJob ? <Icon name="loader" size={14} /> : <Icon name="x" size={14} />}
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
