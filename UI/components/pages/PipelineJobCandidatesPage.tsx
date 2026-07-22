'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { bandFor } from '../matching/shared';
import { CandidateSlideOut } from '../CandidateSlideOut';
import { CandidateDiscoveryForm } from '../CandidateDiscoveryForm';
import { CandidateColumnFilter } from '../CandidateColumnFilter';
import {
  fetchPipelineCandidates, fetchPipeline, patchCandidate,
  fetchCandidate, bulkEnrichJobCandidates, runJobMatch, fetchCandidateFacets,
  fetchJobRequirements, discoverJobCandidates, deleteJobCandidates,
  type Candidate, type Pipeline, type PipelineJob, type CandidateFilters, type CandidateFacets,
  type DiscoverFilters, type EnrichMode,
} from '@/lib/api';

interface Props { pipelineId: string; jobId: string }

const ROWS_OPTIONS = [25, 50, 100];

// One enrichment click = one Apify actor run (runs, not dollars, are the free
// tier's scarce resource). Mirrors the backend's JOB_ENRICH_SELECTION_MAX —
// the server enforces it too; this just keeps the UI honest up front.
const ENRICH_MAX = 10;

const EMPTY_FACETS: CandidateFacets = { companies: [], locations: [], status: [] };

const STATUS_LABEL: Record<string, string> = { accepted: 'Accepted', rejected: 'Rejected' };

/** Human summary of one active filter, for the chips above the table. */
function describeFilter(key: keyof CandidateFilters, f: CandidateFilters): string | null {
  switch (key) {
    case 'name':      return f.name ? `Candidate contains "${f.name}"` : null;
    case 'role':      return f.role ? `Role contains "${f.role}"` : null;
    case 'companies': return f.companies?.length ? `Company: ${f.companies.join(', ')}` : null;
    case 'locations': return f.locations?.length ? `Location: ${f.locations.join(', ')}` : null;
    case 'status':    return f.status?.length ? `Status: ${f.status.map((s) => STATUS_LABEL[s]).join(', ')}` : null;
    case 'matchMin':
    case 'matchMax': {
      const { matchMin: lo, matchMax: hi } = f;
      if (lo == null && hi == null) return null;
      // One chip covers the pair; only the low end renders it.
      if (key === 'matchMax' && lo != null) return null;
      if (lo != null && hi != null) return `Match ${lo}–${hi}`;
      return lo != null ? `Match ≥ ${lo}` : `Match ≤ ${hi}`;
    }
    default: return null;
  }
}

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

/** Add/remove chip editor for skill keywords (review-before-match). */
function SkillChips({ label, hint, skills, onChange, accent }: {
  label: string; hint: string; skills: string[];
  onChange: (next: string[]) => void; accent: string;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (!skills.some((s) => s.toLowerCase() === v.toLowerCase())) onChange([...skills, v]);
    setDraft('');
  };
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--fg-secondary)', marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-muted)', marginBottom: 8 }}>{hint}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
        {skills.length === 0 && (
          <span style={{ fontSize: 12, color: 'var(--fg-subtle)', fontStyle: 'italic' }}>None yet — add below.</span>
        )}
        {skills.map((s) => (
          <span key={s} style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px 4px 10px',
            borderRadius: 9999, fontSize: 12, fontWeight: 600,
            background: `${accent}14`, color: accent, border: `1px solid ${accent}45`,
          }}>
            {s}
            <button
              onClick={() => onChange(skills.filter((x) => x !== s))}
              title={`Remove "${s}"`}
              style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit', display: 'inline-flex', padding: 0 }}
            >
              <Icon name="x" size={11} />
            </button>
          </span>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
          placeholder="Type a skill + Enter"
          style={{
            flex: 1, height: 32, padding: '0 10px', borderRadius: 6, fontSize: 12.5,
            border: '1px solid var(--border-card)', fontFamily: 'inherit', outline: 'none',
          }}
        />
        <button
          onClick={add}
          disabled={!draft.trim()}
          style={{
            height: 32, padding: '0 12px', borderRadius: 6, fontSize: 12, fontWeight: 700,
            cursor: draft.trim() ? 'pointer' : 'not-allowed', border: '1px solid var(--border-card)',
            background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit',
            opacity: draft.trim() ? 1 : 0.5,
          }}
        >
          <Icon name="plus" size={12} />
        </button>
      </div>
    </div>
  );
}

/** One badge, one band definition. Colors/thresholds come from the shared
 *  matching bands so this table can never disagree with the match-run views.
 *  `provisional` = the score is still the sourcing-time title-overlap heuristic
 *  (no match run yet) — rendered dashed so it doesn't read as an assessment. */
function MatchBadge({ score, provisional }: { score: number; provisional?: boolean }) {
  const band = bandFor(score);
  return (
    <span
      title={provisional
        ? 'Provisional — title match only. Run Match for the real score.'
        : band.label}
      style={{
        background: band.bg, color: band.fg,
        border: `1px ${provisional ? 'dashed' : 'solid'} ${band.line}`,
        padding: '2px 7px', borderRadius: 4, fontSize: 11, fontWeight: 600,
        opacity: provisional ? 0.75 : 1,
      }}
    >
      {Math.round(score)}{provisional ? '*' : ''}
    </span>
  );
}

export function PipelineJobCandidatesPage({ pipelineId, jobId }: Props) {
  const router = useRouter();
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);

  // Bulk actions (enrich / run match) on the selected candidates.
  const [bulkBusy, setBulkBusy] = useState<null | 'enrich' | 'match' | 'delete'>(null);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);
  // The Enrich split-button menu (Apollo / Apify / Both).
  const [enrichMenuOpen, setEnrichMenuOpen] = useState(false);

  // Per-column filters (AND across columns). Server-side: the table is paginated,
  // so filtering only the fetched page would report wrong totals and miss rows.
  const [filters, setFilters] = useState<CandidateFilters>({});
  const [facets, setFacets] = useState<CandidateFacets>(EMPTY_FACETS);
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

  // Discovery: a source picker (Apify vs Apollo) → the matching questionnaire.
  const [discoverOpen, setDiscoverOpen] = useState(false);       // unified discovery form
  const [discoverMsg, setDiscoverMsg] = useState<string | null>(null);
  const autoOpenedRef = useRef(false);

  // "Widen the search?" — shown when discovery found fewer strong candidates
  // than the target. The recruiter picks which adjacent-specialty titles to
  // add; nothing widens without their click.
  const [dismissedShortfall, setDismissedShortfall] = useState(false);
  const [widenPicked, setWidenPicked] = useState<Set<string>>(new Set());
  const [widenBusy, setWidenBusy] = useState(false);

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
        pipelineId, jobId, page, rowsPerPage, filters, sortBy, sortOrder,
      );
      setCandidates(c.candidates);
      setPages(c.pages);
      setTotal(c.total);
    } catch (e: any) {
      setActionError(e.message || 'Failed to load candidates');
    }
  }, [pipelineId, jobId, page, rowsPerPage, filters, sortField, sortOrder]);

  // Facet counts follow the filters (each column's options honour the others').
  const loadFacets = useCallback(async () => {
    try {
      setFacets(await fetchCandidateFacets(pipelineId, jobId, filters));
    } catch (e) {
      console.error(e);  // Options going missing must not break the table.
    }
  }, [pipelineId, jobId, filters]);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadPipeline(), loadCandidates()]).finally(() => setLoading(false));
  }, [loadPipeline, loadCandidates]);

  useEffect(() => { loadFacets(); }, [loadFacets]);

  /** Apply a column's filter: merge, reset to page 1, drop stale selections. */
  const setFilter = useCallback((patch: Partial<CandidateFilters>) => {
    setFilters((prev) => {
      const next: CandidateFilters = { ...prev, ...patch };
      // Strip empties so `activeFilters` and the request stay clean.
      (Object.keys(next) as (keyof CandidateFilters)[]).forEach((k) => {
        const v = next[k];
        if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete next[k];
      });
      return next;
    });
    setPage(1);
    // Selection is keyed by row; rows about to change means it can't survive.
    setSelected(new Set());
  }, []);

  const activeFilters = useMemo(
    () => (Object.keys(filters) as (keyof CandidateFilters)[])
      .map((k) => ({ key: k, label: describeFilter(k, filters) }))
      .filter((x): x is { key: keyof CandidateFilters; label: string } => x.label !== null),
    [filters],
  );

  const clearFilter = useCallback((key: keyof CandidateFilters) => {
    // Match is one chip over two keys — clearing it must drop both bounds.
    if (key === 'matchMin' || key === 'matchMax') {
      setFilter({ matchMin: undefined, matchMax: undefined });
    } else {
      setFilter({ [key]: undefined } as Partial<CandidateFilters>);
    }
  }, [setFilter]);

  // Open the questionnaire on arrival when either:
  //   • the job is freshly added and still awaiting its first search, or
  //   • we arrived from the pipeline's "New search" button (?search=1).
  // Once only — reopening on every poll would fight the user closing it.
  useEffect(() => {
    if (loading || autoOpenedRef.current) return;
    // Read the flag off the URL directly rather than via useSearchParams: that
    // hook forces the whole page under a Suspense boundary (Next's CSR bailout),
    // which renders this route blank. We only need a one-shot read on mount.
    const requested = new URLSearchParams(window.location.search).get('search') === '1';
    const awaitingFirstSearch = jobEntry?.searchStatus === 'awaiting_input' && total === 0;
    if (requested || awaitingFirstSearch) {
      autoOpenedRef.current = true;
      setDiscoverOpen(true);
      // ?search=1 is a one-shot instruction, not state — drop it so a refresh or
      // a Back into this page doesn't reopen the form over the results.
      if (requested) router.replace(`/candidates/${pipelineId}/jobs/${jobId}`, { scroll: false });
    }
  }, [loading, jobEntry?.searchStatus, total, router, pipelineId, jobId]);

  // Poll the job through search → auto-enrich after the questionnaire is run.
  const pollDiscover = useCallback(async () => {
    // A per-engine progress line for the combined run ("LinkedIn: 12 · Apollo: searching…").
    const engineLine = (je?: PipelineJob | null): string => {
      const part = (label: string, st?: string | null, kept?: number | null) => {
        if (!st || st === 'skipped') return null;
        if (st === 'running') return `${label}: searching…`;
        if (st === 'failed') return `${label}: failed`;
        return `${label}: ${kept ?? 0}`;
      };
      const bits = [
        part('LinkedIn', je?.apifySearchStatus, je?.apifyKept),
        part('Apollo', je?.apolloSearchStatus, je?.apolloKept),
      ].filter(Boolean);
      return bits.length ? bits.join(' · ') : 'Searching for candidates…';
    };
    let sawRunning = false;
    for (let i = 0; i < 200; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      let p: Pipeline | null = null;
      try { p = await fetchPipeline(pipelineId); } catch { continue; }
      setPipeline(p);
      const je = p.jobs.find((j) => j.jobId === jobId);
      const ss = je?.searchStatus;
      const es = je?.enrichStatus;
      const found = je?.candidateCount ?? 0;
      if (ss === 'running') { sawRunning = true; setDiscoverMsg(engineLine(je)); continue; }
      if (ss === 'failed') { setDiscoverMsg(`Search failed: ${je?.searchError || 'unknown error'}`); return; }
      // Zero strong candidates ends as awaiting_input (the recruiter decides
      // the next move) — terminal once we know THIS search ran, not the state
      // left over from before it claimed the job.
      if (ss === 'awaiting_input' && (sawRunning || je?.searchShortfall)) {
        setDismissedShortfall(false);
        setDiscoverMsg(null);
        await loadCandidates();
        return;
      }
      if (ss === 'completed') {
        await loadCandidates();
        // Deep enrichment is human-controlled: search completing is the END
        // of discovery. The recruiter reviews the profiles and presses
        // Enrich to pull full work history. (es === 'queued'/'running'
        // only happens if a legacy auto-enrich run is still finishing.)
        if (es === 'queued' || es === 'running') { setDiscoverMsg(`Found ${found} candidate(s) · enriching profiles…`); continue; }
        setDismissedShortfall(false);
        if (es === 'completed') { setDiscoverMsg(`Found ${found} candidate(s) — profiles enriched ✓`); await loadCandidates(); return; }
        setDiscoverMsg(
          found > 0
            ? `Found ${found} candidate(s), strongest first. Tick up to ${ENRICH_MAX}, then press Enrich for full work history.`
            : 'No candidates matched. Adjust the filters and search again.',
        );
        return;
      }
    }
    setDiscoverMsg('This is taking longer than expected — refresh to check.');
  }, [pipelineId, jobId, loadCandidates]);

  const onDiscoverSubmitted = () => {
    setDiscoverOpen(false);
    setDismissedShortfall(false);
    setDiscoverMsg('Starting search…');
    pollDiscover();
  };

  // Shortfall chips default to ALL suggested titles ticked — one click to run,
  // still fully editable.
  const shortfall = (jobEntry?.searchStatus === 'completed' || jobEntry?.searchStatus === 'awaiting_input')
    ? jobEntry?.searchShortfall : null;
  useEffect(() => {
    setWidenPicked(new Set(shortfall?.adjacentTitles ?? []));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shortfall?.at]);

  /** Re-run discovery with the SAME filters plus the recruiter-picked adjacent
   *  titles. This is the only path that ever widens the specialty — and it is
   *  a recruiter click, by design. */
  const onWidenSearch = async () => {
    const base = (jobEntry?.lastDiscoverFilters ?? {}) as DiscoverFilters;
    const existing = (base.currentJobTitles ?? []).map(String);
    const added = Array.from(widenPicked).filter((t) => !existing.includes(t));
    if (added.length === 0) return;
    setWidenBusy(true);
    setActionError(null);
    try {
      await discoverJobCandidates(pipelineId, jobId, {
        ...base,
        currentJobTitles: [...existing, ...added],
        autoBroaden: true,
        // The remaining suggestions stay on offer for a further widening.
        adjacentTitles: (jobEntry?.adjacentTitles ?? []).filter((t) => !widenPicked.has(t)),
      });
      setDismissedShortfall(true);
      setDiscoverMsg(`Searching again with ${added.length} added title(s)…`);
      pollDiscover();
    } catch (e: any) {
      setActionError(e.message || 'Failed to widen the search');
    } finally {
      setWidenBusy(false);
    }
  };

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

  // Single-candidate enrich (slide-out). Goes through the job-level enrich
  // endpoint, which routes by candidate SOURCE — the old per-candidate endpoint
  // was Apollo-only and returned 502 for every Apify-discovered candidate.
  const onEnrich = async (id: string) => {
    setBusyId(id);
    setActionError(null);
    try {
      await bulkEnrichJobCandidates(pipelineId, jobId, [id]);
      pollApify(id); // background job — poll this one row until it settles
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
        const apify = c.apify_enriched ?? 0;
        const apollo = c.apollo_enriched ?? 0;
        const apolloFailed = c.apollo_failed ?? 0;
        const nf = c.not_found ?? 0;
        const parts: string[] = [];
        if (apollo) parts.push(`${apollo} contact(s) revealed`);
        if (apify) parts.push(`${apify} profile(s) enriched`);
        if (apolloFailed) parts.push(`${apolloFailed} Apollo lookup(s) failed`);
        if (nf) parts.push(`${nf} no profile found`);
        setBulkMsg(`Enriched ✓ — ${parts.join(' · ') || 'nothing left to enrich'}`);
        await loadCandidates();
        return;
      }
      if (st === 'failed') { setBulkMsg(`Enrichment failed: ${je?.enrichError || 'unknown error'}`); return; }
    }
    setBulkMsg('Enrichment is taking longer than expected — refresh to check.');
  }, [pipelineId, jobId, loadCandidates]);

  const onBulkEnrich = async (mode: EnrichMode = 'both') => {
    if (selected.size === 0 || selected.size > ENRICH_MAX) return;
    setEnrichMenuOpen(false);
    setBulkBusy('enrich');
    setActionError(null);
    const engine = mode === 'apollo' ? 'Apollo' : mode === 'apify' ? 'Apify' : 'Apollo + Apify';
    setBulkMsg(`Queuing ${engine} enrichment for ${selected.size} candidate(s)…`);
    try {
      await bulkEnrichJobCandidates(pipelineId, jobId, Array.from(selected), mode);
      await pollEnrich();
    } catch (e: any) {
      setActionError(e.message || 'Bulk enrichment failed');
      setBulkMsg(null);
    } finally {
      setBulkBusy(null);
    }
  };

  const onBulkDelete = async () => {
    const n = selected.size;
    if (n === 0) return;
    if (!window.confirm(`Delete ${n} selected candidate(s) from this job? This can't be undone.`)) return;
    setBulkBusy('delete');
    setActionError(null);
    setBulkMsg(`Deleting ${n} candidate(s)…`);
    try {
      const { deleted } = await deleteJobCandidates(pipelineId, jobId, Array.from(selected));
      setSelected(new Set());
      await loadCandidates();
      setBulkMsg(`Deleted ${deleted} candidate(s).`);
    } catch (e: any) {
      setActionError(e.message || 'Delete failed');
      setBulkMsg(null);
    } finally {
      setBulkBusy(null);
    }
  };

  // ── Review-before-match ───────────────────────────────────────────────────
  // Run Match no longer fires blind: it first shows the recruiter what was
  // extracted from the JD (must-have / nice-to-have skills) and lets them add
  // or remove keywords. The match then scores against THEIR list, and the edit
  // is persisted onto the job's role spec for future runs.
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reqLoading, setReqLoading] = useState(false);
  const [reqError, setReqError] = useState<string | null>(null);
  const [reqEdited, setReqEdited] = useState(false);
  const [mustHave, setMustHave] = useState<string[]>([]);
  const [niceToHave, setNiceToHave] = useState<string[]>([]);

  const onRunMatch = async () => {
    if (selected.size === 0) return;
    setReviewOpen(true);
    setReqLoading(true);
    setReqError(null);
    setReqEdited(false);
    try {
      const data = await fetchJobRequirements(pipelineId, jobId);
      setMustHave(data.requirements.mustHaveSkills);
      setNiceToHave(data.requirements.niceToHaveSkills);
    } catch (e: any) {
      // The review step must not dead-end the flow: surface the parse problem,
      // start from empty lists, and let the recruiter type the keywords.
      setMustHave([]);
      setNiceToHave([]);
      setReqError(e.message || 'Could not parse the job description — add the key skills yourself.');
    } finally {
      setReqLoading(false);
    }
  };

  const startMatch = async () => {
    setReviewOpen(false);
    setBulkBusy('match');
    setActionError(null);
    setBulkMsg(`Starting match for ${selected.size} candidate(s)…`);
    try {
      const { matchRunId } = await runJobMatch(
        pipelineId, jobId, Array.from(selected), undefined,
        // Only send an override when the recruiter actually changed something —
        // an untouched list keeps the parsed requirements (and their provenance).
        reqEdited ? { mustHaveSkills: mustHave, niceToHaveSkills: niceToHave } : undefined,
      );
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
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setDiscoverOpen(true)}
          title="Search LinkedIn + Apollo for candidates"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 7, height: 34, padding: '0 14px',
            borderRadius: 8, fontSize: 13, fontWeight: 700, fontFamily: 'inherit', cursor: 'pointer',
            border: 'none', background: 'var(--primary)', color: '#FFF',
          }}
        >
          <Icon name="search" size={14} />
          {total > 0 ? 'New search' : 'Discover candidates'}
        </button>
      </div>

      {discoverMsg && (
        <div style={{
          padding: '8px 24px', fontSize: 12.5, color: 'var(--fg-secondary)',
          background: '#F5F3FF', borderBottom: '1px solid var(--border-default)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <Icon name="search" size={13} style={{ color: '#4F46E5' }} />
          {discoverMsg}
        </div>
      )}

      {/* "Widen the search?" — discovery found fewer strong candidates than the
          target. The tool NEVER widens the specialty itself: these chips are the
          Strategist's adjacent-specialty titles, and only the recruiter's click
          adds them to the search. */}
      {shortfall && !dismissedShortfall && (
        <div style={{
          padding: '14px 24px', background: '#FFFBEB',
          borderBottom: '1px solid #FDE68A',
          display: 'flex', flexDirection: 'column', gap: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
            <Icon name="alert-triangle" size={15} style={{ color: '#D97706', flexShrink: 0, marginTop: 1 }} />
            <div style={{ fontSize: 13, lineHeight: 1.55, color: '#92400E' }}>
              <b>{shortfall.reason}</b>{' '}
              The search stayed strictly inside this specialty across {shortfall.attempts} attempt(s) —
              it will not swap in a different profession on its own.
              {shortfall.adjacentTitles.length > 0
                ? ' To widen, pick which neighbouring specialties to include and search again:'
                : ' To widen, run a new search and adjust the titles yourself.'}
            </div>
          </div>
          {shortfall.adjacentTitles.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, alignItems: 'center', paddingLeft: 24 }}>
              {shortfall.adjacentTitles.map((t) => {
                const on = widenPicked.has(t);
                return (
                  <button
                    key={t}
                    onClick={() => setWidenPicked((prev) => {
                      const next = new Set(prev);
                      if (next.has(t)) next.delete(t); else next.add(t);
                      return next;
                    })}
                    style={{
                      padding: '5px 12px', borderRadius: 999, fontSize: 12.5, fontWeight: 600,
                      cursor: 'pointer', fontFamily: 'inherit',
                      border: on ? '1px solid #D97706' : '1px solid #FDE68A',
                      background: on ? '#FEF3C7' : '#FFF', color: '#92400E',
                    }}
                  >
                    {on ? '✓ ' : ''}{t}
                  </button>
                );
              })}
            </div>
          )}
          <div style={{ display: 'flex', gap: 10, paddingLeft: 24 }}>
            {shortfall.adjacentTitles.length > 0 && (
              <button
                onClick={onWidenSearch}
                disabled={widenBusy || widenPicked.size === 0}
                style={{
                  height: 32, padding: '0 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 700,
                  cursor: widenBusy || widenPicked.size === 0 ? 'not-allowed' : 'pointer',
                  border: 'none', background: '#D97706', color: '#FFF', fontFamily: 'inherit',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  opacity: widenBusy || widenPicked.size === 0 ? 0.6 : 1,
                }}
              >
                <Icon name={widenBusy ? 'loader' : 'search'} size={13} />
                Search wider ({widenPicked.size} added title{widenPicked.size === 1 ? '' : 's'})
              </button>
            )}
            <button
              onClick={() => setDiscoverOpen(true)}
              style={{
                height: 32, padding: '0 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                cursor: 'pointer', border: '1px solid #FDE68A', background: '#FFF',
                color: '#92400E', fontFamily: 'inherit',
              }}
            >
              Edit the search
            </button>
            <button
              onClick={() => setDismissedShortfall(true)}
              style={{
                height: 32, padding: '0 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                cursor: 'pointer', border: 'none', background: 'transparent',
                color: '#92400E', fontFamily: 'inherit', textDecoration: 'underline',
              }}
            >
              Keep as is
            </button>
          </div>
        </div>
      )}

      {/* Search transparency — every attempt the discovery ran, and what the
          pre-screen gate kept/dropped. A thin list should be explainable, not
          mysterious: this is the proof the tool searched correctly. */}
      {(jobEntry?.searchAttempts?.length ?? 0) > 0 && (
        <details style={{
          padding: '8px 24px', borderBottom: '1px solid var(--border-default)',
          background: 'var(--bg-app)', fontSize: 12.5,
        }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600, color: 'var(--fg-secondary)' }}>
            How this search ran — {jobEntry!.searchAttempts!.length} attempt(s)
            {jobEntry?.prescreen
              ? ` · ${jobEntry.prescreen.total} raw hit(s), ${jobEntry.prescreen.kept} kept, ${jobEntry.prescreen.dropped} screened out`
              : ''}
          </summary>
          <div style={{ padding: '10px 0 6px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {jobEntry!.searchAttempts!.map((a) => (
              <div key={a.attempt} style={{ display: 'flex', gap: 8, lineHeight: 1.5 }}>
                <span style={{
                  flexShrink: 0, width: 20, height: 20, borderRadius: 999, fontSize: 11, fontWeight: 700,
                  background: a.resultCount > 0 ? '#DCFCE7' : a.error ? '#FEE2E2' : '#F3F4F6',
                  color: a.resultCount > 0 ? '#166534' : a.error ? '#991B1B' : 'var(--fg-muted)',
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {a.attempt}
                </span>
                <div style={{ color: 'var(--fg-secondary)' }}>
                  <b>{a.action === 'initial' ? 'Your search' : a.action.replace(/_/g, ' ')}</b>
                  {' — '}
                  {a.resultCount} result(s)
                  {a.channelCounts && Object.keys(a.channelCounts).length > 1 && (
                    <span style={{ color: 'var(--fg-muted)' }}>
                      {' '}({Object.entries(a.channelCounts).map(([k, v]) => `${v} via ${k}`).join(', ')})
                    </span>
                  )}
                  {a.error && <span style={{ color: '#B91C1C' }}> · {a.error}</span>}
                  {a.reasoning && <div style={{ color: 'var(--fg-muted)' }}>{a.reasoning}</div>}
                  {(a.filters as any)?.currentJobTitles?.length > 0 && (
                    <div style={{ color: 'var(--fg-muted)' }}>
                      Titles: {((a.filters as any).currentJobTitles as string[]).join(' · ')}
                      {(a.filters as any)?.locations?.length > 0 && <> · in {((a.filters as any).locations as string[]).join(', ')}</>}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {(jobEntry?.prescreen?.droppedSamples?.length ?? 0) > 0 && (
              <div style={{ color: 'var(--fg-muted)', lineHeight: 1.5 }}>
                <b>Screened out (title unrelated to the role):</b>{' '}
                {jobEntry!.prescreen!.droppedSamples!.slice(0, 6).map((d) => `${d.name || '—'} (“${d.title || '—'}”)`).join(', ')}
                {jobEntry!.prescreen!.dropped > 6 ? ` +${jobEntry!.prescreen!.dropped - 6} more` : ''}
              </div>
            )}
          </div>
        </details>
      )}

      {/* Filter strip */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)', flexWrap: 'wrap',
      }}>
        {/* Active column filters. The old All/Accepted/Rejected tabs are gone —
            Status is now a column filter like every other. */}
        {activeFilters.length === 0 ? (
          <span style={{ fontSize: 12.5, color: 'var(--fg-muted)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Icon name="filter" size={12} />
            Filter any column from its header
          </span>
        ) : (
          <>
            {activeFilters.map(({ key, label }) => (
              <span
                key={key}
                title={label}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: 320,
                  padding: '4px 8px 4px 10px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                  background: 'var(--accent-soft, #EEF0FE)', color: 'var(--primary)',
                  border: '1px solid var(--primary)',
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
                <button
                  onClick={() => clearFilter(key)}
                  title="Remove this filter"
                  style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--primary)', display: 'inline-flex', padding: 0, flexShrink: 0 }}
                >
                  <Icon name="x" size={12} />
                </button>
              </span>
            ))}
            <button
              onClick={() => { setFilters({}); setPage(1); setSelected(new Set()); }}
              style={{ ...chipStyle(false), padding: '4px 10px', fontSize: 12.5 }}
            >
              Clear all
            </button>
          </>
        )}
        <div style={{ flex: 1 }} />
        {/* Selection-driven actions. Enrichment is HUMAN-CONTROLLED and follows
            the selection: tick the candidates (or the header checkbox for all),
            then Enrich. Both buttons stay visible whenever candidates exist so
            the flow is discoverable; they enable once something is selected. */}
        {total > 0 && (
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, marginRight: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--fg-muted)', fontWeight: 600 }}>
              {selected.size > 0 ? `${selected.size} selected` : 'Select candidates to act'}
            </span>
            {(() => {
              const enrichDisabled = bulkBusy !== null || selected.size === 0 || selected.size > ENRICH_MAX;
              const overCap = selected.size > ENRICH_MAX;
              const opts: { mode: EnrichMode; label: string; desc: string }[] = [
                { mode: 'apollo', label: 'Apollo enrich', desc: 'Verified email + contact. No profile scrape, no Apify.' },
                { mode: 'apify', label: 'Apify enrich', desc: 'Full LinkedIn work history & skills. No Apollo credit.' },
                { mode: 'both', label: 'Both', desc: 'Apollo contact, then Apify deep profile.' },
              ];
              return (
                <div style={{ position: 'relative', display: 'inline-flex' }}>
                  <button
                    onClick={() => { if (!enrichDisabled) setEnrichMenuOpen((o) => !o); }}
                    disabled={enrichDisabled}
                    title={selected.size === 0
                      ? 'Tick candidates first (header checkbox selects the whole page), then enrich them together.'
                      : overCap
                      ? `Enrichment is capped at ${ENRICH_MAX} per batch — pick your ${ENRICH_MAX} strongest candidates. You can enrich more in a second batch.`
                      : 'Choose an engine: Apollo (contact), Apify (deep profile), or both. Background, skips already-enriched. This is the paid step.'}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
                      borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: 'inherit',
                      cursor: enrichDisabled ? 'not-allowed' : 'pointer',
                      border: `1px solid ${overCap ? '#DC2626' : 'var(--primary)'}`,
                      background: '#FFF', color: overCap ? '#DC2626' : 'var(--primary)',
                      opacity: bulkBusy || selected.size === 0 ? 0.55 : 1,
                    }}
                  >
                    <Icon name={bulkBusy === 'enrich' ? 'loader' : 'sparkles'} size={13} />
                    Enrich{selected.size > 0 ? ` (${selected.size}/${ENRICH_MAX})` : ''}
                    <Icon name="chevron-down" size={13} />
                  </button>
                  {enrichMenuOpen && !enrichDisabled && (
                    <>
                      {/* click-catcher to close on outside click */}
                      <div onClick={() => setEnrichMenuOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
                      <div style={{
                        position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 41,
                        width: 268, background: '#FFF', border: '1px solid var(--border-card)',
                        borderRadius: 10, boxShadow: '0 12px 32px rgba(0,0,0,0.16)', overflow: 'hidden',
                      }}>
                        {opts.map((o, i) => (
                          <button
                            key={o.mode}
                            onClick={() => onBulkEnrich(o.mode)}
                            style={{
                              display: 'block', width: '100%', textAlign: 'left', padding: '10px 13px',
                              border: 'none', borderTop: i === 0 ? 'none' : '1px solid var(--border-default)',
                              background: '#FFF', cursor: 'pointer', fontFamily: 'inherit',
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = '#F5F3FF'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = '#FFF'; }}
                          >
                            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--fg-primary)' }}>{o.label}</div>
                            <div style={{ fontSize: 11.5, lineHeight: 1.45, color: 'var(--fg-muted)', marginTop: 2 }}>{o.desc}</div>
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              );
            })()}
            <button
              onClick={onRunMatch}
              disabled={bulkBusy !== null || selected.size === 0}
              title={selected.size === 0
                ? 'Tick candidates first, then run the match.'
                : 'Review the must-have skills, then score this job against the selected candidates.'}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
                borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: 'inherit',
                cursor: bulkBusy || selected.size === 0 ? 'not-allowed' : 'pointer', border: 'none',
                background: 'var(--primary)', color: '#FFF',
                opacity: bulkBusy || selected.size === 0 ? 0.55 : 1,
              }}
            >
              <Icon name={bulkBusy === 'match' ? 'loader' : 'target'} size={13} />
              Run Match{selected.size > 0 ? ` (${selected.size})` : ''}
            </button>
            <button
              onClick={onBulkDelete}
              disabled={bulkBusy !== null || selected.size === 0}
              title={selected.size === 0
                ? 'Tick candidates first, then delete them from this job.'
                : 'Remove the selected candidates from this job. This cannot be undone.'}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
                borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: 'inherit',
                cursor: bulkBusy || selected.size === 0 ? 'not-allowed' : 'pointer',
                border: '1px solid #DC2626', background: '#FFF', color: '#DC2626',
                opacity: bulkBusy || selected.size === 0 ? 0.55 : 1,
              }}
            >
              <Icon name={bulkBusy === 'delete' ? 'loader' : 'trash-2'} size={13} />
              Delete{selected.size > 0 ? ` (${selected.size})` : ''}
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
                      : jobEntry?.searchShortfall
                      ? 'No candidates matched this exact specialty'
                      : 'No candidates found'}
                  </div>
                  <div style={{ fontSize: 13 }}>
                    {jobEntry?.searchShortfall && jobEntry?.searchStatus !== 'running'
                      ? 'The search stayed strictly on-specialty. Use “Search wider” above, or edit the search.'
                      : 'No candidates match the current filter.'}
                  </div>
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
                <th style={{ ...thStyle, width: 240 }}>
                  <CandidateColumnFilter
                    label="Candidate" kind="text" active={!!filters.name}
                    text={filters.name}
                    onText={(v) => setFilter({ name: v || undefined })}
                    onClear={() => clearFilter('name')}
                  />
                </th>
                <th style={{ ...thStyle, width: 180 }}>
                  <CandidateColumnFilter
                    label="Current Company" kind="options" active={!!filters.companies?.length}
                    options={facets.companies} selected={filters.companies}
                    onOptions={(v) => setFilter({ companies: v })}
                    onClear={() => clearFilter('companies')}
                  />
                </th>
                <th style={{ ...thStyle, width: 200 }}>
                  <CandidateColumnFilter
                    label="Current Role" kind="text" active={!!filters.role}
                    text={filters.role}
                    onText={(v) => setFilter({ role: v || undefined })}
                    onClear={() => clearFilter('role')}
                  />
                </th>
                <th
                  style={{ ...thStyle, width: 100, cursor: 'pointer' }}
                  onClick={() => handleSort('added')}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    Added
                    <SortIcon active={sortField === 'added'} order={sortOrder} />
                  </span>
                </th>
                <th style={{ ...thStyle, width: 150 }}>
                  <CandidateColumnFilter
                    label="Location" kind="options" active={!!filters.locations?.length}
                    options={facets.locations} selected={filters.locations}
                    onOptions={(v) => setFilter({ locations: v })}
                    onClear={() => clearFilter('locations')}
                  />
                </th>
                <th style={{ ...thStyle, width: 110 }}>
                  {/* Sorting stays on the label; the funnel stops the click so
                      opening the filter doesn't also flip the sort order. */}
                  <span
                    style={{ display: 'inline-flex', alignItems: 'center', cursor: 'pointer' }}
                    onClick={() => handleSort('match')}
                  >
                    <CandidateColumnFilter
                      label="Match" kind="range"
                      active={filters.matchMin != null || filters.matchMax != null}
                      min={filters.matchMin} max={filters.matchMax}
                      onRange={(lo, hi) => setFilter({ matchMin: lo, matchMax: hi })}
                      onClear={() => clearFilter('matchMin')}
                    />
                    <SortIcon active={sortField === 'match'} order={sortOrder} />
                  </span>
                </th>
                <th style={{ ...thStyle, width: 110 }}>
                  <CandidateColumnFilter
                    label="Status" kind="options" active={!!filters.status?.length}
                    align="right"
                    options={facets.status} selected={filters.status}
                    optionLabel={(v) => STATUS_LABEL[v] || v}
                    onOptions={(v) => setFilter({ status: v as ('accepted' | 'rejected')[] })}
                    onClear={() => clearFilter('status')}
                  />
                </th>
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
                      {/* WHY this person is in the list — the pre-screen evidence. */}
                      {c.prescreen?.matchedVia && (
                        <span
                          title={(c.prescreen.reasons || []).join(' ')}
                          style={{
                            display: 'block', overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap', maxWidth: 180, fontSize: 11, color: 'var(--fg-muted)',
                          }}
                        >
                          ≈ {c.prescreen.matchedVia}
                        </span>
                      )}
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
                      <MatchBadge score={c.matchScore} provisional={c.matchScoreSource !== 'match_run'} />
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
                        {c.openToWork && (
                          <span title="LinkedIn: open to work — likely to respond" style={{
                            display: 'inline-flex', alignItems: 'center', gap: 3,
                            padding: '2px 7px', borderRadius: 9999, fontSize: 10, fontWeight: 700,
                            background: '#ECFDF5', color: '#047857', border: '1px solid #A7F3D0',
                          }}>
                            <Icon name="hand" size={10} />Open to work
                          </span>
                        )}
                        {(c.sourceChannels?.length ?? 0) > 1 && (
                          <span title="Found independently by BOTH the title search and the keyword search — the strongest pre-enrichment signal" style={{
                            display: 'inline-flex', alignItems: 'center', gap: 3,
                            padding: '2px 7px', borderRadius: 9999, fontSize: 10, fontWeight: 700,
                            background: '#F0F9FF', color: '#0369A1', border: '1px solid #BAE6FD',
                          }}>
                            <Icon name="target" size={10} />2× found
                          </span>
                        )}
                        {c.sourceChannels?.length === 1 && c.sourceChannels[0] === 'keyword' && (
                          <span title="Their profile content matches the search keywords even though the job title alone doesn't show it — worth a look" style={{
                            display: 'inline-flex', alignItems: 'center', gap: 3,
                            padding: '2px 7px', borderRadius: 9999, fontSize: 10, fontWeight: 700,
                            background: '#FAF5FF', color: '#7C3AED', border: '1px solid #E9D5FF',
                          }}>
                            <Icon name="search" size={10} />Keyword find
                          </span>
                        )}
                      </span>
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

      {/* Review-before-match: what the run will score against, editable. */}
      {reviewOpen && (
        <div
          onClick={(e) => { if (e.target === e.currentTarget) setReviewOpen(false); }}
          style={{
            position: 'fixed', inset: 0, zIndex: 80, background: 'rgba(0,0,0,0.4)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
          }}
        >
          <div style={{
            width: '100%', maxWidth: 560, background: '#FFF', borderRadius: 12,
            boxShadow: '0 20px 60px rgba(0,0,0,0.25)', maxHeight: '84vh',
            display: 'flex', flexDirection: 'column',
          }}>
            <div style={{ padding: '18px 22px 14px', borderBottom: '1px solid var(--border-card)' }}>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)' }}>
                Check the matching criteria
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--fg-muted)', marginTop: 3, lineHeight: 1.5 }}>
                These keywords were read from the job description — the match scores every
                candidate against them. Is this enough? Add what&apos;s missing, remove what&apos;s wrong,
                then run.
              </div>
            </div>
            <div style={{ padding: '16px 22px', overflow: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 18 }}>
              {reqLoading ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--fg-muted)', padding: '12px 0' }}>
                  <Icon name="loader" size={14} /> Reading the job description…
                </div>
              ) : (
                <>
                  {reqError && (
                    <div style={{
                      padding: '9px 12px', borderRadius: 8, background: '#FFFBEB',
                      border: '1px solid #FDE68A', fontSize: 12.5, color: '#92400E', lineHeight: 1.5,
                    }}>
                      {reqError}
                    </div>
                  )}
                  <SkillChips
                    label="Must-have skills"
                    hint="Hard requirements. Candidates missing these are capped, whatever else their profile says."
                    skills={mustHave}
                    onChange={(next) => { setMustHave(next); setReqEdited(true); }}
                    accent="#B91C1C"
                  />
                  <SkillChips
                    label="Nice-to-have skills"
                    hint="A plus, not a requirement — shown to the reviewer, never a hard filter."
                    skills={niceToHave}
                    onChange={(next) => { setNiceToHave(next); setReqEdited(true); }}
                    accent="#4F46E5"
                  />
                </>
              )}
            </div>
            <div style={{
              padding: '14px 22px', borderTop: '1px solid var(--border-card)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
            }}>
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                {reqEdited ? 'Your edits will be saved for this job.' : 'Looks right? Run it as-is.'}
              </span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => setReviewOpen(false)}
                  style={{
                    height: 34, padding: '0 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                    cursor: 'pointer', border: '1px solid var(--border-card)', background: '#FFF',
                    color: 'var(--fg-primary)', fontFamily: 'inherit',
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={startMatch}
                  disabled={reqLoading}
                  title={mustHave.length === 0 ? 'No must-have skills — the score will rest on overall profile fit only.' : undefined}
                  style={{
                    height: 34, padding: '0 16px', borderRadius: 6, fontSize: 12.5, fontWeight: 700,
                    cursor: reqLoading ? 'not-allowed' : 'pointer', border: 'none',
                    background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    opacity: reqLoading ? 0.6 : 1,
                  }}
                >
                  <Icon name="target" size={13} />
                  Run Match ({selected.size})
                </button>
              </div>
            </div>
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

      {discoverOpen && (
        <CandidateDiscoveryForm
          pipelineId={pipelineId}
          jobId={jobId}
          jobTitle={jobEntry?.jobTitle || ''}
          jobLocation={jobEntry?.jobLocation}
          companyName={pipeline?.companyName || ''}
          onClose={() => setDiscoverOpen(false)}
          onSubmitted={onDiscoverSubmitted}
        />
      )}

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
