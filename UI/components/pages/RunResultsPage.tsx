'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { ProspectsSlideOut } from '../ProspectsSlideOut';
import { AddToPipelineModal } from '../AddToPipelineModal';
import {
  fetchRun,
  fetchRunJobs,
  fetchEnrichmentCredits,
  fetchCompany,
  type Run,
  type RunJob,
  type RunJobsResponse,
  type EnrichmentCreditStatus,
} from '@/lib/api';
import { useRouter } from 'next/navigation';

interface RunResultsPageProps {
  runId: string;
}

const ROWS_OPTIONS = [25, 50, 100];

function formatDate(dateStr?: string | null) {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

function SortIcon({ active, order }: { active: boolean; order: 'asc' | 'desc' }) {
  if (!active) return null;
  return (
    <Icon
      name={order === 'asc' ? 'chevron-up' : 'chevron-down'}
      size={12}
      style={{ color: 'var(--status-info)', marginLeft: 4 }}
    />
  );
}

function QualityBadge({ status }: { status: RunJob['qualityStatus'] }) {
  const map: Record<string, React.CSSProperties> = {
    excellent: { background: '#ECFDF5', color: '#059669', border: '1px solid #A7F3D0' },
    good:      { background: '#EFF6FF', color: '#2563EB', border: '1px solid #BFDBFE' },
    fair:      { background: '#FFFBEB', color: '#D97706', border: '1px solid #FDE68A' },
    poor:      { background: '#FEF2F2', color: '#DC2626', border: '1px solid #FECACA' },
  };
  const style = map[status] ?? { background: '#F3F4F6', color: '#6B7280', border: '1px solid #E5E7EB' };
  return (
    <span style={{ ...style, padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600, textTransform: 'capitalize' }}>
      {status}
    </span>
  );
}

export function RunResultsPage({ runId }: RunResultsPageProps) {
  const [run, setRun] = useState<Run | null>(null);
  const [jobsResp, setJobsResp] = useState<RunJobsResponse | null>(null);
  const [creditStatus, setCreditStatus] = useState<EnrichmentCreditStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const [page, setPage] = useState(1);
  const [rowsPerPage, setRowsPerPage] = useState(50);

  const [sortField, setSortField] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<string | null>(null);

  // Prospects slide-out
  const [slideOutJob, setSlideOutJob] = useState<{ id: string; title: string; company: string } | null>(null);

  // Add-to-pipeline modal state
  const router = useRouter();
  const [addToPipelineJob, setAddToPipelineJob] = useState<RunJob | null>(null);
  const [pipelineCompanyDefaults, setPipelineCompanyDefaults] = useState<{
    companyName?: string; companyDomain?: string; companyIndustry?: string;
    matchedIndustry?: string | null; companyLocation?: string;
    linkedinSlug?: string | null; website?: string;
  } | undefined>(undefined);

  const openAddToPipeline = useCallback(async (job: RunJob) => {
    // Prefill the modal from the Phase-2-resolved company doc when available
    let defaults: typeof pipelineCompanyDefaults = {
      companyName: job.company,
      companyLocation: job.location,
    };
    if (job.companyId) {
      try {
        const co = await fetchCompany(job.companyId);
        defaults = {
          companyName: co.companyName || job.company,
          companyDomain: co.companyDomain || '',
          companyIndustry: (co.industry as string) || (co.companyIndustry as string) || '',
          matchedIndustry: co.matchedIndustry,
          companyLocation: (co.location as string) || '',
          linkedinSlug: co.linkedinSlug,
          website: (co.website as string) || '',
        };
      } catch { /* fall back to defaults */ }
    }
    setPipelineCompanyDefaults(defaults);
    setAddToPipelineJob(job);
  }, []);

  const loadRun = useCallback(async () => {
    try { setRun(await fetchRun(runId)); } catch (e) { console.error(e); }
  }, [runId]);

  const loadJobs = useCallback(async () => {
    try {
      // Results screen is accepted-only; the backend enforces this too.
      setJobsResp(await fetchRunJobs(runId, page, rowsPerPage, 'good', sortField ?? undefined, sortOrder));
    } catch (e) { console.error(e); }
  }, [runId, page, rowsPerPage, sortField, sortOrder]);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadRun(), loadJobs()]).finally(() => setLoading(false));
  }, [loadRun, loadJobs]);

  useEffect(() => {
    fetchEnrichmentCredits(runId).then(setCreditStatus).catch(() => {});
  }, [runId]);

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder('asc');
    }
    setPage(1);
  };

  const handleSelectAll = () => {
    const jobs = jobsResp?.jobs ?? [];
    if (selectedJobs.size === jobs.length && jobs.length > 0) {
      setSelectedJobs(new Set());
    } else {
      setSelectedJobs(new Set(jobs.map((j) => j._id)));
    }
  };

  const toggleJob = (id: string) => {
    const next = new Set(selectedJobs);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedJobs(next);
  };

  const thStyle: React.CSSProperties = {
    textAlign: 'left',
    fontSize: 11,
    fontWeight: 600,
    color: 'var(--fg-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    padding: '10px 16px',
    borderBottom: '1px solid var(--border-default)',
    background: '#FAFAFA',
    whiteSpace: 'nowrap',
    userSelect: 'none',
  };

  const tdStyle: React.CSSProperties = {
    padding: '0 16px',
    height: 48,
    borderBottom: '1px solid #F3F4F6',
    verticalAlign: 'middle',
    whiteSpace: 'nowrap',
    fontSize: 13,
    color: 'var(--fg-primary)',
  };

  const jobs = jobsResp?.jobs ?? [];
  const totalJobs = jobsResp?.total ?? 0;
  const totalPages = jobsResp?.pages ?? 1;
  const startRow = (page - 1) * rowsPerPage + 1;
  const endRow = Math.min(page * rowsPerPage, totalJobs);

  const creditPct =
    creditStatus && creditStatus.dailyLimit > 0
      ? Math.round((creditStatus.creditsUsed / creditStatus.dailyLimit) * 100)
      : 0;

  if (loading && !run) {
    return (
      <>
        <TopBar title="Run Results" showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)' }}>
          <div style={{ textAlign: 'center' }}>
            <Icon name="loader" size={24} />
            <div style={{ marginTop: 12, fontSize: 14 }}>Loading results...</div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar
        titleNode={
          <Link
            href={`/runs/${runId}`}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', textDecoration: 'none' }}
          >
            <Icon name="arrow-left" size={16} />
            Back to Run
          </Link>
        }
        showSearch={false}
        actions={
          <>
            {/* Credit pill */}
            {creditStatus && (
              <div style={{
                display: 'inline-flex', alignItems: 'center', gap: 8,
                padding: '4px 10px', borderRadius: 8, fontSize: 11, fontWeight: 700,
                background: '#ECFDF5', border: '1px solid #A7F3D0', color: '#059669',
              }}>
                <div style={{ width: 48, height: 4, background: '#D1FAE5', borderRadius: 9999, overflow: 'hidden' }}>
                  <div style={{ width: `${Math.min(creditPct, 100)}%`, height: '100%', background: '#10B981', borderRadius: 9999 }} />
                </div>
                {creditStatus.creditsUsed}/{creditStatus.dailyLimit} credits
              </div>
            )}

            <button
              onClick={() => alert('Check Status — coming soon')}
              style={{
                height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                cursor: 'pointer', border: '1px solid var(--border-card)', background: '#FFF',
                color: 'var(--fg-secondary)', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              <Icon name="activity" size={14} /> Check Status
            </button>

            <button
              onClick={() => alert('Email outreach is not enabled in this iteration.')}
              style={{
                height: 32, padding: '0 14px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                cursor: 'pointer', border: 'none', background: 'var(--primary)', color: '#FFF',
                fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              <Icon name="mail" size={14} /> Trigger Email Flow
            </button>
          </>
        }
      />

      {/* Jobs table — accepted jobs only. minHeight:0 lets this flex child
          shrink so the table body (not the page) owns the vertical scroll. */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', background: '#FFF' }}>
        {jobs.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '80px 24px', color: 'var(--fg-muted)' }}>
            <div style={{ textAlign: 'center' }}>
              {loading ? (
                <Icon name="loader" size={24} />
              ) : (
                <>
                  <Icon name="search" size={36} style={{ marginBottom: 16 }} />
                  <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)', marginBottom: 6 }}>No accepted jobs</div>
                  <div style={{ fontSize: 13 }}>This run has no accepted jobs yet.</div>
                </>
              )}
            </div>
          </div>
        ) : (
          <table style={{ width: '100%', minWidth: 1000, borderCollapse: 'separate', borderSpacing: 0 }}>
            <thead style={{ position: 'sticky', top: 0, zIndex: 10 }}>
              <tr>
                {/* Checkbox */}
                <th style={{ ...thStyle, width: 44 }}>
                  <input
                    type="checkbox"
                    checked={selectedJobs.size === jobs.length && jobs.length > 0}
                    onChange={handleSelectAll}
                    style={{ cursor: 'pointer' }}
                  />
                </th>
                {/* Sortable headers */}
                {[
                  { field: 'title', label: 'Job Title', width: 280 },
                  { field: 'company', label: 'Company', width: 160 },
                  { field: 'industry', label: 'Industry', width: 110 },
                ].map(({ field, label, width }) => (
                  <th
                    key={field}
                    style={{ ...thStyle, width, maxWidth: width, cursor: 'pointer' }}
                    onClick={() => handleSort(field)}
                  >
                    <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                      {label}
                      <SortIcon active={sortField === field} order={sortOrder} />
                    </span>
                  </th>
                ))}
                <th style={{ ...thStyle, width: 110 }}>Posted</th>
                <th
                  style={{ ...thStyle, width: 140, cursor: 'pointer' }}
                  onClick={() => handleSort('location')}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    Location
                    <SortIcon active={sortField === 'location'} order={sortOrder} />
                  </span>
                </th>
                <th style={{ ...thStyle, width: 110 }}>Quality</th>
                <th style={{ ...thStyle, width: 120 }}>Outreach</th>
                <th style={{ ...thStyle, width: 120, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const isSelected = selectedJobs.has(job._id);
                const isHov = hover === job._id;
                const rowBg = isSelected
                  ? '#EFF6FF'
                  : isHov
                  ? '#F9FAFB'
                  : job.qualityStatus === 'good' || job.qualityStatus === 'excellent'
                  ? '#F0FDF4'
                  : job.qualityStatus === 'poor'
                  ? '#FFF5F5'
                  : '#FFFFFF';

                return (
                  <tr
                    key={job._id}
                    style={{ background: rowBg, transition: 'background 100ms', cursor: 'default' }}
                    onMouseEnter={() => setHover(job._id)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <td style={{ ...tdStyle, width: 44 }}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleJob(job._id)}
                        style={{ cursor: 'pointer' }}
                      />
                    </td>
                    <td style={{ ...tdStyle, fontWeight: 600, width: 280, maxWidth: 280 }}>
                      {job.jobDetails?.jobUrl ? (
                        <a
                          href={job.jobDetails.jobUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{
                            color: 'var(--status-info)', textDecoration: 'none', fontWeight: 600,
                            display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260,
                          }}
                          onMouseOver={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = 'underline')}
                          onMouseOut={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = 'none')}
                          title={job.title}
                        >
                          {job.title}
                        </a>
                      ) : (
                        <span
                          style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }}
                          title={job.title}
                        >
                          {job.title}
                        </span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      {job.jobDetails?.companyUrl ? (
                        <a
                          href={job.jobDetails.companyUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--status-info)', textDecoration: 'none' }}
                          title={job.company}
                        >
                          <span style={{
                            width: 22, height: 22, borderRadius: 5, background: '#EEF2FF',
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                          }}>
                            <Icon name="building-2" size={12} style={{ color: '#4F46E5' }} />
                          </span>
                          <span style={{ fontWeight: 500, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {job.company}
                          </span>
                        </a>
                      ) : (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--fg-secondary)' }}>
                          <span style={{
                            width: 22, height: 22, borderRadius: 5, background: '#F3F4F6',
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                          }}>
                            <Icon name="building-2" size={12} style={{ color: 'var(--fg-muted)' }} />
                          </span>
                          {job.company}
                        </span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--fg-secondary)', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', width: 110, maxWidth: 110 }}>
                      <span
                        style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 92 }}
                        title={job.industry || '—'}
                      >
                        {job.industry || '—'}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--fg-secondary)' }}>
                      {formatDate(job.postedDate || job.createdAt)}
                    </td>
                    <td style={tdStyle}>
                      <span style={{
                        display: 'inline-block', background: '#F3F4F6', padding: '2px 8px',
                        borderRadius: 4, fontSize: 11, fontWeight: 500, color: 'var(--fg-secondary)',
                        maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis',
                      }} title={job.location || 'Unknown'}>
                        {job.location || 'Unknown'}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <QualityBadge status={job.qualityStatus} />
                    </td>
                    <td style={tdStyle}>
                      {job.outreachCount && job.outreachCount > 0 ? (
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, fontWeight: 700, color: '#059669' }}>
                          <span style={{ width: 6, height: 6, borderRadius: 9999, background: '#10B981', flexShrink: 0 }} />
                          {job.outreachCount} Sent
                        </span>
                      ) : (
                        <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--fg-muted)' }}>Pending</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }}>
                      <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                        {/* Add to candidate pipeline — only enable for accepted jobs */}
                        <button
                          disabled={job.qualityStatus !== 'good'}
                          title={
                            job.qualityStatus !== 'good'
                              ? 'Only accepted jobs can be added to a candidate pipeline'
                              : job.inPipeline
                                ? `Already in pipeline — open ${job.inPipelineCompany ?? ''}`
                                : 'Add this job to a candidate pipeline'
                          }
                          onClick={() => {
                            if (job.inPipeline && job.inPipelineId) {
                              router.push(`/candidates/${job.inPipelineId}`);
                            } else {
                              openAddToPipeline(job);
                            }
                          }}
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: 5,
                            padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                            cursor: job.qualityStatus !== 'good' ? 'not-allowed' : 'pointer',
                            border: '1px solid var(--border-card)',
                            background: job.inPipeline ? 'var(--status-success)1A' : '#FFF',
                            color: job.qualityStatus !== 'good'
                              ? 'var(--fg-muted)'
                              : job.inPipeline ? 'var(--status-success)' : 'var(--fg-primary)',
                            fontFamily: 'inherit',
                          }}
                        >
                          <Icon name={job.inPipeline ? 'check' : 'user-plus'} size={12} />
                          {job.inPipeline ? 'In pipeline' : 'Add'}
                        </button>
                        <button
                          onClick={() => setSlideOutJob({ id: job._id, title: job.title, company: job.company })}
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: 5,
                            padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                            cursor: 'pointer', border: 'none',
                            background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
                            transition: 'opacity 120ms',
                          }}
                          onMouseOver={(e) => ((e.currentTarget as HTMLElement).style.opacity = '0.85')}
                          onMouseOut={(e) => ((e.currentTarget as HTMLElement).style.opacity = '1')}
                        >
                          <Icon name="users" size={12} />
                          {job.prospectCount && job.prospectCount > 0 ? `${job.prospectCount} Prospects` : 'Prospects'}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination footer */}
      {totalJobs > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 24px', borderTop: '1px solid var(--border-default)',
          background: '#FFF', flexShrink: 0,
        }}>
          {/* Rows per page + range */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 11, color: 'var(--fg-muted)', fontWeight: 500 }}>
            <span>Rows:</span>
            <select
              value={rowsPerPage}
              onChange={(e) => { setRowsPerPage(Number(e.target.value)); setPage(1); }}
              style={{
                height: 26, padding: '0 4px', borderRadius: 5, border: '1px solid var(--border-card)',
                fontSize: 11, fontWeight: 700, color: 'var(--fg-primary)', background: '#FFF',
                cursor: 'pointer', fontFamily: 'inherit',
              }}
            >
              {ROWS_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span style={{ borderLeft: '1px solid var(--border-card)', paddingLeft: 12 }}>
              {startRow.toLocaleString()}–{endRow.toLocaleString()} of{' '}
              <strong style={{ color: 'var(--fg-primary)' }}>{totalJobs.toLocaleString()}</strong> results
            </span>
          </div>

          {/* Page buttons */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              style={{
                width: 28, height: 28, borderRadius: 6, border: '1px solid var(--border-card)',
                background: '#FFF', cursor: page === 1 ? 'not-allowed' : 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                color: page === 1 ? 'var(--fg-muted)' : 'var(--fg-primary)',
              }}
            >
              <Icon name="chevron-left" size={14} />
            </button>

            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              // Show pages centered around current page
              let pageNum: number;
              if (totalPages <= 5) {
                pageNum = i + 1;
              } else if (page <= 3) {
                pageNum = i + 1;
              } else if (page >= totalPages - 2) {
                pageNum = totalPages - 4 + i;
              } else {
                pageNum = page - 2 + i;
              }
              const isActive = pageNum === page;
              return (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  style={{
                    width: 28, height: 28, borderRadius: 6, fontSize: 11, fontWeight: 700,
                    cursor: 'pointer', border: isActive ? 'none' : '1px solid var(--border-card)',
                    background: isActive ? 'var(--primary)' : '#FFF',
                    color: isActive ? '#FFF' : 'var(--fg-secondary)',
                    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    fontFamily: 'inherit', transition: 'all 120ms',
                  }}
                >
                  {pageNum}
                </button>
              );
            })}

            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              style={{
                width: 28, height: 28, borderRadius: 6, border: '1px solid var(--border-card)',
                background: '#FFF', cursor: page >= totalPages ? 'not-allowed' : 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                color: page >= totalPages ? 'var(--fg-muted)' : 'var(--fg-primary)',
              }}
            >
              <Icon name="chevron-right" size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Prospects slide-out */}
      <ProspectsSlideOut
        isOpen={slideOutJob !== null}
        onClose={() => setSlideOutJob(null)}
        jobId={slideOutJob?.id ?? null}
        jobTitle={slideOutJob?.title ?? ''}
        companyName={slideOutJob?.company ?? ''}
        runId={runId}
      />

      {/* Add-to-candidate-pipeline modal */}
      <AddToPipelineModal
        isOpen={addToPipelineJob !== null}
        onClose={() => setAddToPipelineJob(null)}
        job={addToPipelineJob}
        companyDefaults={pipelineCompanyDefaults}
        onAdded={(pipelineId) => {
          setAddToPipelineJob(null);
          // Refresh the jobs list so the row flips to "In pipeline"
          loadJobs();
          // Soft toast via router push? Keep it simple — navigate to the pipeline.
          router.push(`/candidates/${pipelineId}`);
        }}
      />

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
