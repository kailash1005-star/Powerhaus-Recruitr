'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { CandidateSlideOut } from '../CandidateSlideOut';
import {
  fetchPipelineCandidates, fetchPipeline, patchCandidate, enrichCandidate,
  fetchCandidate, bulkEnrichJobCandidates, runJobMatch,
  type Candidate, type Pipeline,
} from '@/lib/api';

interface Props { pipelineId: string; jobId: string }

type CandFilter = 'all' | 'accepted' | 'rejected';

const FILTER_OPTIONS: { key: CandFilter; label: string }[] = [
  { key: 'all', label: 'All Candidates' },
  { key: 'accepted', label: 'Accepted' },
  { key: 'rejected', label: 'Rejected' },
];

const ROWS_OPTIONS = [25, 50, 100];

// Backend only supports sorting by these two fields.
const SORT_FIELDS: Record<string, 'matchScore' | 'createdAt'> = {
  match: 'matchScore',
  added: 'createdAt',
};

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

function candidateName(c: Candidate): string {
  if (c.displayName && c.displayName.trim()) return c.displayName.trim();
  const ln = (c.lastName || '').trim();
  const fn = (c.firstName || '').trim();
  return `${fn} ${ln !== '—' && ln !== '-' ? ln : ''}`.trim() || '—';
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

function MatchBadge({ score }: { score: number }) {
  let style: React.CSSProperties;
  if (score >= 80)      style = { background: '#ECFDF5', color: '#059669', border: '1px solid #A7F3D0' };
  else if (score >= 60) style = { background: '#EFF6FF', color: '#2563EB', border: '1px solid #BFDBFE' };
  else if (score >= 40) style = { background: '#FFFBEB', color: '#D97706', border: '1px solid #FDE68A' };
  else                  style = { background: '#FEF2F2', color: '#DC2626', border: '1px solid #FECACA' };
  return (
    <span style={{ ...style, padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>
      {score}
    </span>
  );
}

export function PipelineJobCandidatesPage({ pipelineId, jobId }: Props) {
  const router = useRouter();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);

  // Bulk actions (enrich / run match) on the selected candidates.
  const [bulkBusy, setBulkBusy] = useState<null | 'enrich' | 'match'>(null);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);

  const [candFilter, setCandFilter] = useState<CandFilter>('all');
  const [page, setPage] = useState(1);
  const [rowsPerPage, setRowsPerPage] = useState(50);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);

  const [sortField, setSortField] = useState<string | null>('match');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hover, setHover] = useState<string | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Slide-out
  const [slideOpen, setSlideOpen] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  const jobEntry = useMemo(
    () => pipeline?.jobs.find((j) => j.jobId === jobId),
    [pipeline, jobId],
  );

  const loadPipeline = useCallback(async () => {
    try { setPipeline(await fetchPipeline(pipelineId)); } catch (e) { console.error(e); }
  }, [pipelineId]);

  const loadCandidates = useCallback(async () => {
    try {
      const sortBy = sortField ? SORT_FIELDS[sortField] : undefined;
      const c = await fetchPipelineCandidates(
        pipelineId, jobId, page, rowsPerPage, candFilter, sortBy, sortOrder,
      );
      setCandidates(c.candidates);
      setPages(c.pages);
      setTotal(c.total);
    } catch (e: any) {
      setActionError(e.message || 'Failed to load candidates');
    }
  }, [pipelineId, jobId, page, rowsPerPage, candFilter, sortField, sortOrder]);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadPipeline(), loadCandidates()]).finally(() => setLoading(false));
  }, [loadPipeline, loadCandidates]);

  const handleSort = (field: string) => {
    // Only match/added are server-sortable; others are no-ops.
    if (!SORT_FIELDS[field]) return;
    if (sortField === field) {
      setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortOrder('desc');
    }
    setPage(1);
  };

  const handleSelectAll = () => {
    if (selected.size === candidates.length && candidates.length > 0) {
      setSelected(new Set());
    } else {
      setSelected(new Set(candidates.map((c) => c._id)));
    }
  };

  const toggleSelect = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const applyPatch = async (id: string, body: { isAccepted?: boolean; rejectionReason?: string | null }) => {
    setBusyId(id);
    setActionError(null);
    try {
      const updated = await patchCandidate(id, body);
      setCandidates((prev) => prev.map((c) => (c._id === id ? updated : c)));
    } catch (e: any) {
      setActionError(e.message || 'Failed to update candidate');
    } finally {
      setBusyId(null);
    }
  };

  const toggleAccept = (c: Candidate) =>
    applyPatch(c._id, c.isAccepted
      ? { isAccepted: false, rejectionReason: 'Manual reject' }
      : { isAccepted: true, rejectionReason: null });

  // Candidates whose background Apify stage we're currently polling (dedupe).
  const apifyPollRef = useRef<Set<string>>(new Set());

  // Poll a single candidate until the background Apify stage settles, refreshing
  // its row (which the open slide-out reads through, so the deep profile appears
  // live). Bounded so a stuck job can't poll forever.
  const pollApify = useCallback((id: string) => {
    if (apifyPollRef.current.has(id)) return;
    apifyPollRef.current.add(id);
    (async () => {
      try {
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          let fresh: Candidate;
          try { fresh = await fetchCandidate(id); } catch { continue; }
          setCandidates((prev) => prev.map((c) => (c._id === id ? fresh : c)));
          if (fresh.apifyEnrichmentStatus && fresh.apifyEnrichmentStatus !== 'pending') break;
        }
      } finally {
        apifyPollRef.current.delete(id);
      }
    })();
  }, []);

  const onEnrich = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      const updated = await enrichCandidate(id);
      setCandidates((prev) => prev.map((c) => (c._id === id ? updated : c)));
      // Apollo done; the deep Apify profile continues in the background — poll it.
      if (updated.apifyEnrichmentStatus === 'pending') pollApify(id);
    } catch (e: any) {
      setActionError(e.message || 'Enrichment failed');
    } finally {
      setBusyId(null);
    }
  };

  // Poll the pipeline until this job's enrichStatus settles, refreshing rows.
  const pollEnrich = useCallback(async () => {
    for (let i = 0; i < 150; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      let p: Pipeline | null = null;
      try { p = await fetchPipeline(pipelineId); } catch { continue; }
      setPipeline(p);
      const je = p.jobs.find((j) => j.jobId === jobId);
      const st = je?.enrichStatus;
      const c = je?.enrichCounts || {};
      if (st === 'running' || st === 'queued') {
        setBulkMsg(`Enriching… (Apollo ${c.apollo_enriched ?? 0} · Apify ${c.apify_enriched ?? 0})`);
      }
      if (st === 'completed') {
        setBulkMsg(`Enriched ✓ — Apollo ${c.apollo_enriched ?? 0}, Apify ${c.apify_enriched ?? 0}${c.not_found ? `, not found ${c.not_found}` : ''}`);
        await loadCandidates();
        return;
      }
      if (st === 'failed') { setBulkMsg(`Enrichment failed: ${je?.enrichError || 'unknown error'}`); return; }
    }
    setBulkMsg('Enrichment is taking longer than expected — refresh to check.');
  }, [pipelineId, jobId, loadCandidates]);

  const onBulkEnrich = async () => {
    if (selected.size === 0) return;
    setBulkBusy('enrich');
    setActionError(null);
    setBulkMsg(`Queuing enrichment for ${selected.size} candidate(s)…`);
    try {
      await bulkEnrichJobCandidates(pipelineId, jobId, Array.from(selected));
      await pollEnrich();
    } catch (e: any) {
      setActionError(e.message || 'Bulk enrichment failed');
      setBulkMsg(null);
    } finally {
      setBulkBusy(null);
    }
  };

  const onRunMatch = async () => {
    if (selected.size === 0) return;
    setBulkBusy('match');
    setActionError(null);
    setBulkMsg(`Starting match for ${selected.size} candidate(s)…`);
    try {
      const { matchRunId } = await runJobMatch(pipelineId, jobId, Array.from(selected));
      router.push(`/matching/${matchRunId}`);
    } catch (e: any) {
      setActionError(e.message || 'Failed to start match');
      setBulkMsg(null);
      setBulkBusy(null);
    }
  };

  const openSlideOut = (id: string) => {
    setActiveId(id);
    setSlideOpen(true);
  };

  const chipStyle = (active: boolean): React.CSSProperties => ({
    padding: '5px 12px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    border: '1px solid',
    borderColor: active ? 'var(--primary)' : 'var(--border-card)',
    background: active ? 'var(--primary)' : 'var(--bg-app)',
    color: active ? '#FFF' : 'var(--fg-secondary)',
    transition: 'all 120ms',
    fontFamily: 'inherit',
  });

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

  const startRow = (page - 1) * rowsPerPage + 1;
  const endRow = Math.min(page * rowsPerPage, total);

  if (loading && !pipeline) {
    return (
      <>
        <TopBar title="Candidates" showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)' }}>
          <div style={{ textAlign: 'center' }}>
            <Icon name="loader" size={24} />
            <div style={{ marginTop: 12, fontSize: 14 }}>Loading candidates...</div>
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
            href={`/candidates/${pipelineId}`}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', textDecoration: 'none' }}
          >
            <Icon name="arrow-left" size={16} />
            Back to {pipeline?.companyName || 'pipeline'}
          </Link>
        }
        showSearch={false}
        actions={
          jobEntry?.appliedIndustryFallback ? (
            <span title="Zero results with industry → retried without industry" style={{
              fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 8,
              background: '#FFFBEB', color: '#D97706', border: '1px solid #FDE68A',
            }}>
              Industry-relaxed search
            </span>
          ) : undefined
        }
      />

      {/* Job title strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '12px 24px',
        borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)',
      }}>
        <span style={{ width: 26, height: 26, borderRadius: 6, background: '#EEF2FF', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Icon name="briefcase" size={13} style={{ color: '#4F46E5' }} />
        </span>
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)' }}>
          {jobEntry?.jobTitle || 'Candidates'}
        </span>
      </div>

      {/* Filter strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)', flexWrap: 'wrap',
      }}>
        {FILTER_OPTIONS.map((f) => (
          <button
            key={f.key}
            onClick={() => { setCandFilter(f.key); setPage(1); setSelected(new Set()); }}
            style={chipStyle(candFilter === f.key)}
          >
            {f.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        {selected.size > 0 && (
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginRight: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--fg-muted)', fontWeight: 600 }}>
              {selected.size} selected
            </span>
            <button
              onClick={onBulkEnrich}
              disabled={bulkBusy !== null}
              title="Apollo /people/match → Apify deep profile for the selected candidates (background). Skips already-enriched."
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
                borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: 'inherit',
                cursor: bulkBusy ? 'not-allowed' : 'pointer', border: '1px solid var(--primary)',
                background: '#FFF', color: 'var(--primary)', opacity: bulkBusy ? 0.6 : 1,
              }}
            >
              <Icon name={bulkBusy === 'enrich' ? 'loader' : 'sparkles'} size={13} />
              Enrich ({selected.size})
            </button>
            <button
              onClick={onRunMatch}
              disabled={bulkBusy !== null}
              title="Score this job's JD against the selected candidates (auto-enriches any that aren't yet)."
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
                borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: 'inherit',
                cursor: bulkBusy ? 'not-allowed' : 'pointer', border: 'none',
                background: 'var(--primary)', color: '#FFF', opacity: bulkBusy ? 0.6 : 1,
              }}
            >
              <Icon name={bulkBusy === 'match' ? 'loader' : 'target'} size={13} />
              Run Match ({selected.size})
            </button>
          </div>
        )}
        <span style={{ fontSize: 12, color: 'var(--fg-muted)', fontWeight: 500 }}>
          <span style={{ fontWeight: 700, color: 'var(--fg-primary)' }}>{total.toLocaleString()}</span> total candidates
        </span>
      </div>
      {bulkMsg && (
        <div style={{
          padding: '8px 24px', fontSize: 12, color: 'var(--fg-secondary)',
          background: '#F8FAFC', borderBottom: '1px solid var(--border-default)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          {bulkBusy && <Icon name="loader" size={13} />}
          {bulkMsg}
        </div>
      )}

      {/* Candidates table */}
      <div style={{ flex: 1, overflow: 'auto', background: '#FFF' }}>
        {candidates.length === 0 ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '80px 24px', color: 'var(--fg-muted)' }}>
            <div style={{ textAlign: 'center' }}>
              {loading ? (
                <Icon name="loader" size={24} />
              ) : (
                <>
                  <Icon name="users" size={36} style={{ marginBottom: 16 }} />
                  <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)', marginBottom: 6 }}>
                    {jobEntry?.searchStatus === 'queued' || jobEntry?.searchStatus === 'running'
                      ? 'Search in progress…'
                      : 'No candidates found'}
                  </div>
                  <div style={{ fontSize: 13 }}>No candidates match the current filter.</div>
                </>
              )}
            </div>
          </div>
        ) : (
          <table style={{ width: '100%', minWidth: 1000, borderCollapse: 'separate', borderSpacing: 0 }}>
            <thead style={{ position: 'sticky', top: 0, zIndex: 10 }}>
              <tr>
                <th style={{ ...thStyle, width: 44 }}>
                  <input
                    type="checkbox"
                    checked={selected.size === candidates.length && candidates.length > 0}
                    onChange={handleSelectAll}
                    style={{ cursor: 'pointer' }}
                  />
                </th>
                <th style={{ ...thStyle, width: 240 }}>Candidate</th>
                <th style={{ ...thStyle, width: 180 }}>Current Company</th>
                <th style={{ ...thStyle, width: 200 }}>Current Role</th>
                <th
                  style={{ ...thStyle, width: 100, cursor: 'pointer' }}
                  onClick={() => handleSort('added')}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    Added
                    <SortIcon active={sortField === 'added'} order={sortOrder} />
                  </span>
                </th>
                <th style={{ ...thStyle, width: 150 }}>Location</th>
                <th
                  style={{ ...thStyle, width: 90, cursor: 'pointer' }}
                  onClick={() => handleSort('match')}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    Match
                    <SortIcon active={sortField === 'match'} order={sortOrder} />
                  </span>
                </th>
                <th style={{ ...thStyle, width: 110 }}>Status</th>
                <th style={{ ...thStyle, width: 200, textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => {
                const isSelected = selected.has(c._id);
                const isHov = hover === c._id;
                const rowBg = isSelected
                  ? '#EFF6FF'
                  : isHov
                  ? '#F9FAFB'
                  : c.isAccepted
                  ? '#F0FDF4'
                  : '#FFF5F5';

                return (
                  <tr
                    key={c._id}
                    style={{ background: rowBg, transition: 'background 100ms', cursor: 'pointer' }}
                    onClick={() => openSlideOut(c._id)}
                    onMouseEnter={() => setHover(c._id)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <td style={{ ...tdStyle, width: 44 }} onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelect(c._id)}
                        style={{ cursor: 'pointer' }}
                      />
                    </td>
                    <td style={{ ...tdStyle, fontWeight: 600, width: 240, maxWidth: 240 }}>
                      {c.externalLinkedinUrl ? (
                        <a
                          href={c.externalLinkedinUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            color: 'var(--status-info)', textDecoration: 'none', fontWeight: 600,
                            display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220,
                          }}
                          onMouseOver={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = 'underline')}
                          onMouseOut={(e) => ((e.currentTarget as HTMLElement).style.textDecoration = 'none')}
                          title={candidateName(c)}
                        >
                          {candidateName(c)}
                        </a>
                      ) : (
                        <span
                          style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}
                          title={candidateName(c)}
                        >
                          {candidateName(c)}
                        </span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--fg-secondary)' }}>
                        <span style={{
                          width: 22, height: 22, borderRadius: 5, background: '#F3F4F6',
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                        }}>
                          <Icon name="building-2" size={12} style={{ color: 'var(--fg-muted)' }} />
                        </span>
                        <span style={{ maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {c.currentCompany || '—'}
                        </span>
                      </span>
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--fg-secondary)', width: 200, maxWidth: 200 }}>
                      <span
                        style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 180 }}
                        title={c.currentTitle || c.headline || '—'}
                      >
                        {c.currentTitle || c.headline || '—'}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, color: 'var(--fg-secondary)' }}>
                      {formatDate(c.createdAt)}
                    </td>
                    <td style={tdStyle}>
                      <span style={{
                        display: 'inline-block', background: '#F3F4F6', padding: '2px 8px',
                        borderRadius: 4, fontSize: 11, fontWeight: 500, color: 'var(--fg-secondary)',
                        maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis',
                      }} title={c.location || 'Unknown'}>
                        {c.location || 'Unknown'}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <MatchBadge score={c.matchScore} />
                    </td>
                    <td style={tdStyle}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', gap: 5,
                          padding: '2px 8px', borderRadius: 9999, fontSize: 11, fontWeight: 600,
                          background: c.isAccepted ? 'var(--status-success)1A' : 'var(--status-danger)1A',
                          color: c.isAccepted ? 'var(--status-success)' : 'var(--status-danger)',
                          border: `1px solid ${c.isAccepted ? 'var(--status-success)40' : 'var(--status-danger)40'}`,
                        }}>
                          <span style={{ width: 6, height: 6, borderRadius: 9999, background: 'currentColor', flexShrink: 0 }} />
                          {c.isAccepted ? 'Accepted' : 'Rejected'}
                        </span>
                        {c.isApifyEnriched && (
                          <span title="Deep LinkedIn profile enriched (Apify)" style={{
                            display: 'inline-flex', alignItems: 'center', gap: 3,
                            padding: '2px 7px', borderRadius: 9999, fontSize: 10, fontWeight: 700,
                            background: '#EEF2FF', color: '#4F46E5', border: '1px solid #C7D2FE',
                          }}>
                            <Icon name="sparkles" size={10} />Profile
                          </span>
                        )}
                      </span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: 'right' }} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                        {/* Accept / Reject toggle */}
                        <button
                          disabled={busyId === c._id}
                          onClick={() => toggleAccept(c)}
                          title={c.isAccepted ? 'Reject candidate' : 'Accept candidate'}
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: 5,
                            padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                            cursor: busyId === c._id ? 'not-allowed' : 'pointer',
                            border: `1px solid ${c.isAccepted ? 'var(--status-danger)40' : 'var(--status-success)40'}`,
                            background: c.isAccepted ? 'var(--status-danger)1A' : 'var(--status-success)1A',
                            color: c.isAccepted ? 'var(--status-danger)' : 'var(--status-success)',
                            fontFamily: 'inherit',
                          }}
                        >
                          <Icon name={c.isAccepted ? 'x' : 'check'} size={12} />
                          {c.isAccepted ? 'Reject' : 'Accept'}
                        </button>
                        {/* Enrich */}
                        <button
                          disabled={busyId === c._id || c.isEnriched}
                          onClick={() => onEnrich(c._id)}
                          title={c.isEnriched ? 'Already enriched — open to view Apollo data' : 'Pull full profile from Apollo'}
                          style={{
                            display: 'inline-flex', alignItems: 'center', gap: 5,
                            padding: '5px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                            cursor: busyId === c._id || c.isEnriched ? 'not-allowed' : 'pointer',
                            border: 'none',
                            background: c.isEnriched ? 'var(--bg-app)' : 'var(--primary)',
                            color: c.isEnriched ? 'var(--fg-muted)' : '#FFF',
                            fontFamily: 'inherit',
                          }}
                        >
                          {busyId === c._id ? <Icon name="loader" size={12} /> : <Icon name="sparkles" size={12} />}
                          {c.isEnriched ? 'Enriched' : 'Enrich'}
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
      {total > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 24px', borderTop: '1px solid var(--border-default)',
          background: '#FFF', flexShrink: 0,
        }}>
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
              <strong style={{ color: 'var(--fg-primary)' }}>{total.toLocaleString()}</strong> results
            </span>
          </div>

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

            {Array.from({ length: Math.min(5, pages) }, (_, i) => {
              let pageNum: number;
              if (pages <= 5) {
                pageNum = i + 1;
              } else if (page <= 3) {
                pageNum = i + 1;
              } else if (page >= pages - 2) {
                pageNum = pages - 4 + i;
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
              disabled={page >= pages}
              onClick={() => setPage((p) => p + 1)}
              style={{
                width: 28, height: 28, borderRadius: 6, border: '1px solid var(--border-card)',
                background: '#FFF', cursor: page >= pages ? 'not-allowed' : 'pointer',
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                color: page >= pages ? 'var(--fg-muted)' : 'var(--fg-primary)',
              }}
            >
              <Icon name="chevron-right" size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Candidate slide-out — Apollo enrichment view */}
      <CandidateSlideOut
        isOpen={slideOpen}
        onClose={() => setSlideOpen(false)}
        candidates={candidates}
        activeId={activeId}
        setActiveId={setActiveId}
        jobTitle={jobEntry?.jobTitle || 'Candidates'}
        companyName={pipeline?.companyName || ''}
        busyId={busyId}
        onEnrich={onEnrich}
        onToggleAccept={toggleAccept}
      />

      {actionError && (
        <div style={{
          position: 'fixed', bottom: 20, right: 20, zIndex: 120,
          padding: '12px 16px', background: '#FEF2F2', border: '1px solid #FECACA',
          borderRadius: 8, fontSize: 13, color: '#B91C1C', maxWidth: 380,
          boxShadow: '0 6px 24px rgba(0,0,0,0.12)',
        }}>{actionError}</div>
      )}
    </>
  );
}
