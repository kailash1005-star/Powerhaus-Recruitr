'use client';

import { useState } from 'react';
import { Icon } from './Icon';
import type { PipelineJob, SearchAttemptEntry, BroadeningStepEntry } from '@/lib/api';

interface SearchQueryDetailsTableProps {
  jobEntry?: PipelineJob | null;
}

const EXPERIENCE_MAP: Record<string, string> = {
  '1': '< 1 year (Entry)',
  '2': '1–2 years',
  '3': '3–5 years',
  '4': '6–10 years',
  '5': '> 10 years (Senior/Exec)',
};

const SENIORITY_MAP: Record<string, string> = {
  '100': 'In Training / Intern',
  '110': 'Entry Level',
  '120': 'Senior',
  '130': 'Strategic / Principal',
  '200': 'Lead / Entry Manager',
  '210': 'Experienced Manager',
  '220': 'Director',
  '300': 'VP',
  '310': 'C-Suite / CXO',
  '320': 'Partner / Owner',
};

export function SearchQueryDetailsTable({ jobEntry }: SearchQueryDetailsTableProps) {
  const [isOpen, setIsOpen] = useState(false);

  if (!jobEntry) return null;

  const attempts: SearchAttemptEntry[] = jobEntry.searchAttempts || [];
  const initialFilters = (jobEntry.lastDiscoverFilters || jobEntry.lastApolloFilters || {}) as Record<string, any>;

  // Build rows to render in tabular format — only executed search queries
  const rows: Array<{
    typeLabel: string;
    badgeColor: { bg: string; text: string; border: string };
    action: string;
    reasoning?: string;
    searchQuery: string;
    jobTitles: string[];
    locations: string[];
    experience?: string;
    seniority?: string;
    exclusions?: string[];
    resultCount?: number | null;
    channelCounts?: Record<string, number> | null;
    at?: string;
    searchCoverage?: PipelineJob['searchCoverage'];
  }> = [];

  // Case 1: We have recorded search attempts in DB (executed searches)
  if (attempts.length > 0) {
    attempts.forEach((a, idx) => {
      const f = (a.filters || {}) as Record<string, any>;
      const sq = (f.searchQuery || f.query || initialFilters.searchQuery || '—') as string;
      const titles = (f.currentJobTitles || f.titles || initialFilters.currentJobTitles || []) as string[];
      const locs = (f.locations || initialFilters.locations || []) as string[];
      const exp = f.yearsOfExperience ? EXPERIENCE_MAP[String(f.yearsOfExperience)] || String(f.yearsOfExperience) : undefined;
      const sen = f.seniorityLevel ? SENIORITY_MAP[String(f.seniorityLevel)] || String(f.seniorityLevel) : undefined;
      
      const exTitles = (f.excludeCurrentJobTitles || []) as string[];
      const exComp = (f.excludeCurrentCompanies || []) as string[];
      const exclusions = [...exTitles.map(t => `Exclude title: ${t}`), ...exComp.map(c => `Exclude comp: ${c}`)];

      rows.push({
        typeLabel: idx === 0 ? 'Attempt #1 (Initial Query)' : `Attempt #${a.attempt || idx + 1} (Fallback)`,
        badgeColor: idx === 0 
          ? { bg: '#EEF2FF', text: '#4338CA', border: '#C7D2FE' } 
          : { bg: '#FEF3C7', text: '#B45309', border: '#FDE68A' },
        action: a.action || (idx === 0 ? 'Initial Search' : 'Broadened Fallback Search'),
        reasoning: a.reasoning,
        searchQuery: sq,
        jobTitles: titles,
        locations: locs,
        experience: exp,
        seniority: sen,
        exclusions,
        resultCount: a.resultCount,
        channelCounts: a.channelCounts,
        at: a.at,
        // Coverage is a job-level snapshot of the CURRENT state, not
        // per-attempt history — only worth showing next to the latest attempt.
        searchCoverage: idx === attempts.length - 1 ? jobEntry.searchCoverage : null,
      });
    });
  } else if (Object.keys(initialFilters).length > 0) {
    // Case 2: Executed initial query filters from DB
    const sq = (initialFilters.searchQuery || initialFilters.query || '—') as string;
    const titles = (initialFilters.currentJobTitles || initialFilters.titles || []) as string[];
    const locs = (initialFilters.locations || []) as string[];
    const exp = initialFilters.yearsOfExperience ? EXPERIENCE_MAP[String(initialFilters.yearsOfExperience)] || String(initialFilters.yearsOfExperience) : undefined;
    const sen = initialFilters.seniorityLevel ? SENIORITY_MAP[String(initialFilters.seniorityLevel)] || String(initialFilters.seniorityLevel) : undefined;

    const exTitles = (initialFilters.excludeCurrentJobTitles || []) as string[];
    const exComp = (initialFilters.excludeCurrentCompanies || []) as string[];
    const exclusions = [...exTitles.map(t => `Exclude title: ${t}`), ...exComp.map(c => `Exclude comp: ${c}`)];

    rows.push({
      typeLabel: 'Initial Query',
      badgeColor: { bg: '#EEF2FF', text: '#4338CA', border: '#C7D2FE' },
      action: 'Initial Search Query',
      reasoning: 'Primary search query executed for candidate discovery.',
      searchQuery: sq,
      jobTitles: titles,
      locations: locs,
      experience: exp,
      seniority: sen,
      exclusions,
      resultCount: jobEntry.candidateCount,
      searchCoverage: jobEntry.searchCoverage,
    });
  }

  if (rows.length === 0) return null;

  return (
    <div style={{
      margin: '12px 24px',
      borderRadius: 10,
      border: '1px solid #E2E8F0',
      background: '#FFFFFF',
      boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
      overflow: 'hidden',
      fontFamily: 'inherit',
    }}>
      {/* Header Bar */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 18px',
          background: '#F8FAFC',
          border: 'none',
          borderBottom: isOpen ? '1px solid #E2E8F0' : 'none',
          cursor: 'pointer',
          textAlign: 'left',
          transition: 'background 120ms',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{
            width: 28, height: 28, borderRadius: 6, background: '#EEF2FF',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            color: '#4F46E5', flexShrink: 0,
          }}>
            <Icon name="search" size={14} />
          </span>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 700, color: '#1E293B' }}>
              Search Strategy & Input Queries
            </div>
            <div style={{ fontSize: 11.5, color: '#64748B', marginTop: 1 }}>
              {rows.length} query configuration(s) • Engine: {jobEntry.lastApolloFilters ? 'Apollo & LinkedIn' : 'LinkedIn (Apify)'}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            fontSize: 12, fontWeight: 600, color: '#475569',
            display: 'inline-flex', alignItems: 'center', gap: 4,
          }}>
            {isOpen ? 'Hide Query Details' : 'Show Query Details'}
            <Icon name={isOpen ? 'chevron-up' : 'chevron-down'} size={15} />
          </span>
        </div>
      </button>

      {/* Collapsible Content */}
      {isOpen && (
        <div style={{ padding: 16, overflowX: 'auto', maxHeight: 320, overflowY: 'auto' }}>
          <table style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12.5,
            color: '#334155',
          }}>
            <thead>
              <tr style={{ background: '#F1F5F9', borderBottom: '1px solid #CBD5E1' }}>
                <th style={thStyle}>Stage & Action</th>
                <th style={thStyle}>Input Search Query (`searchQuery`)</th>
                <th style={thStyle}>Target Job Titles</th>
                <th style={thStyle}>Locations & Filters</th>
                <th style={thStyle}>Yield / Results</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={idx} style={{
                  borderBottom: idx === rows.length - 1 ? 'none' : '1px solid #E2E8F0',
                  verticalAlign: 'top',
                }}>
                  {/* Stage & Action */}
                  <td style={tdStyle}>
                    <div style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700, background: row.badgeColor.bg, color: row.badgeColor.text, border: `1px solid ${row.badgeColor.border}`, marginBottom: 6 }}>
                      {row.typeLabel}
                    </div>
                    <div style={{ fontWeight: 600, color: '#0F172A', marginBottom: 2 }}>{row.action}</div>
                    {row.reasoning && (
                      <div style={{ fontSize: 11, color: '#64748B', lineHeight: 1.4, maxWidth: 220 }}>
                        {row.reasoning}
                      </div>
                    )}
                  </td>

                  {/* Input Search Query */}
                  <td style={tdStyle}>
                    <div style={{
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                      fontSize: 11.5,
                      lineHeight: 1.5,
                      background: '#F8FAFC',
                      border: '1px solid #CBD5E1',
                      borderRadius: 6,
                      padding: '8px 10px',
                      color: '#090D16',
                      wordBreak: 'break-word',
                      maxWidth: 380,
                    }}>
                      {row.searchQuery}
                    </div>
                  </td>

                  {/* Target Job Titles */}
                  <td style={tdStyle}>
                    {row.jobTitles && row.jobTitles.length > 0 ? (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, maxWidth: 240 }}>
                        {row.jobTitles.map((t, i) => (
                          <span key={i} style={{
                            fontSize: 11, padding: '2px 7px', borderRadius: 4,
                            background: '#F1F5F9', color: '#334155', border: '1px solid #E2E8F0',
                          }}>
                            {t}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span style={{ color: '#94A3B8' }}>—</span>
                    )}
                  </td>

                  {/* Locations & Filters */}
                  <td style={tdStyle}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 220 }}>
                      {row.locations && row.locations.length > 0 && (
                        <div style={{ fontSize: 11.5 }}>
                          <span style={{ color: '#64748B', fontWeight: 600 }}>Location: </span>
                          <span style={{ color: '#0F172A' }}>{row.locations.join(', ')}</span>
                        </div>
                      )}
                      {row.experience && (
                        <div style={{ fontSize: 11.5 }}>
                          <span style={{ color: '#64748B', fontWeight: 600 }}>Experience: </span>
                          <span style={{ color: '#0F172A' }}>{row.experience}</span>
                        </div>
                      )}
                      {row.seniority && (
                        <div style={{ fontSize: 11.5 }}>
                          <span style={{ color: '#64748B', fontWeight: 600 }}>Seniority: </span>
                          <span style={{ color: '#0F172A' }}>{row.seniority}</span>
                        </div>
                      )}
                      {row.exclusions && row.exclusions.length > 0 && (
                        <div style={{ fontSize: 11, color: '#DC2626' }}>
                          {row.exclusions.join(' • ')}
                        </div>
                      )}
                    </div>
                  </td>

                  {/* Yield / Results */}
                  <td style={tdStyle}>
                    {row.resultCount != null ? (
                      <div>
                        <span style={{
                          fontWeight: 700, fontSize: 13,
                          color: row.resultCount > 0 ? '#166534' : '#991B1B',
                        }}>
                          {row.resultCount} candidate(s)
                        </span>
                        {row.channelCounts && (
                          <div style={{ fontSize: 11, color: '#64748B', marginTop: 2 }}>
                            {Object.entries(row.channelCounts).map(([ch, cnt]) => `${ch}: ${cnt}`).join(' • ')}
                          </div>
                        )}
                        {/* Honest coverage note for the title channel — only
                            claims a real total when every page that exists
                            was actually checked, never a sample presented as
                            complete. */}
                        {row.searchCoverage && (
                          <div style={{
                            fontSize: 11, marginTop: 3, fontWeight: 600,
                            color: row.searchCoverage.fullyCovered ? '#166534' : '#92400E',
                          }}>
                            {row.searchCoverage.fullyCovered
                              ? `✓ ${row.searchCoverage.totalElements} total for the title search — every match checked`
                              : `Checked ${row.searchCoverage.pagesFetched} of ${row.searchCoverage.totalPages} pages `
                                + `(${row.searchCoverage.totalElements} total matches) for the title search`}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span style={{ fontSize: 11, color: '#64748B', fontStyle: 'italic' }}>Planned Fallback</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '10px 12px',
  fontSize: 11.5,
  fontWeight: 700,
  color: '#475569',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};

const tdStyle: React.CSSProperties = {
  padding: '12px',
  verticalAlign: 'top',
};
