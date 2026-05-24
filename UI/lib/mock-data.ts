// ──────────────────────────────────────────────────────────────────
// Mock Data Generator with Loading States
// ──────────────────────────────────────────────────────────────────

import type { Prospect, Company, ICPConfig, Run, RunDetail, DashboardMetrics, Integration, Settings } from '@/types';

// Simulated API delay
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const mockProspects: Prospect[] = [
  {
    id: 'p1',
    businessName: 'TechStart Solutions',
    category: 'Software Development',
    city: 'San Francisco',
    state: 'CA',
    country: 'USA',
    status: 'ENRICHED',
    hasEmail: true,
    rating: 4.2,
    reviewCount: 38,
    lastUpdated: '05/21/2026 · 14:32',
    address: '123 Market St, San Francisco, CA 94103',
    phone: '+1 415 555 0100',
    website: null,
    contactName: 'Sarah Chen',
    contactEmail: 's.chen@techstart.io',
    contactTitle: 'CEO',
    linkedinUrl: 'https://linkedin.com/in/sarahchen',
    companySize: '11-50',
    industry: 'Software',
    timeline: [
      { state: 'NEW', timestamp: '05/19/2026 · 09:02', actor: 'Discovery Run #42' },
      { state: 'VALIDATED', timestamp: '05/19/2026 · 09:14', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/21/2026 · 14:32', actor: 'AI Enrichment' },
    ],
  },
  {
    id: 'p2',
    businessName: 'CloudScale Inc',
    category: 'Cloud Services',
    city: 'Austin',
    state: 'TX',
    country: 'USA',
    status: 'READY_TO_SEND',
    hasEmail: true,
    rating: 3.9,
    reviewCount: 12,
    lastUpdated: '05/21/2026 · 11:00',
    address: '456 Congress Ave, Austin, TX 78701',
    phone: '+1 512 555 0200',
    website: null,
    contactName: 'Michael Rodriguez',
    contactEmail: 'm.rodriguez@cloudscale.com',
    contactTitle: 'VP of Sales',
    linkedinUrl: 'https://linkedin.com/in/mrodriguez',
    companySize: '51-200',
    industry: 'Cloud Infrastructure',
    timeline: [
      { state: 'NEW', timestamp: '05/18/2026 · 08:00', actor: 'Discovery Run #41' },
      { state: 'VALIDATED', timestamp: '05/18/2026 · 08:11', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/19/2026 · 10:24', actor: 'AI Enrichment' },
      { state: 'READY_TO_SEND', timestamp: '05/21/2026 · 11:00', actor: 'Draft Approved' },
    ],
  },
  {
    id: 'p3',
    businessName: 'DataPrime Analytics',
    category: 'Data Analytics',
    city: 'New York',
    state: 'NY',
    country: 'USA',
    status: 'NEEDS_REVIEW',
    hasEmail: false,
    rating: 4.8,
    reviewCount: 6,
    lastUpdated: '05/20/2026 · 09:15',
    address: '789 Broadway, New York, NY 10003',
    phone: '+1 212 555 0300',
    website: null,
    contactName: null,
    contactEmail: null,
    contactTitle: null,
    linkedinUrl: null,
    companySize: '11-50',
    industry: 'Analytics',
    timeline: [
      { state: 'NEW', timestamp: '05/20/2026 · 08:55', actor: 'Discovery Run #43' },
      { state: 'VALIDATED', timestamp: '05/20/2026 · 09:08', actor: 'Apollo Enrichment' },
      { state: 'NEEDS_REVIEW', timestamp: '05/20/2026 · 09:15', actor: 'Auto-flag: no email found' },
    ],
  },
  {
    id: 'p4',
    businessName: 'Recruit Pro Partners',
    category: 'Staffing & Recruiting',
    city: 'Chicago',
    state: 'IL',
    country: 'USA',
    status: 'SENT',
    hasEmail: true,
    rating: 4.5,
    reviewCount: 24,
    lastUpdated: '05/21/2026 · 08:42',
    address: '321 Michigan Ave, Chicago, IL 60601',
    phone: '+1 312 555 0400',
    website: null,
    contactName: 'Jennifer Lee',
    contactEmail: 'j.lee@recruitpro.com',
    contactTitle: 'Director of Recruiting',
    linkedinUrl: 'https://linkedin.com/in/jenniferlee',
    companySize: '51-200',
    industry: 'Staffing',
    timeline: [
      { state: 'NEW', timestamp: '05/17/2026 · 07:30', actor: 'Discovery Run #40' },
      { state: 'VALIDATED', timestamp: '05/17/2026 · 07:42', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/18/2026 · 11:20', actor: 'AI Enrichment' },
      { state: 'READY_TO_SEND', timestamp: '05/20/2026 · 16:08', actor: 'Draft Approved' },
      { state: 'SENT', timestamp: '05/21/2026 · 08:42', actor: 'Email Service' },
    ],
  },
  {
    id: 'p5',
    businessName: 'TalentBridge Solutions',
    category: 'HR Services',
    city: 'Seattle',
    state: 'WA',
    country: 'USA',
    status: 'FOLLOW_UP_F1',
    hasEmail: true,
    rating: 4.1,
    reviewCount: 17,
    lastUpdated: '05/20/2026 · 14:15',
    address: '654 Pine St, Seattle, WA 98101',
    phone: '+1 206 555 0500',
    website: null,
    contactName: 'David Park',
    contactEmail: 'd.park@talentbridge.io',
    contactTitle: 'CEO',
    linkedinUrl: 'https://linkedin.com/in/davidpark',
    companySize: '11-50',
    industry: 'HR Tech',
    timeline: [
      { state: 'NEW', timestamp: '05/12/2026 · 09:00', actor: 'Discovery Run #38' },
      { state: 'VALIDATED', timestamp: '05/12/2026 · 09:10', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/13/2026 · 10:00', actor: 'AI Enrichment' },
      { state: 'READY_TO_SEND', timestamp: '05/15/2026 · 11:20', actor: 'Draft Approved' },
      { state: 'SENT', timestamp: '05/16/2026 · 09:00', actor: 'Email Service' },
      { state: 'FOLLOW_UP_F1', timestamp: '05/20/2026 · 14:15', actor: 'Follow-up Cadence' },
    ],
  },
  {
    id: 'p6',
    businessName: 'Executive Search Group',
    category: 'Executive Search',
    city: 'Boston',
    state: 'MA',
    country: 'USA',
    status: 'REPLIED',
    hasEmail: true,
    rating: 4.7,
    reviewCount: 91,
    lastUpdated: '05/21/2026 · 16:48',
    address: '987 Boylston St, Boston, MA 02115',
    phone: '+1 617 555 0600',
    website: null,
    contactName: 'Amanda Foster',
    contactEmail: 'a.foster@execsearch.com',
    contactTitle: 'Managing Partner',
    linkedinUrl: 'https://linkedin.com/in/amandafoster',
    companySize: '11-50',
    industry: 'Executive Recruiting',
    timeline: [
      { state: 'NEW', timestamp: '05/14/2026 · 08:00', actor: 'Discovery Run #39' },
      { state: 'VALIDATED', timestamp: '05/14/2026 · 08:12', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/15/2026 · 09:30', actor: 'AI Enrichment' },
      { state: 'READY_TO_SEND', timestamp: '05/17/2026 · 10:00', actor: 'Draft Approved' },
      { state: 'SENT', timestamp: '05/18/2026 · 09:00', actor: 'Email Service' },
      { state: 'REPLIED', timestamp: '05/21/2026 · 16:48', actor: 'Inbound: "Interested in learning more"' },
    ],
  },
  {
    id: 'p7',
    businessName: 'HireNow Staffing',
    category: 'Temporary Staffing',
    city: 'Denver',
    state: 'CO',
    country: 'USA',
    status: 'VALIDATED',
    hasEmail: true,
    rating: 4.4,
    reviewCount: 29,
    lastUpdated: '05/21/2026 · 10:08',
    address: '159 Larimer St, Denver, CO 80202',
    phone: '+1 303 555 0700',
    website: null,
    contactName: 'Robert Kim',
    contactEmail: 'r.kim@hirenow.com',
    contactTitle: 'Operations Manager',
    linkedinUrl: 'https://linkedin.com/in/robertkim',
    companySize: '201-500',
    industry: 'Staffing',
    timeline: [
      { state: 'NEW', timestamp: '05/21/2026 · 09:55', actor: 'Discovery Run #44' },
      { state: 'VALIDATED', timestamp: '05/21/2026 · 10:08', actor: 'Apollo Enrichment' },
    ],
  },
  {
    id: 'p8',
    businessName: 'Career Builders LLC',
    category: 'Career Counseling',
    city: 'Miami',
    state: 'FL',
    country: 'USA',
    status: 'NEW',
    hasEmail: false,
    rating: 3.7,
    reviewCount: 8,
    lastUpdated: '05/21/2026 · 17:24',
    address: '753 Brickell Ave, Miami, FL 33131',
    phone: '+1 305 555 0800',
    website: null,
    contactName: null,
    contactEmail: null,
    contactTitle: null,
    linkedinUrl: null,
    companySize: '1-10',
    industry: 'Career Services',
    timeline: [
      { state: 'NEW', timestamp: '05/21/2026 · 17:24', actor: 'Discovery Run #45' },
    ],
  },
  {
    id: 'p9',
    businessName: 'IT Staffing Solutions',
    category: 'IT Staffing',
    city: 'Atlanta',
    state: 'GA',
    country: 'USA',
    status: 'DEAD_LETTER',
    hasEmail: true,
    rating: 3.4,
    reviewCount: 5,
    lastUpdated: '05/19/2026 · 14:00',
    address: '852 Peachtree St, Atlanta, GA 30308',
    phone: '+1 404 555 0900',
    website: null,
    contactName: 'Lisa Martinez',
    contactEmail: 'l.martinez@itstaffing.net',
    contactTitle: 'HR Director',
    linkedinUrl: null,
    companySize: '11-50',
    industry: 'IT Services',
    timeline: [
      { state: 'NEW', timestamp: '05/13/2026 · 08:00', actor: 'Discovery Run #37' },
      { state: 'VALIDATED', timestamp: '05/13/2026 · 08:14', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/14/2026 · 09:30', actor: 'AI Enrichment' },
      { state: 'SENT', timestamp: '05/16/2026 · 09:00', actor: 'Email Service' },
      { state: 'DEAD_LETTER', timestamp: '05/19/2026 · 14:00', actor: 'Bounce: mailbox unavailable' },
    ],
  },
  {
    id: 'p10',
    businessName: 'Professional Recruiters Inc',
    category: 'Professional Staffing',
    city: 'Portland',
    state: 'OR',
    country: 'USA',
    status: 'ENRICHED',
    hasEmail: true,
    rating: 4.0,
    reviewCount: 22,
    lastUpdated: '05/20/2026 · 19:50',
    address: '456 SW Broadway, Portland, OR 97205',
    phone: '+1 503 555 1000',
    website: null,
    contactName: 'Kevin Wu',
    contactEmail: 'k.wu@prorecruiters.com',
    contactTitle: 'VP of Business Development',
    linkedinUrl: 'https://linkedin.com/in/kevinwu',
    companySize: '51-200',
    industry: 'Professional Services',
    timeline: [
      { state: 'NEW', timestamp: '05/19/2026 · 18:00', actor: 'Discovery Run #42' },
      { state: 'VALIDATED', timestamp: '05/19/2026 · 18:12', actor: 'Apollo Enrichment' },
      { state: 'ENRICHED', timestamp: '05/20/2026 · 19:50', actor: 'AI Enrichment' },
    ],
  },
];

export const mockCompanies: Company[] = [
  {
    id: 'c1',
    name: 'TechStart Solutions',
    domain: 'techstart.io',
    industry: 'Software Development',
    size: '11-50',
    location: 'San Francisco, CA',
    prospectCount: 3,
    lastUpdated: '05/21/2026 · 14:32',
  },
  {
    id: 'c2',
    name: 'CloudScale Inc',
    domain: 'cloudscale.com',
    industry: 'Cloud Services',
    size: '51-200',
    location: 'Austin, TX',
    prospectCount: 2,
    lastUpdated: '05/21/2026 · 11:00',
  },
  {
    id: 'c3',
    name: 'Recruit Pro Partners',
    domain: 'recruitpro.com',
    industry: 'Staffing & Recruiting',
    size: '51-200',
    location: 'Chicago, IL',
    prospectCount: 5,
    lastUpdated: '05/21/2026 · 08:42',
  },
];

export const mockICPConfigs: ICPConfig[] = [
  {
    id: 'icp1',
    name: 'Recruitment Agencies - Mid Market',
    description: 'Staffing and recruitment agencies with 50-200 employees targeting C-suite decision makers',
    criteria: {
      industries: ['Staffing & Recruiting', 'Executive Search', 'HR Services'],
      titles: ['CEO', 'VP of Sales', 'Director of Recruiting', 'Managing Partner'],
      companySize: ['51-200', '201-500'],
      locations: ['United States'],
      keywords: ['staffing', 'recruitment', 'executive search', 'talent acquisition'],
    },
    status: 'ACTIVE',
    createdAt: '05/15/2026 · 10:00',
    updatedAt: '05/20/2026 · 14:30',
  },
  {
    id: 'icp2',
    name: 'Tech Startups - Early Stage',
    description: 'Software and tech startups in seed/series A stage',
    criteria: {
      industries: ['Software Development', 'SaaS', 'Cloud Services'],
      titles: ['CEO', 'CTO', 'Head of Engineering'],
      companySize: ['1-10', '11-50'],
      locations: ['United States', 'Canada'],
      keywords: ['startup', 'software', 'saas', 'cloud'],
    },
    status: 'ACTIVE',
    createdAt: '05/10/2026 · 09:00',
    updatedAt: '05/18/2026 · 11:15',
  },
];

export const mockRuns: Run[] = [
  {
    id: 'run1',
    icpId: 'icp1',
    status: 'COMPLETED',
    startedAt: '05/21/2026 · 08:00',
    completedAt: '05/21/2026 · 17:24',
    prospectsFound: 342,
    prospectsEnriched: 256,
    budget: 100,
    spent: 42.50,
    progress: 100,
  },
  {
    id: 'run2',
    icpId: 'icp2',
    status: 'RUNNING',
    startedAt: '05/22/2026 · 09:00',
    completedAt: null,
    prospectsFound: 156,
    prospectsEnriched: 98,
    budget: 75,
    spent: 28.30,
    progress: 63,
  },
  {
    id: 'run3',
    icpId: 'icp1',
    status: 'COMPLETED',
    startedAt: '05/20/2026 · 08:00',
    completedAt: '05/20/2026 · 16:45',
    prospectsFound: 188,
    prospectsEnriched: 142,
    budget: 80,
    spent: 35.20,
    progress: 100,
  },
];

export const mockDashboardMetrics: DashboardMetrics = {
  totalProspects: 342,
  activeRuns: 1,
  emailsSentToday: 12,
  replyRate: 8.5,
  weeklyTrend: [45, 62, 58, 71, 68, 74, 82],
};

export const mockIntegrations: Integration[] = [
  {
    id: 'int1',
    name: 'Apollo.io',
    type: 'ENRICHMENT',
    status: 'CONNECTED',
    lastSync: '05/22/2026 · 09:30',
    config: { apiKey: 'apol_*********************' },
  },
  {
    id: 'int2',
    name: 'Microsoft 365',
    type: 'EMAIL',
    status: 'CONNECTED',
    lastSync: '05/22/2026 · 08:15',
    config: { tenantId: 'tenant_***' },
  },
  {
    id: 'int3',
    name: 'Apify',
    type: 'DISCOVERY',
    status: 'CONNECTED',
    lastSync: '05/21/2026 · 17:24',
    config: { apiKey: 'apify_*********************' },
  },
  {
    id: 'int4',
    name: 'HubSpot CRM',
    type: 'CRM',
    status: 'DISCONNECTED',
    lastSync: null,
    config: {},
  },
];

export const mockSettings: Settings = {
  apiKeys: {
    apollo: 'apol_*********************',
    apify: 'apify_*********************',
    openai: 'sk-*********************',
  },
  safetyCaps: {
    dailyEmailLimit: 30,
    budgetLimit: 100,
    enrichmentConcurrency: 5,
  },
  behavior: {
    autoEnrich: true,
    autoGenerateDrafts: false,
    requireApproval: true,
  },
  senderIdentity: {
    name: 'Sarah Thompson',
    email: 'sarah@recruitr.io',
    signature: 'Best regards,\nSarah Thompson\nRecruitr',
  },
};

// API simulation functions with loading states
export async function fetchProspects(filters?: { status?: string }): Promise<Prospect[]> {
  await delay(400 + Math.random() * 300);
  
  if (filters?.status && filters.status !== 'all') {
    return mockProspects.filter(p => p.status === filters.status);
  }
  
  return mockProspects;
}

export async function fetchCompanies(): Promise<Company[]> {
  await delay(350 + Math.random() * 250);
  return mockCompanies;
}

export async function fetchICPConfigs(): Promise<ICPConfig[]> {
  await delay(300 + Math.random() * 200);
  return mockICPConfigs;
}

export async function fetchRuns(): Promise<Run[]> {
  await delay(320 + Math.random() * 220);
  return mockRuns;
}

export async function fetchRunDetail(id: string): Promise<RunDetail | null> {
  await delay(450 + Math.random() * 350);
  
  const run = mockRuns.find(r => r.id === id);
  if (!run) return null;
  
  const config = mockICPConfigs.find(c => c.id === run.icpId)!;
  
  return {
    ...run,
    config,
    logs: [
      { id: 'log1', timestamp: '05/21/2026 · 08:00', level: 'INFO', message: 'Run started' },
      { id: 'log2', timestamp: '05/21/2026 · 08:15', level: 'INFO', message: 'Discovery phase: 156 prospects found' },
      { id: 'log3', timestamp: '05/21/2026 · 09:30', level: 'INFO', message: 'Enrichment phase: 98 prospects enriched' },
      { id: 'log4', timestamp: '05/21/2026 · 10:45', level: 'WARN', message: '12 prospects flagged for review' },
      { id: 'log5', timestamp: '05/21/2026 · 17:24', level: 'INFO', message: 'Run completed successfully' },
    ],
    metrics: {
      totalSearched: 1000,
      newProspects: 342,
      duplicates: 124,
      enriched: 256,
      errors: 8,
      avgEnrichmentTime: 2.4,
    },
  };
}

export async function fetchDashboardMetrics(): Promise<DashboardMetrics> {
  await delay(280 + Math.random() * 180);
  return mockDashboardMetrics;
}

export async function fetchIntegrations(): Promise<Integration[]> {
  await delay(310 + Math.random() * 190);
  return mockIntegrations;
}

export async function fetchSettings(): Promise<Settings> {
  await delay(290 + Math.random() * 170);
  return mockSettings;
}
