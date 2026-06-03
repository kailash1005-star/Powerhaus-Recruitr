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
