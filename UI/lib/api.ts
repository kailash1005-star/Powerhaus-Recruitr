const API_BASE = process.env.NEXT_PUBLIC_API_URL || "https://job-hunt-204519078454.asia-south1.run.app"

export interface RunStats {
  totalJobsScraped: number
  uniqueCompanies: number
  acceptedCompanies: number
  rejectedCompanies: number
  totalProspects: number
  inserted?: number
  duplicates?: number
  acceptedJobs?: number
  rejectedJobs?: number
  skippedCompanies?: number
}

export interface RunConfig {
  searchTitles: string[]
  searchLocations: string[]
  hoursOld: number
  resultsPerSearch: number
  siteName: string[]
  icpConfigSnapshot?: { icpConfigId: string | null; version: number } | null
}

export interface Run {
  id: string | null
  _id?: string
  title: string
  source: string
  status: "active" | "completed" | "paused" | "cancelled"
  runStartedAt: string
  runEndedAt: string | null
  stats: RunStats
  runConfig: RunConfig
  createdAt: string | null
  updatedAt: string | null
}

export interface RunJob {
  _id: string
  runId: string
  title: string
  company: string
  location: string
  boardName: string
  externalId: string
  companyId: string | null
  qualityStatus: "excellent" | "good" | "fair" | "poor"
  rejectionReason: string | null
  jobDetails: {
    jobUrl: string
    companyUrl: string
    searchKeyword: string
    searchLocation: string
    description: string
    [key: string]: unknown
  }
  createdAt: string
  updatedAt: string
}

export interface AllJobsResponse {
  total: number
  page: number
  limit: number
  pages: number
  jobs: RunJob[]
}

export interface RunJobsResponse {
  total: number
  page: number
  limit: number
  pages: number
  jobs: RunJob[]
}

export async function fetchRuns(page = 1, limit = 10): Promise<Run[]> {
  const res = await fetch(`${API_BASE}/api/v1/runs?page=${page}&limit=${limit}`)
  if (!res.ok) throw new Error("Failed to fetch runs")
  return res.json()
}

export async function fetchRun(id: string): Promise<Run> {
  const res = await fetch(`${API_BASE}/api/v1/runs/${id}`)
  if (!res.ok) throw new Error("Failed to fetch run")
  return res.json()
}

// ── Analytics ────────────────────────────────────────────────────────────

export interface JobsAnalytics {
  days: number
  summary: { total: number; accepted: number; rejected: number; acceptanceRate: number }
  byBoard: { board: string; count: number }[]
  byQuality: { status: string; count: number }[]
  byRejectionReason: { reason: string; count: number }[]
  byKeyword: { keyword: string; count: number }[]
  byLocation: { location: string; count: number }[]
  dailyTrend: { date: string; total: number; accepted: number; rejected: number }[]
}

export interface CompaniesAnalytics {
  summary: { total: number; accepted: number; rejected: number; acceptanceRate: number; avgEmployees: number }
  byEligibility: { status: string; count: number }[]
  byIndustry: { industry: string; count: number }[]
  byRejectionReason: { reason: string; count: number }[]
  bySize: { range: string; count: number }[]
}

export async function fetchJobsAnalytics(days = 7): Promise<JobsAnalytics> {
  const res = await fetch(`${API_BASE}/api/v1/analytics/jobs?days=${days}`)
  if (!res.ok) throw new Error("Failed to fetch jobs analytics")
  return res.json()
}

export async function fetchCompaniesAnalytics(): Promise<CompaniesAnalytics> {
  const res = await fetch(`${API_BASE}/api/v1/analytics/companies`)
  if (!res.ok) throw new Error("Failed to fetch companies analytics")
  return res.json()
}

export async function fetchRunJobs(
  runId: string,
  page = 1,
  limit = 20,
  quality?: string,
  sortBy?: string,
  sortOrder?: string
): Promise<RunJobsResponse> {
  let url = `${API_BASE}/api/v1/runs/${runId}/jobs?page=${page}&limit=${limit}`
  if (quality) url += `&quality=${quality}`
  if (sortBy) url += `&sort_by=${sortBy}`
  if (sortOrder) url += `&sort_order=${sortOrder}`
  const res = await fetch(url)
  if (!res.ok) throw new Error("Failed to fetch run jobs")
  return res.json()
}

// ── All Jobs ─────────────────────────────────────────────────────────────

export async function fetchAllJobs(
  page = 1,
  limit = 50,
  sortBy?: string,
  sortOrder?: string
): Promise<AllJobsResponse> {
  let url = `${API_BASE}/api/v1/jobs?page=${page}&limit=${limit}`
  if (sortBy) url += `&sort_by=${sortBy}`
  if (sortOrder) url += `&sort_order=${sortOrder}`
  const res = await fetch(url)
  if (!res.ok) throw new Error("Failed to fetch jobs")
  return res.json()
}
