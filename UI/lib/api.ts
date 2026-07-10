// ─────────────────────────────────────────────────────────────────────────────
// Recruitr — Real API client
// Mirrors the backend at http://127.0.0.1:8000
// ─────────────────────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `POST ${path} → ${res.status}`);
  }
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`);
  return res.json();
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `PATCH ${path} → ${res.status}`);
  }
  return res.json();
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

export type PipelineJobSearchStatus = 'queued' | 'running' | 'completed' | 'failed';

export type PipelineJobEnrichStatus = 'queued' | 'running' | 'completed' | 'failed';

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
  /** Bulk-enrichment (Apollo→Apify) background status + counts. */
  enrichStatus?: PipelineJobEnrichStatus | null;
  enrichError?: string | null;
  enrichCounts?: Record<string, number> | null;
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
  headline?: string;
  currentTitle?: string;
  currentCompany?: string;
  currentCompanyDomain?: string;
  location?: string;
  matchScore: number;
  matchReasons: string[];
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
  apifyEnrichmentStatus?: 'enriched' | 'not_found' | null;
  apifyEnrichment?: {
    profile?: Record<string, unknown> | null;
    contact?: Record<string, unknown> | null;
    source?: Record<string, unknown> | null;
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

export function rerunPipelineJob(pipelineId: string, jobId: string): Promise<unknown> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/rerun`, {});
}

export function fetchPipelineCandidates(
  pipelineId: string,
  jobId: string,
  page = 1,
  limit = 50,
  quality: 'all' | 'accepted' | 'rejected' = 'all',
  sortBy?: 'matchScore' | 'createdAt',
  sortOrder: 'asc' | 'desc' = 'desc',
): Promise<CandidateListResponse> {
  let url = `/api/v1/pipelines/${pipelineId}/jobs/${jobId}/candidates?page=${page}&limit=${limit}`;
  if (quality !== 'all') url += `&quality=${quality}`;
  if (sortBy) url += `&sort_by=${sortBy}&sort_order=${sortOrder}`;
  return get(url);
}

export function patchCandidate(
  candidateId: string,
  body: { isAccepted?: boolean; rejectionReason?: string | null },
): Promise<Candidate> {
  return patch(`/api/v1/pipelines/candidates/${candidateId}`, body);
}

export function enrichCandidate(candidateId: string): Promise<Candidate> {
  return post(`/api/v1/pipelines/candidates/${candidateId}/enrich`, {});
}

/**
 * Queue a background bulk enrichment (Apollo /people/match → Apify deep profile)
 * for the selected candidates in a job. Poll the pipeline's job.enrichStatus.
 */
export function bulkEnrichJobCandidates(
  pipelineId: string, jobId: string, candidateIds: string[],
): Promise<{ success: boolean; queued: boolean }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/enrich`, { candidateIds });
}

/**
 * Start a background match run: score the job's JD against the selected
 * candidates' enriched profiles (auto-enriching any that aren't yet). Returns a
 * matchRunId to poll via fetchMatchRun.
 */
export function runJobMatch(
  pipelineId: string, jobId: string, candidateIds: string[], returnTop?: number,
): Promise<{ success: boolean; matchRunId: string }> {
  return post(`/api/v1/pipelines/${pipelineId}/jobs/${jobId}/match`, { candidateIds, returnTop });
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

export interface MatchedCandidate {
  candidateId: string;
  /** "cv" (uploaded CV corpus) or "pipeline" (Apify-enriched candidate). */
  source?: 'cv' | 'pipeline';
  fullName?: string | null;
  currentTitle?: string | null;
  location?: string | null;
  score: number;
  subscores: Record<string, number>;
  reasons: string[];
  gaps: string[];
  contact: { email?: string | null; phone?: string | null; linkedin?: string | null };
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
  if (!res.ok) throw new Error((await res.text()) || `upload → ${res.status}`);
  return res.json();
}

export function fetchCvBatchStatus(batchId: string): Promise<CvBatchStatus> {
  return get(`/api/v1/matching/cv/batch/${batchId}`);
}

export function fetchCvs(page = 1, limit = 20): Promise<{ total: number; items: CvListItem[] }> {
  return get(`/api/v1/matching/cv?page=${page}&limit=${limit}`);
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
  if (!res.ok) throw new Error((await res.text()) || `match → ${res.status}`);
  return res.json();
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
