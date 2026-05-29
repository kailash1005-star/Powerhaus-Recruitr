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
