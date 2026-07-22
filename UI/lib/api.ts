// ─────────────────────────────────────────────────────────────────────────────
// Recruitr — Real API client
//
// Calls go to our OWN origin, not the backend directly: /api/proxy/* is a Next
// route handler that reads the access token from the httpOnly session cookie and
// forwards to the API with an Authorization header (see app/api/proxy).
//
// That's why there's no token handling anywhere in this file, and why the
// browser never learns the backend's URL. The old NEXT_PUBLIC_API_URL is gone —
// NEXT_PUBLIC_* is inlined into the browser bundle, and under the BFF model only
// the server talks to the API (API_BASE_URL, server-side).
//
// Paths are unchanged: the proxy forwards everything after /api/proxy verbatim,
// so `/api/v1/runs` still means `/api/v1/runs` upstream.
// ─────────────────────────────────────────────────────────────────────────────

const API_BASE = '/api/proxy';

// ── Session handling ─────────────────────────────────────────────────────────
// A 401 from the proxy means the session is dead (expired, silent refresh
// failed, or revoked) — not a transient API error. Left as a thrown Error, this
// surfaced as a raw "GET /api/v1/... → 401" message on an otherwise-normal-
// looking page, which reads as the product being broken rather than a session
// that needs renewing. Route it through the same login flow the page-level
// middleware already uses for an unauthenticated page load, `returnTo` and all,
// so the user lands back where they were once they sign in again.
function redirectToLogin(): void {
  if (typeof window === 'undefined') return;
  const { pathname, search } = window.location;
  if (pathname.startsWith('/auth/') || pathname === '/login') return; // no loop
  window.location.href = `/auth/login?returnTo=${encodeURIComponent(pathname + search)}`;
}

/** Shared response handling for every helper below. On 401 this redirects and
 *  never resolves — the browser is already navigating away, so nothing should
 *  render an error for the instant before that completes. */
async function handleResponse<T>(res: Response, fallback: string): Promise<T> {
  if (res.status === 401) {
    redirectToLogin();
    return new Promise<T>(() => {});
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || fallback);
  }
  return res.json();
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  return handleResponse<T>(res, `GET ${path} → ${res.status}`);
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, `POST ${path} → ${res.status}`);
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
  return handleResponse<T>(res, `DELETE ${path} → ${res.status}`);
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return handleResponse<T>(res, `PATCH ${path} → ${res.status}`);
}

// ── ICP Config ────────────────────────────────────────────────────────────────

export interface ICPTitleConfig {
  title: string;
  isActive: boolean;
  isDefault: boolean;
}

export interface ICPLocationConfig {
  location: string;
  country: string;
  isActive: boolean;
  isDefault: boolean;
}

export interface ICPIndustryConfig {
  slug: string;
  displayName: string;
  isTarget: boolean;
  linkedinNames: string[];
}

export interface ICPBackendConfig {
  version: number;
  isActive: boolean;
  titles: ICPTitleConfig[];
  locations: ICPLocationConfig[];
  industries: ICPIndustryConfig[];
  personaMappings: unknown[];
  defaultPersonaTitles: string[];
}

export function fetchICPConfig(): Promise<ICPBackendConfig> {
  return get('/api/v1/icp/config');
}

export function addIndustry(displayName: string, description?: string): Promise<ICPBackendConfig> {
  return post('/api/v1/icp/industries', { displayName, description });
}

export function addTitle(title: string): Promise<unknown> {
  return post('/api/v1/icp/titles', { title });
}

export function addLocation(location: string, country?: string): Promise<unknown> {
  return post('/api/v1/icp/locations', { location, country });
}

// ── Runs ──────────────────────────────────────────────────────────────────────

export interface RunStats {
  totalJobsScraped: number;
  uniqueCompanies: number;
  acceptedCompanies: number;
  rejectedCompanies: number;
  totalProspects: number;
  inserted?: number;
  duplicates?: number;
  acceptedJobs?: number;
  rejectedJobs?: number;
  skippedCompanies?: number;
}

export interface RunConfig {
  searchTitles: string[];
  searchLocations: string[];
  targetIndustries: string[];
  customIndustries?: string[];
  hoursOld: number;
  resultsPerSearch: number;
  siteName: string[];
  searchUrl?: string;
  icpConfigSnapshot?: { version: number } | null;
}

export interface Run {
  id: string | null;
  _id?: string;
  title: string;
  source: string;
  status: 'active' | 'completed' | 'paused' | 'cancelled';
  runStartedAt: string;
  runEndedAt: string | null;
  stats: RunStats;
  runConfig: RunConfig;
  createdAt: string | null;
  updatedAt: string | null;
}

export function fetchRuns(page = 1, limit = 10): Promise<Run[]> {
  return get(`/api/v1/runs?page=${page}&limit=${limit}`);
}

export function fetchRun(id: string): Promise<Run> {
  return get(`/api/v1/runs/${id}`);
}

export function deleteRun(id: string): Promise<unknown> {
  return del(`/api/v1/runs/${id}`);
}

export function renameRun(id: string, title: string): Promise<Run> {
  return patch(`/api/v1/runs/${id}`, { title });
}

export interface StartRunPayload {
  title: string;
  source: string;
  runConfig: {
    searchTitles: string[];
    searchLocations: string[];
    targetIndustries: string[];
    customIndustries: string[];
    hoursOld: number;
    resultsPerSearch: number;
    siteName: string[];
    searchUrl?: string;
    icpConfigSnapshot?: { version: number } | null;
    scrapeDescriptions?: boolean;
    maxDescriptions?: number;
    minExperience?: number;
    maxExperience?: number;
  };
}

export function startRun(payload: StartRunPayload): Promise<{ id: string; _id?: string }> {
  return post('/api/v1/runs/start', payload);
}

/**
 * Subscribe to real-time pipeline progress via SSE.
 * Returns an EventSource that emits 'phase', 'done', and 'error' events.
 */
export function streamRunProgress(
  runId: string,
  onPhase: (data: { phase: string; stats: Record<string, number> }) => void,
  onDone: (data: { runId: string }) => void,
  onError: (data: { message: string }) => void,
): EventSource {
  const es = new EventSource(`${API_BASE}/api/v1/runs/${runId}/stream`);

  es.addEventListener('phase', (e) => {
    try { onPhase(JSON.parse((e as MessageEvent).data)); } catch {}
  });
  es.addEventListener('done', (e) => {
    try { onDone(JSON.parse((e as MessageEvent).data)); } catch {}
    es.close();
  });
  es.addEventListener('error', (e) => {
    // SSE 'error' can be a reconnect or a real error event from the server
    if ((e as MessageEvent).data) {
      try { onError(JSON.parse((e as MessageEvent).data)); } catch {}
    }
    es.close();
  });

  return es;
}

// ── Run Jobs ──────────────────────────────────────────────────────────────────

export interface RunJob {
  _id: string;
  runId: string;
  title: string;
  company: string;
  location: string;
  boardName: string;
  externalId: string;
  companyId: string | null;
  industry?: string;
  prospectCount?: number;
  outreachCount?: number;
  postedDate?: string;
  qualityStatus: 'excellent' | 'good' | 'fair' | 'poor';
  rejectionReason: string | null;
  jobDetails: {
    jobUrl: string;
    companyUrl: string;
    searchKeyword: string;
    searchLocation: string;
    description: string;
    [key: string]: unknown;
  };
  inPipeline?: boolean;
  inPipelineId?: string;
  inPipelineCompany?: string;
  createdAt: string;
  updatedAt: string;
}

export interface RunJobsResponse {
  total: number;
  page: number;
  limit: number;
  pages: number;
  jobs: RunJob[];
}

export function fetchRunJobs(
  runId: string,
  page = 1,
  limit = 50,
  quality?: string,
  sortBy?: string,
  sortOrder?: string,
): Promise<RunJobsResponse> {
  let url = `/api/v1/runs/${runId}/jobs?page=${page}&limit=${limit}`;
  if (quality) url += `&quality=${quality}`;
  if (sortBy) url += `&sort_by=${sortBy}`;
  if (sortOrder) url += `&sort_order=${sortOrder}`;
  return get(url);
}

// ── Prospects ─────────────────────────────────────────────────────────────────

export interface JobProspect {
  _id: string;
  runId?: string;
  companyId?: string;
  firstName: string;
  lastName: string;
  email?: string;
  title?: string;
  seniority?: string;
  industryName?: string;
  isEnriched: boolean;
  isAccepted: boolean;
  mobileEnrichmentStatus?: 'pending' | 'enriched' | null;
  matchReasons?: string[];
  rejectionReason?: string | null;
  prospectDetails?: {
    linkedinUrl?: string;
    phone?: string;
    location?: string;
  };
}

export function fetchJobProspects(
  jobId: string,
): Promise<{ prospects: JobProspect[]; emailTemplate: unknown }> {
  return get(`/api/v1/jobs/${jobId}/prospects`);
}

/**
 * On-demand Apollo enrichment for a single prospect — unlocks their email
 * (and LinkedIn/phone where available). Consumes one Apollo credit.
 */
export function enrichProspect(
  prospectId: string,
): Promise<{ prospect: JobProspect; emailRevealed: boolean }> {
  return post(`/api/v1/jobs/prospects/${prospectId}/enrich`, {});
}

/**
 * On-demand Apollo phone-number reveal for a single prospect.
 * Returns immediately if phone is cached, or status "pending" when Apollo
 * will deliver the number asynchronously via webhook.
 */
export function enrichProspectPhone(
  prospectId: string,
): Promise<{ prospect_id: string; status: 'enriched' | 'pending'; phone: string | null }> {
  return post(`/api/v1/jobs/prospects/${prospectId}/enrich-mobile`, {});
}

// ── Credits & Outreach ────────────────────────────────────────────────────────

export interface EnrichmentCreditStatus {
  creditsUsed: number;
  dailyLimit: number;
  creditsRemaining: number;
  perJobLimit: number;
  jobCredits: Record<string, number>;
  periodEnd: string;
}

export function fetchEnrichmentCredits(runId: string): Promise<EnrichmentCreditStatus> {
  return get(`/api/v1/runs/${runId}/enrichment-credits`);
}

export function fetchOutreachStatus(runId: string): Promise<{ records: unknown[] }> {
  return get(`/api/v1/runs/${runId}/outreach-status`);
}

export function triggerEmailFlow(
  runId: string,
  jobsPayload: { jobId: string; prospects: unknown[] }[],
): Promise<{ message: string }> {
  return post(`/api/v1/runs/${runId}/trigger-email-flow`, { jobs: jobsPayload });
}

// ── Analytics ─────────────────────────────────────────────────────────────────

export interface JobsAnalytics {
  days: number;
  summary: { total: number; accepted: number; rejected: number; acceptanceRate: number };
  byBoard: { board: string; count: number }[];
  byQuality: { status: string; count: number }[];
  byRejectionReason: { reason: string; count: number }[];
  byKeyword: { keyword: string; count: number }[];
  byLocation: { location: string; count: number }[];
  dailyTrend: { date: string; total: number; accepted: number; rejected: number }[];
}

export interface CompaniesAnalytics {
  summary: { total: number; accepted: number; rejected: number; acceptanceRate: number; avgEmployees: number };
  byEligibility: { status: string; count: number }[];
  byIndustry: { industry: string; count: number }[];
  byRejectionReason: { reason: string; count: number }[];
  bySize: { range: string; count: number }[];
}

export function fetchJobsAnalytics(days = 7): Promise<JobsAnalytics> {
  return get(`/api/v1/analytics/jobs?days=${days}`);
}

export function fetchCompaniesAnalytics(): Promise<CompaniesAnalytics> {
  return get('/api/v1/analytics/companies');
}

// ── Companies (minimal — used by AddToPipelineModal prefill) ────────────────

export interface CompanyDoc {
  _id: string;
  companyName?: string;
  companyDomain?: string;
  companyIndustry?: string;
  industry?: string;
  matchedIndustry?: string | null;
  location?: string;
  linkedinSlug?: string | null;
  website?: string;
  [key: string]: unknown;
}

export function fetchCompany(id: string): Promise<CompanyDoc> {
  return get(`/api/v1/companies/${id}`);
}

// ── Candidate Pipelines ───────────────────────────────────────────────────────

export type PipelineJobSearchStatus = 'awaiting_input' | 'queued' | 'running' | 'completed' | 'failed';

// 'ready' = discovery found candidates and stopped before the (now manual) deep
// enrichment; 'none' = discovery found nothing to enrich.
export type PipelineJobEnrichStatus =
  'ready' | 'none' | 'queued' | 'running' | 'completed' | 'failed';

/** One executed search attempt — the discovery loop's audit trail. */
export interface SearchAttemptEntry {
  attempt: number;
  action: string;
  reasoning?: string;
  filters: Record<string, unknown>;
  resultCount: number;
  /** Raw hits per channel when the attempt ran more than one search page. */
  channelCounts?: Record<string, number> | null;
  at?: string;
  error?: string | null;
}

/** Why the pre-screen gate kept/dropped what it did. */
export interface PrescreenSummary {
  total: number;
  kept: number;
  dropped: number;
  droppedSamples?: { name?: string; title?: string; score?: number; reason?: string }[];
  at?: string;
}

/** Set when discovery found fewer strong candidates than the target — carries
 *  the recruiter-opt-in widening suggestions. */
export interface SearchShortfall {
  found: number;
  target: number;
  adjacentTitles: string[];
  attempts: number;
  reason: string;
  at?: string;
}

export interface PipelineJob {
  jobId: string;
  jobTitle: string;
  jobLocation?: string;
  addedAt: string;
  searchStatus: PipelineJobSearchStatus;
  lastSearchedAt?: string | null;
  candidateCount: number;
  acceptedCount: number;
  rejectedCount: number;
  appliedIndustryFallback?: boolean;
  searchError?: string | null;
  /** Per-engine sub-status when the unified (combined) search ran. `searchStatus`
   *  above is the rollup. 'skipped' = that engine was toggled off. */
  apifySearchStatus?: 'running' | 'completed' | 'no_results' | 'failed' | 'skipped' | null;
  apolloSearchStatus?: 'running' | 'completed' | 'no_results' | 'failed' | 'skipped' | null;
  apifyKept?: number | null;
  apolloKept?: number | null;
  /** Bulk-enrichment (Apollo→Apify) background status + counts. */
  enrichStatus?: PipelineJobEnrichStatus | null;
  enrichError?: string | null;
  enrichCounts?: Record<string, number> | null;
  /** Discovery transparency: every search attempt this job ran. */
  searchAttempts?: SearchAttemptEntry[] | null;
  /** What the pre-screen gate did with the raw search hits. */
  prescreen?: PrescreenSummary | null;
  /** Present when the exact-specialty pool came up short of the target. */
  searchShortfall?: SearchShortfall | null;
  /** The filters the last discovery ran with (rerun/widen replays these). */
  lastDiscoverFilters?: Record<string, unknown> | null;
  /** Adjacent-specialty titles the Strategist proposed — opt-in widen chips. */
  adjacentTitles?: string[] | null;
}

export interface Pipeline {
  _id: string;
  companyId: string | null;
  companyName: string;
  companyDomain: string;
  companyIndustry?: string;
  matchedIndustry?: string | null;
  companyLocation?: string;
  linkedinSlug?: string | null;
  website?: string;
  source: 'run' | 'manual';
  jobs: PipelineJob[];
  totalCandidates: number;
  acceptedCount: number;
  rejectedCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface PipelineListResponse {
  total: number; page: number; limit: number; pages: number;
  pipelines: Pipeline[];
}

export interface CandidateEmploymentEntry {
  title?: string | null;
  organizationName?: string | null;
  organizationId?: string | null;
  startDate?: string | null;
  endDate?: string | null;
  current?: boolean;
}

export interface CandidateOrganizationSlim {
  name?: string | null;
  primaryDomain?: string | null;
  industry?: string | null;
  estimatedNumEmployees?: number | null;
  foundedYear?: number | null;
  hqCity?: string | null;
  hqCountry?: string | null;
  shortDescription?: string | null;
  logoUrl?: string | null;
  linkedinUrl?: string | null;
  websiteUrl?: string | null;
}

// ── Apify deep-profile shape (candidate.apifyEnrichment.profile) ────────────
// Mirrors BE candidate_merge.merge_enriched — snake_case sub-fields intact.
export interface ApifyExperienceEntry {
  title?: string;
  company_name?: string;
  location?: string;
  employment_type?: string;
  summary?: string;
  description?: string;
  skills?: string[];
  starts_at?: string | null;
  ends_at?: string | null;
  is_current?: boolean;
}

export interface ApifyEducationEntry {
  school_name?: string;
  degree_name?: string;
  field_of_study?: string;
  starts_at?: string | null;
  ends_at?: string | null;
}

export interface ApifyCertification { name?: string; authority?: string }
export interface ApifyLanguage { name?: string; proficiency?: string }

export interface ApifyProfile {
  fullName?: string;
  firstName?: string;
  lastName?: string;
  headline?: string;
  summary?: string;
  location?: string;
  currentTitle?: string;
  currentCompany?: string;
  totalYears?: number | null;
  skills?: string[];
  titles?: string[];
  experience?: ApifyExperienceEntry[];
  education?: ApifyEducationEntry[];
  certifications?: ApifyCertification[];
  languages?: ApifyLanguage[];
}

export interface CandidateEnrichedData {
  email?: string | null;
  emailStatus?: string | null;
  personalEmails?: string[];
  linkedinUrl?: string | null;
  photoUrl?: string | null;
  title?: string | null;
  headline?: string | null;
  seniority?: string | null;
  functions?: string[];
  departments?: string[];
  location?: string | null;
  timeZone?: string | null;
  employmentHistory?: CandidateEmploymentEntry[];
  socials?: { twitter?: string | null; github?: string | null; facebook?: string | null };
  organization?: CandidateOrganizationSlim;
}

export interface Candidate {
  _id: string;
  pipelineId: string;
  sourceJobIds: string[];
  apolloId: string;
  externalLinkedinUrl?: string;
  firstName: string;
  lastName: string;
  displayName?: string;
  /** The 800px original as stored by the Apify search. */
  photoUrl?: string | null;
  /** Avatar-sized variant (~200px), derived server-side — the smaller sizes are
   *  signed separately and cannot be derived from `photoUrl` client-side. */
  photoThumbUrl?: string | null;
  headline?: string;
  currentTitle?: string;
  currentCompany?: string;
  currentCompanyDomain?: string;
  location?: string;
  matchScore: number;
  matchReasons: string[];
  /** Provenance of matchScore: "sourcing_heuristic" = provisional title-overlap
   *  number from search time; "match_run" = the real scoring engine's result. */
  matchScoreSource?: 'sourcing_heuristic' | 'match_run';
  lastMatchRunId?: string;
  /** LinkedIn's "open to work" flag (set at deep-enrichment time). */
  openToWork?: boolean;
  /** Which search channel(s) found this person: "title" and/or "keyword". */
  sourceChannels?: string[];
  /** The pre-screen gate's verdict on the free search signal. */
  prescreen?: {
    score?: number | null;
    roleFit?: number | null;
    decision?: 'keep' | 'drop';
    matchedVia?: string | null;
    reasons?: string[];
    channels?: string[];
  } | null;
  isAccepted: boolean;
  rejectionReason?: string | null;
  decidedAt?: string | null;
  isEnriched: boolean;
  enrichedAt?: string | null;
  enrichedData?: CandidateEnrichedData | null;
  /** Full untouched Apollo /people/match envelope — audit-grade. */
  enrichedRaw?: Record<string, unknown> | null;
  enrichedSource?: string | null;
  /** Apify deep-profile enrichment (separate from the Apollo fields above). */
  isApifyEnriched?: boolean;
  apifyEnrichedAt?: string | null;
  /** 'pending' while the background Apify stage runs; terminal otherwise. */
  apifyEnrichmentStatus?: 'pending' | 'enriched' | 'not_found' | 'failed' | null;
  apifyEnrichmentError?: string | null;
  apifyEnrichment?: {
    profile?: ApifyProfile | null;
    contact?: { email?: string | null; phone?: string | null; linkedin?: string | null; emailStatus?: string | null } | null;
    source?: { apollo?: boolean; apify?: boolean } | null;
  } | null;
  runHistory: Array<{
    runAt: string; jobId: string; isRerun: boolean; appliedIndustryFallback: boolean;
  }>;
  createdAt: string;
  updatedAt: string;
}

export interface CandidateListResponse {
  total: number; page: number; limit: number; pages: number;
  candidates: Candidate[];
}

export function fetchPipelines(
  page = 1, limit = 20, q?: string,
): Promise<PipelineListResponse> {
  let url = `/api/v1/pipelines?page=${page}&limit=${limit}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  return get(url);
}

export function fetchPipeline(id: string): Promise<Pipeline> {
  return get(`/api/v1/pipelines/${id}`);
}

export interface CreatePipelinePayload {
  companyId?: string;
  companyName?: string;
  companyDomain?: string;
  companyIndustry?: string;
  matchedIndustry?: string | null;
  companyLocation?: string;
  linkedinSlug?: string | null;
  website?: string;
}

export function createPipeline(payload: CreatePipelinePayload): Promise<Pipeline> {
  return post('/api/v1/pipelines', payload);
}

export function deletePipeline(id: string): Promise<unknown> {
  return del(`/api/v1/pipelines/${id}`);
}

export function addJobToPipeline(pipelineId: string, jobId: string): Promise<unknown> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs`, { jobId });
}

export function removeJobFromPipeline(pipelineId: string, jobId: string): Promise<unknown> {
  return del(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}`);
}

export interface JobSearchResult {
  _id: string;
  title: string;
  location: string;
  boardName?: string;
  createdAt?: string;
}

export interface JobSearchResponse {
  total: number; page: number; limit: number; pages: number;
  jobs: JobSearchResult[];
}

export function searchJobs(q: string, page = 1, limit = 10): Promise<JobSearchResponse> {
  return get(`/api/v1/jobs?q=${encodeURIComponent(q)}&page=${page}&limit=${limit}`);
}

export interface ManualJobPayload {
  title: string;
  location?: string;
  companyId?: string;
  description?: string;
}

export function createManualJob(payload: ManualJobPayload): Promise<JobSearchResult> {
  return post('/api/v1/jobs', payload);
}

/**
 * LEGACY — no callers. Runs the old Apollo people-search with the job title
 * verbatim and no review step, which is what returned poor/zero results.
 *
 * Re-searching a job now goes through the AI discovery flow instead: the
 * pipeline's "New search" button routes to the job's candidates page with
 * `?search=1`, which opens the questionnaire → suggestJobFilters →
 * discoverJobCandidates. Don't wire this back up without that reason.
 */
export function rerunPipelineJob(pipelineId: string, jobId: string): Promise<unknown> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/rerun`, {});
}

/**
 * Per-column filters for the candidates table. Combined with AND across columns
 * and OR within one column (two companies means "either"). Empty/omitted = no
 * filter on that column.
 */
export interface CandidateFilters {
  /** Candidate name contains (case-insensitive). */
  name?: string;
  /** Current role contains (case-insensitive). */
  role?: string;
  companies?: string[];
  locations?: string[];
  status?: ('accepted' | 'rejected')[];
  matchMin?: number;
  matchMax?: number;
}

/** Distinct values + counts per filterable column, for the header dropdowns. */
export interface CandidateFacets {
  companies: { value: string; count: number }[];
  locations: { value: string; count: number }[];
  status: { value: 'accepted' | 'rejected'; count: number }[];
}

/** Serialize filters to query params; repeated keys for multi-value columns. */
function filterParams(f: CandidateFilters = {}): URLSearchParams {
  const p = new URLSearchParams();
  if (f.name?.trim()) p.set('name', f.name.trim());
  if (f.role?.trim()) p.set('role', f.role.trim());
  f.companies?.forEach((v) => p.append('companies', v));
  f.locations?.forEach((v) => p.append('locations', v));
  f.status?.forEach((v) => p.append('status', v));
  if (f.matchMin != null) p.set('match_min', String(f.matchMin));
  if (f.matchMax != null) p.set('match_max', String(f.matchMax));
  return p;
}

export function fetchPipelineCandidates(
  pipelineId: string,
  jobId: string,
  page = 1,
  limit = 50,
  filters: CandidateFilters = {},
  sortBy?: 'matchScore' | 'createdAt',
  sortOrder: 'asc' | 'desc' = 'desc',
): Promise<CandidateListResponse> {
  const p = filterParams(filters);
  p.set('page', String(page));
  p.set('limit', String(limit));
  if (sortBy) { p.set('sort_by', sortBy); p.set('sort_order', sortOrder); }
  return get(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/candidates?${p}`);
}

/**
 * Option lists for the column filter dropdowns.
 *
 * Counts honour the OTHER columns' active filters but not the column's own, so
 * the list you're picking from never collapses to your current selection.
 */
export function fetchCandidateFacets(
  pipelineId: string, jobId: string, filters: CandidateFilters = {},
): Promise<CandidateFacets> {
  return get(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/candidates/facets?${filterParams(filters)}`);
}

export function patchCandidate(
  candidateId: string,
  body: { isAccepted?: boolean; rejectionReason?: string | null },
): Promise<Candidate> {
  return patch(`/api/v1/pipelines/candidates/${candidateId}`, body);
}

/** Fetch a single candidate (full doc incl. Apollo + Apify enrichment). */
export function fetchCandidate(candidateId: string): Promise<Candidate> {
  return get(`/api/v1/pipelines/candidates/${candidateId}`);
}

export function enrichCandidate(candidateId: string): Promise<Candidate> {
  return post(`/api/v1/pipelines/candidates/${candidateId}/enrich`, {});
}

/** Which enrichment engine(s) the Enrich action runs. */
export type EnrichMode = 'apollo' | 'apify' | 'both';

/**
 * Queue a background bulk enrichment for the selected candidates in a job.
 * `mode` picks the engine: `'apollo'` (verified email + contact, no scrape),
 * `'apify'` (deep profile scrape only), or `'both'` (Apollo → Apify, default).
 * Poll the pipeline's job.enrichStatus.
 */
export function bulkEnrichJobCandidates(
  pipelineId: string, jobId: string, candidateIds: string[] | null,
  mode: EnrichMode = 'both',
): Promise<{ success: boolean; queued: boolean }> {
  // null → enrich every candidate in the job (the "Enrich all" button); an
  // array → only those selected. The backend treats null/empty the same way.
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/enrich`, { candidateIds, mode });
}

/**
 * Delete the selected candidates from a job. A candidate surfaced only by this
 * job is removed outright; one shared with other jobs is just detached from
 * this job. Returns the count actually deleted.
 */
export function deleteJobCandidates(
  pipelineId: string, jobId: string, candidateIds: string[],
): Promise<{ success: boolean; deleted: number }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/candidates/delete`, { candidateIds });
}

/** Filters for the Apify LinkedIn-search discovery questionnaire. */
export interface DiscoverFilters {
  searchQuery?: string;
  maxItems?: number;
  locations?: string[];
  currentJobTitles?: string[];
  pastJobTitles?: string[];
  currentCompanies?: string[];
  pastCompanies?: string[];
  schools?: string[];
  industryIds?: string[];
  firstNames?: string[];
  lastNames?: string[];
  companyHqLocations?: string[];
  excludeLocations?: string[];
  excludeCurrentCompanies?: string[];
  excludePastCompanies?: string[];
  excludeSchools?: string[];
  excludeCurrentJobTitles?: string[];
  excludePastJobTitles?: string[];
  excludeIndustryIds?: string[];
  excludeSeniorityLevel?: string;
  excludeFunction?: string;
  yearsOfExperience?: string;
  yearsAtCurrentCompany?: string;
  seniorityLevel?: string;
  function?: string;
  companyHeadcount?: string;
  profileLanguages?: string[];
  recentlyChangedJobs?: boolean;
  recentlyPostedOnLinkedin?: boolean;
  // ── Agentic search controls (stripped server-side before the actor call) ──
  /** Retry a zero-result search with agent-broadened filters. Default true. */
  autoBroaden?: boolean;
  /** The brief sent to suggest-filters, so the Broadener knows the role's intent. */
  brief?: SearchBrief;
  /** The Strategist's pre-planned fallbacks, echoed back from suggest-filters. */
  broadeningLadder?: BroadeningStep[];
  /** The Strategist's two-tier domain anchor, echoed back from suggest-filters.
   *  Guards broadening against changing the target profession. */
  domainAnchor?: DomainAnchor;
  /** Adjacent-specialty titles — never searched automatically; offered as
   *  opt-in chips when the exact-specialty search comes up short. */
  adjacentTitles?: string[];
}

/** The words that make the role THIS role — see the Strategist's output. */
export interface DomainAnchor {
  /** Specialization words ("hcm", "payroll"). A title without one is a
   *  different profession. */
  coreTerms: string[];
  /** Platform/vendor words shared across professions ("sap"). Matching one of
   *  these alone does NOT make a title in-domain. */
  ecosystemTerms: string[];
}

/** The recruiter's optional hints for the Strategist. All fields optional. */
export interface SearchBrief {
  seniorityHint?: string;
  mustHaveSkills?: string[];
  niceToHaveSkills?: string[];
  minYears?: number;
  targetIndustries?: string[];
  targetCompanies?: string[];
  excludeCompanies?: string[];
  languages?: string[];
  workModel?: '' | 'onsite' | 'hybrid' | 'remote';
  openToRelocation?: boolean;
  notes?: string;
}

export interface BroadeningStep {
  step: number;
  action: string;
  detail: string;
  filters: DiscoverFilters;
}

/** Apollo-specific inputs proposed alongside the Apify filters (engine matches
 *  differently, so it gets its own plan — not the Apify filter set reused). */
export interface ApolloPlan {
  titles: string[];
  /** 1–3 defining skills, matched as free-text q_keywords (more AND-narrows). */
  qKeywords: string[];
  /** Where the candidate lives, not the employer HQ. */
  locations: string[];
  /** Apollo seniority codes: owner, founder, c_suite, partner, vp, head,
   *  director, manager, senior, entry, intern. */
  seniorities: string[];
}

/** The Strategist's proposal for one job. */
export interface SearchStrategy {
  interpretedRole: string;
  /** The single interpreted, LinkedIn-real title anchoring both engines. */
  focusTitle: string;
  titleReasoning: string;
  filters: DiscoverFilters;
  /** Engine-appropriate Apollo inputs proposed from the same brief. */
  apolloPlan: ApolloPlan;
  rationale: { field: string; why: string }[];
  /** What may never be relaxed away — enforced in code by the discovery loop. */
  domainAnchor: DomainAnchor;
  /** Neighbouring-specialty titles. NEVER searched automatically — they become
   *  the opt-in "widen the search" chips when the specialty pool is thin. */
  adjacentTitles: string[];
  broadeningLadder: BroadeningStep[];
  /** 0 means the AI didn't run (no key / error) and this is a literal prefill. */
  confidence: number;
  warnings: string[];
}

/**
 * AI-propose the LinkedIn search filters for a job (prefill).
 *
 * Reasoning only — one LLM call, no vendor spend, no candidates sourced. Never
 * rejects: with no LLM key it returns the literal job-title prefill with
 * confidence 0 and a warning, so the form always has something to show.
 */
export function suggestJobFilters(
  pipelineId: string, jobId: string, brief?: SearchBrief,
): Promise<{ success: boolean; strategy: SearchStrategy }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/suggest-filters`, brief || {});
}

/**
 * Run the Apify LinkedIn-search actor for a job with the questionnaire filters,
 * store results as candidates, then auto-enrich each (background). Poll the
 * pipeline's job.searchStatus → then job.enrichStatus.
 *
 * With `autoBroaden`, a zero-result search is retried with agent-relaxed filters
 * rather than returning empty; each attempt lands on the job's `searchAttempts`.
 */
export function discoverJobCandidates(
  pipelineId: string, jobId: string, filters: DiscoverFilters,
): Promise<{ success: boolean; queued: boolean }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/discover`, filters);
}

/**
 * Apollo people-search filters (the alternative sourcing engine). Everything is
 * ANDed into one Apollo search. `skills` has no structured Apollo filter, so it
 * is matched as free-text keywords (a soft match across the profile).
 */
export interface ApolloDiscoverFilters {
  titles?: string[];
  locations?: string[];
  /** Key skills → Apollo q_keywords (soft, free-text match). */
  skills?: string[];
  /** Apollo seniority codes: owner, c_suite, partner, vp, head, director, manager, senior, entry. */
  seniorities?: string[];
  industries?: string[];
  maxItems?: number;
}

/**
 * Run an Apollo people-search for a job with the questionnaire filters and store
 * the results as candidates (search-only — no LinkedIn scrape, no auto-enrich).
 * Contact info stays masked until revealed on demand. Poll the pipeline's
 * job.searchStatus (there is no enrich phase to wait on).
 */
export function discoverApolloCandidates(
  pipelineId: string, jobId: string, filters: ApolloDiscoverFilters,
): Promise<{ success: boolean; queued: boolean }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/discover-apollo`, filters);
}

/** The unified discovery payload — one screen, both engines run concurrently. */
export interface CombinedDiscoverPayload {
  /** LinkedIn (Apify) filters, incl. the agentic controls the backend strips. */
  apify: DiscoverFilters;
  /** Apollo people-search filters. */
  apollo: ApolloDiscoverFilters;
  /** Which engines to fire. Both default on; toggle either off to skip it. */
  engines: { apify: boolean; apollo: boolean };
}

/**
 * Run Apify (LinkedIn) and Apollo people-search CONCURRENTLY from one payload and
 * merge the results into the one candidate list (deduped by LinkedIn URL). Poll
 * the job's rollup `searchStatus`, plus `apifySearchStatus` / `apolloSearchStatus`
 * (+ `apifyKept` / `apolloKept`) for the per-engine breakdown.
 */
export function discoverCombined(
  pipelineId: string, jobId: string, payload: CombinedDiscoverPayload,
): Promise<{ success: boolean; queued: boolean }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/discover-combined`, payload);
}

/**
 * Start a background match run: score the job's JD against the selected
 * candidates' enriched profiles (auto-enriching any that aren't yet). Returns a
 * matchRunId to poll via fetchMatchRun.
 */
/** Recruiter edits to the parsed requirements, applied to one match run (and
 *  persisted onto the job's role spec). Omitted keys keep the parsed values;
 *  an empty list is a deliberate "none" and is honoured. */
export interface RequirementsEdit {
  mustHaveSkills?: string[];
  niceToHaveSkills?: string[];
  minYears?: number | null;
}

export function runJobMatch(
  pipelineId: string, jobId: string, candidateIds: string[], returnTop?: number,
  requirements?: RequirementsEdit,
): Promise<{ success: boolean; matchRunId: string }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/match`, {
    candidateIds, returnTop, ...(requirements || {}),
  });
}

export interface JobRequirements {
  jdId: string;
  requirements: {
    title?: string | null;
    mustHaveSkills: string[];
    niceToHaveSkills: string[];
    minYears?: number | null;
    location?: string | null;
    seniority?: string | null;
  };
  requirementsSource: 'parsed' | 'recruiter_edited';
}

/** The parsed hiring requirements a match run will score against — powers the
 *  review-and-edit step before Run Match. Parses the JD on first ask. */
export function fetchJobRequirements(pipelineId: string, jobId: string): Promise<JobRequirements> {
  return get(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/requirements`);
}

// ── Candidate Matching (CV ↔ JD engine) ─────────────────────────────────────

export interface CvBatchStatus {
  batchId: string;
  total: number;
  counts: Record<string, number>;
  complete: boolean;
}

export interface CvListItem {
  _id: string;
  sourceFileName?: string;
  status: 'pending' | 'parsed' | 'embedded' | 'failed';
  error?: string | null;
  profile?: {
    fullName?: string | null;
    currentTitle?: string | null;
    location?: string | null;
    totalYears?: number | null;
    skills?: string[];
  } | null;
  createdAt?: string;
}

/** Evidence for one must-have skill: how much credit it earned and why. */
export interface SkillEvidence {
  skill: string;
  /** 0..1 — 1 exact/specific, 0.75 fuzzy, 0.5 broader term, 0 no match. */
  credit: number;
  method: 'exact' | 'specific' | 'broader' | 'fuzzy' | 'none';
  /** The candidate skill that earned the credit. */
  via?: string | null;
  confidence: number;
  note: string;
}

/** One weighted component of a score. */
export interface ScoreComponent {
  key: 'semantic' | 'skillCoverage' | 'experience' | 'location';
  label: string;
  /** False when the JD never stated this requirement — weight redistributed, not free points. */
  applicable: boolean;
  /** The raw 0..100 component value. */
  value: number;
  baseWeight: number;
  /** Effective weight after redistribution (0 when not applicable). */
  weight: number;
  points: number;
  maxPoints: number;
  lost: number;
  note: string;
  /** Per-must-have evidence — present on the skillCoverage component only. */
  skills?: SkillEvidence[];
}

/** The full "why this score" record for one candidate. */
export interface ScoreBreakdown {
  version: string;
  total: number;
  /** The weighted score before any must-have ceiling was applied. */
  base: number;
  ceiling: number;
  /** Set when the must-have ceiling held the score below `base`. */
  cappedBy?: string | null;
  similarity: number;
  components: ScoreComponent[];
  formula: string;
}

export interface MatchedCandidate {
  candidateId: string;
  /** "cv" (uploaded CV corpus) or "pipeline" (Apify-enriched candidate). */
  source?: 'cv' | 'pipeline';
  fullName?: string | null;
  currentTitle?: string | null;
  location?: string | null;
  sourceFileName?: string | null;
  /** LinkedIn photo (pipeline candidates only). Public but signed and expiring —
   *  treat a dead link as normal and fall back to initials. */
  photoUrl?: string | null;
  score: number;
  subscores: Record<string, number>;
  /** Absent on runs scored before the breakdown existed. */
  breakdown?: ScoreBreakdown | null;
  reasons: string[];
  /** Whether `reasons` came from the rubric judge, the LLM, or the deterministic fallback. */
  reasoning?: 'judge' | 'llm' | 'deterministic';
  /** LinkedIn's own "open to work" flag from the scraped profile. */
  openToWork?: boolean;
  /** Must-haves NOTHING in the profile evidences. Deterministic — never LLM prose. */
  gaps: string[];
  /** Must-haves with related-but-incomplete evidence. NOT gaps. */
  partial?: Array<{ skill: string; credit: number; via?: string | null; method: string; note: string }>;
  /** Which retrieval channel(s) surfaced this candidate (hybrid runs).
   *  "lexical"-only = found by the JD's own keywords, missed by pure semantics —
   *  exactly the candidate the old retrieval silently dropped. */
  retrieval?: { channels?: string[]; semanticRank?: number | null; lexicalRank?: number | null; rrf?: number } | null;
  /** QA auditor outcome for this candidate: score corrected upward on verified
   *  evidence, and/or credited skills flagged as unsupported (annotation only). */
  qa?: {
    corrected?: boolean;
    originalScore?: number;
    verifiedSkills?: Array<{ skill: string; quote?: string }>;
    falsePositives?: Array<{ skill: string; why?: string }>;
  } | null;
  contact: { email?: string | null; phone?: string | null; linkedin?: string | null };
}

/** A candidate that never reached scoring, and why. */
export interface ExcludedCandidate {
  candidateId: string;
  fullName?: string | null;
  reason: string;
}

/** Every candidate the run scored — the evidence behind the top-N. */
export interface MatchAnalysis {
  scoringVersion?: string;
  baseWeights?: Record<string, number>;
  reasonedTopN?: number;
  candidates?: MatchedCandidate[];
  excluded?: ExcludedCandidate[];
}

export interface MatchResult {
  matchRunId: string;
  jdId: string;
  jdTitle?: string | null;
  requirements: {
    title?: string | null;
    mustHaveSkills?: string[];
    niceToHaveSkills?: string[];
    minYears?: number | null;
    location?: string | null;
    seniority?: string | null;
  };
  candidatesConsidered: number;
  results: MatchedCandidate[];
}

/** Upload a CV dump (multipart). Returns a batchId to poll. */
export async function uploadCvs(files: File[]): Promise<{ batchId: string; received: number }> {
  const fd = new FormData();
  files.forEach((f) => fd.append('files', f));
  const res = await fetch(`${API_BASE}/api/v1/matching/cv/upload`, { method: 'POST', body: fd });
  return handleResponse(res, `upload → ${res.status}`);
}

export function fetchCvBatchStatus(batchId: string): Promise<CvBatchStatus> {
  return get(`/api/v1/matching/cv/batch/${batchId}`);
}

export function fetchCvs(page = 1, limit = 20): Promise<{ total: number; items: CvListItem[] }> {
  return get(`/api/v1/matching/cv?page=${page}&limit=${limit}`);
}

/** Corpus health: CVs by status. `counts.failed` > 0 means unmatchable CVs. */
export function fetchCvStats(): Promise<{ total: number; counts: Record<string, number> }> {
  return get('/api/v1/matching/cv/stats');
}

/** Re-ingest every failed CV from its stored original file (current parser). */
export function reprocessFailedCvs(): Promise<{ queued: number; unrecoverable: string[]; batchId: string | null }> {
  return post('/api/v1/matching/cv/reprocess-failed', {});
}

/** Run matching from a pasted JD text. */
export function runMatchingText(jdText: string, returnTop?: number): Promise<MatchResult> {
  return post('/api/v1/matching/run/json', { jdText, returnTop });
}

/** Run matching from an uploaded JD document (multipart). */
export async function runMatchingFile(file: File, returnTop?: number): Promise<MatchResult> {
  const fd = new FormData();
  fd.append('file', file);
  if (returnTop != null) fd.append('returnTop', String(returnTop));
  const res = await fetch(`${API_BASE}/api/v1/matching/run`, { method: 'POST', body: fd });
  return handleResponse(res, `match → ${res.status}`);
}

// ── Saved match runs (history) ──────────────────────────────────────────────

export interface SavedMatchRun {
  _id: string;
  jdId?: string;
  jdTitle?: string | null;
  jdText?: string | null;
  jdFileName?: string | null;
  candidatesConsidered: number;
  results: MatchedCandidate[];
  createdAt?: string | null;
  /** Pipeline runs carry a lifecycle status + origin; CV runs are implicitly done. */
  status?: 'running' | 'completed' | 'failed';
  source?: 'cv' | 'pipeline';
  pipelineId?: string;
  jobId?: string;
  requirements?: MatchResult['requirements'];
  error?: string | null;
  /** Every candidate scored, not just the top-N in `results`. */
  analysis?: MatchAnalysis | null;
  params?: { retrieveK?: number; reasonTopN?: number; returnTop?: number; hybrid?: boolean; semanticHits?: number; lexicalHits?: number };
  /** Set when the JD parsed to zero must-haves despite being a real JD — the
   *  ranking is similarity-driven and the recruiter must be told, not left to
   *  assume a normal checklist run. */
  requirementsWarning?: string | null;
  /** Run-level QA audit summary (adversarial second reader). */
  qa?: {
    status?: string;
    reviewed?: number;
    fnFlagsRaised?: number;
    fnFlagsVerified?: number;
    fnCorrected?: number;
    fpFlagsRaised?: number;
  } | null;
  /** Live streaming progress for pipeline runs (per-candidate queue). */
  progress?: { total: number; processed: number; considered: number } | null;
  logs?: Array<{ ts?: string; message: string; level?: 'info' | 'warn' | 'error' }> | null;
}

export function fetchMatchRuns(page = 1, limit = 20): Promise<{ total: number; items: SavedMatchRun[] }> {
  return get(`/api/v1/matching/runs?page=${page}&limit=${limit}`);
}

/** Fetch a single match run (poll this for a pipeline run's status → completed). */
export function fetchMatchRun(matchRunId: string): Promise<SavedMatchRun> {
  return get(`/api/v1/matching/run/${matchRunId}`);
}

/** Direct download URL for a candidate's CV (original file, or parsed .txt fallback). */
export function cvDownloadUrl(candidateId: string): string {
  return `${API_BASE}/api/v1/matching/cv/${candidateId}/download`;
}

// ── Outreach email ──────────────────────────────────────────────────────────

export interface OutreachDraft {
  to: string | null;
  subject: string;
  body: string;
  sendEnabled: boolean;
}

/** Generate a professional outreach email draft for a candidate. */
export function draftOutreach(candidateId: string, roleTitle?: string): Promise<OutreachDraft> {
  return post('/api/v1/matching/outreach/draft', { candidateId, roleTitle });
}

/** Send the (edited) email. Throws if SMTP isn't configured yet. */
export function sendOutreach(payload: { to: string; subject: string; body: string; candidateId?: string }): Promise<{ sent: boolean; to: string }> {
  return post('/api/v1/matching/outreach/send', payload);
}

// ── Outreach CRM (Leads / Candidates funnel) ────────────────────────────────

export type OutreachAudience = 'leads' | 'candidates';
export type OutreachStatus =
  | 'sent' | 'delivered' | 'opened' | 'clicked'
  | 'replied' | 'meeting' | 'bounced' | 'unsubscribed';

export interface OutreachRow {
  id: string;
  name: string;
  secondary: string;
  title: string;
  email: string;
  channel: 'Email' | 'LinkedIn';
  status: OutreachStatus;
  replyClass?: string | null;
  lastActivity: string;
  lastActivityAt?: string | null;
  sentAt?: string | null;
}

export interface OutreachListResponse {
  total: number;
  page: number;
  limit: number;
  items: OutreachRow[];
}

export interface OutreachMetrics {
  total: number;
  opened: number;
  replied: number;
  meetings: number;
  unsubscribed: number;
  bounced: number;
}

export interface OutreachConfig {
  provider: string;
  sendEnabled: boolean;
  smartleadWebhookVerified: boolean;
  calcomWebhookVerified: boolean;
}

export function fetchOutreach(
  audience: OutreachAudience, status?: string, page = 1, limit = 100,
): Promise<OutreachListResponse> {
  const q = new URLSearchParams({ audience, page: String(page), limit: String(limit) });
  if (status && status !== 'all') q.set('status', status);
  return get(`/api/v1/outreach?${q.toString()}`);
}

export function fetchOutreachMetrics(audience: OutreachAudience): Promise<OutreachMetrics> {
  return get(`/api/v1/outreach/metrics?audience=${audience}`);
}

export function fetchOutreachConfig(): Promise<OutreachConfig> {
  return get('/api/v1/outreach/config');
}

export interface EnrollPayload {
  email: string;
  name?: string;
  title?: string;
  company?: string;
  roleTitle?: string;
  audience?: 'lead' | 'candidate';
  campaignName?: string;
  candidateId?: string;
  leadId?: string;
}

export interface EnrollResult {
  messageId?: string;
  tracked?: boolean;
  sent?: boolean;
  note?: string;
  ok?: boolean;
  error?: string;
}

/** Track/enroll a contact into the outreach CRM (and Smartlead if configured). */
export function enrollOutreach(payload: EnrollPayload): Promise<EnrollResult> {
  return post('/api/v1/outreach/enroll', payload);
}

// ── Cost Analyser ───────────────────────────────────────────────────────────

export type CostRange = '7d' | '14d' | '30d' | '90d' | 'all';
export type CostStage = 'job_search' | 'candidate_search' | 'matching' | 'outreach' | 'company_analysis';

export interface CostServiceSlice { service: string; cost: number; fixed?: boolean }
export interface CostStageSlice { stage: string; cost: number; count?: number }
export interface CostSubscription {
  service: string; monthlyUsd: number;
  creditsUsed?: number | null; includedCredits?: number | null;
  usdPerCredit?: number | null; utilizationPct?: number | null; configured?: boolean;
}
export interface CostInsight { type: string; severity: 'info' | 'warn'; title: string; body: string }

export interface CostUnitEconomics {
  sourced: number; enriched: number; rejected?: number; enrichedRejected?: number; matches: number;
  perSourced?: number | null; perEnriched?: number | null; perMatch?: number | null;
  wastedEnrichmentUsd?: number;
}

export interface CostLineItem {
  groupKey: string;
  stage?: string | null;
  label?: string | null;
  cost: number;
  credits?: number;
  byService: CostServiceSlice[];
  refs?: Record<string, unknown>;
  found?: number | null;
  enriched?: number | null;
  rejected?: number | null;
  enrichedRejected?: number | null;
  perEnriched?: number | null;
}

export interface CostOverview {
  range: string;
  operational: number;
  operationalPrev?: number | null;
  deltaPct?: number | null;
  runRate?: number | null;
  fixedMonthly: number;
  billTotal: number;
  apolloRate: number;
  unitEconomics: CostUnitEconomics;
  subscriptions: CostSubscription[];
  byService: CostServiceSlice[];
  byStage: CostStageSlice[];
  daily: { date: string; cost: number }[];
  insights: CostInsight[];
  topSearches: CostLineItem[];
}

export interface CostGroup {
  stage: string;
  range: string;
  total: number;
  count: number;
  creditsUsed: number;
  apolloRate?: number;
  enriched?: number;
  enrichedRejected?: number;
  perEnriched?: number | null;
  insights?: CostInsight[];
  items: CostLineItem[];
}

export interface CostEventRow {
  service: string;
  operation: string;
  model?: string | null;
  unit: string;
  quantity: number | { in?: number; out?: number };
  unitPriceUsd?: number | null;
  costUsd: number;
  allocated: boolean;
  createdAt?: string | null;
}

export interface CostLineDetail {
  groupKey: string;
  stage?: string | null;
  label?: string | null;
  total: number;
  apolloRate?: number;
  found?: number | null;
  enriched?: number | null;
  rejected?: number | null;
  enrichedRejected?: number | null;
  byService: CostServiceSlice[];
  events: CostEventRow[];
  insights?: CostInsight[];
  refs?: Record<string, unknown>;
}

export interface PriceBookEntry {
  _id: string;
  service: string;
  model?: string | null;
  kind: 'metered' | 'subscription';
  unit?: string;
  inUsdPer1M?: number;
  outUsdPer1M?: number;
  usdPerUnit?: number;
  monthlyUsd?: number;
  usdPerCredit?: number;
  includedCredits?: number;
  allocateBy?: string;
  source?: string;
}

export function fetchCostOverview(range: CostRange = '30d'): Promise<CostOverview> {
  return get(`/api/v1/cost/overview?range=${range}`);
}

export function fetchCostGroup(stage: CostStage, range: CostRange = '30d'): Promise<CostGroup> {
  return get(`/api/v1/cost/group/${stage}?range=${range}`);
}

export function fetchCostLineItem(groupKey: string): Promise<CostLineDetail> {
  return get(`/api/v1/cost/search/${encodeURIComponent(groupKey)}`);
}

export function fetchPriceBook(): Promise<{ items: PriceBookEntry[] }> {
  return get('/api/v1/cost/price-book');
}

export function updatePriceEntry(body: {
  service: string; model?: string | null;
  monthlyUsd?: number; usdPerCredit?: number; includedCredits?: number;
  inUsdPer1M?: number; outUsdPer1M?: number; usdPerUnit?: number; allocateBy?: string;
}): Promise<PriceBookEntry> {
  return patch('/api/v1/cost/price-book', body);
}

// ── QA reports (operator/admin only — the match-QA auditor's run-wise metrics) ─

export interface QaMetrics {
  candidatesReviewed: number;
  fnFlagsRaised: number;
  fnFlagsVerified: number;
  fnFlagsDiscarded: number;
  fnCorrected: number;
  fpFlagsRaised: number;
}

export interface QaScoreCorrection {
  candidateId: string;
  fullName?: string | null;
  from: number;
  to: number;
  skills: string[];
}

export interface QaSourcingMetrics {
  kept: number;
  locationRejected: number;
  mismatchesRaised: number;
  mismatchesFlagged: number;
  lowConfidenceNoted: number;
}

export interface QaSourcingFlag {
  candidateId: string;
  reason?: string;
  likelyActualSpecialty?: string;
  confidence: number;
}

export interface QaReportSummary {
  id: string;
  kind: 'match' | 'sourcing';
  matchRunId?: string;
  pipelineId?: string | null;
  jobId?: string | null;
  jdTitle?: string | null;
  status: 'completed' | 'skipped' | 'failed';
  model?: string;
  metrics: QaMetrics & Partial<QaSourcingMetrics>;
  scoreCorrections: QaScoreCorrection[];
  flags?: QaSourcingFlag[];
  createdAt?: string | null;
}

export interface QaReportDetail extends QaReportSummary {
  error?: string | null;
  perCandidate: Array<{
    candidateId: string;
    fullName?: string | null;
    originalScore?: number | null;
    correctedScore?: number | null;
    falseNegativesVerified: Array<{ skill: string; quote?: string; why?: string }>;
    falseNegativesDiscarded: Array<{ skill?: string; quote?: string }>;
    falsePositives: Array<{ skill?: string; why?: string }>;
  }>;
}

/** 200 {isAdmin} for everyone authenticated — the UI hides the QA nav when false. */
export function fetchQaAccess(): Promise<{ isAdmin: boolean }> {
  return get('/api/v1/qa/access');
}

export function fetchQaReports(limit = 50): Promise<{
  totals: QaMetrics & { runs: number };
  sourcingTotals: { runs: number; kept: number; locationRejected: number; mismatchesFlagged: number };
  reports: QaReportSummary[];
}> {
  return get(`/api/v1/qa/reports?limit=${limit}`);
}

export function fetchQaReport(id: string): Promise<QaReportDetail> {
  return get(`/api/v1/qa/reports/${id}`);
}
