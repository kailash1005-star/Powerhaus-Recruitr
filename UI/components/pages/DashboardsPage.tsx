'use client';

import { useState, useEffect, useCallback } from 'react';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { fetchJobsAnalytics, fetchCompaniesAnalytics, type JobsAnalytics, type CompaniesAnalytics } from '@/lib/api';

const TIME_RANGES: { label: string; days: number }[] = [
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 14 days', days: 14 },
  { label: 'Last 30 days', days: 30 },
  { label: 'All time', days: 0 },
];

function StatCard({
  label, value, sub, color = 'var(--fg-primary)', icon,
}: { label: string; value: React.ReactNode; sub?: string; color?: string; icon: string }) {
  return (
    <div style={{ padding: 20, background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {label}
        </div>
        <Icon name={icon} size={16} style={{ color: 'var(--fg-muted)' }} />
      </div>
      <div style={{ fontSize: 36, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

export function DashboardsPage() {
  const [jobsData, setJobsData] = useState<JobsAnalytics | null>(null);
  const [companiesData, setCompaniesData] = useState<CompaniesAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRange, setSelectedRange] = useState(TIME_RANGES[0]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [jobs, companies] = await Promise.all([
        fetchJobsAnalytics(selectedRange.days || 365),
        fetchCompaniesAnalytics(),
      ]);
      setJobsData(jobs);
      setCompaniesData(companies);
    } catch (e: any) {
      setError(e.message || 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  }, [selectedRange.days]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const maxTrend = jobsData ? Math.max(...jobsData.dailyTrend.map((d) => d.total), 1) : 1;

  return (
    <>
      <TopBar title="Dashboards" showSearch={false} />

      {/* Inline time-range segmented control */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
      }}>
        <div style={{ display: 'inline-flex', borderRadius: 6, border: '1px solid var(--border-card)', overflow: 'hidden' }}>
          {TIME_RANGES.map((range) => (
            <button
              key={range.label}
              onClick={() => setSelectedRange(range)}
              style={{
                padding: '5px 12px', fontSize: 13, fontWeight: 500, cursor: 'pointer',
                border: 'none', borderRight: '1px solid var(--border-card)',
                background: selectedRange.label === range.label ? 'var(--fg-primary)' : 'var(--bg-app)',
                color: selectedRange.label === range.label ? '#FFF' : 'var(--fg-secondary)',
                transition: 'all 120ms', fontFamily: 'inherit',
              }}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--fg-muted)' }}>
            <div style={{ textAlign: 'center' }}>
              <Icon name="loader" size={24} />
              <div style={{ marginTop: 12, fontSize: 14 }}>Loading analytics...</div>
            </div>
          </div>
        ) : error ? (
          <div style={{ padding: '20px 24px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        ) : (
          <>
            {/* Stats grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
              <StatCard
                label="Total Jobs"
                value={(jobsData?.summary.total ?? 0).toLocaleString()}
                sub={`${selectedRange.label}`}
                icon="briefcase"
              />
              <StatCard
                label="Accepted Jobs"
                value={(jobsData?.summary.accepted ?? 0).toLocaleString()}
                color="var(--status-success)"
                sub={`${(jobsData?.summary.acceptanceRate ?? 0).toFixed(1)}% acceptance rate`}
                icon="check-circle"
              />
              <StatCard
                label="Companies Analyzed"
                value={(companiesData?.summary.total ?? 0).toLocaleString()}
                icon="building-2"
              />
              <StatCard
                label="Companies Accepted"
                value={(companiesData?.summary.accepted ?? 0).toLocaleString()}
                color="var(--status-info)"
                sub={`${(companiesData?.summary.acceptanceRate ?? 0).toFixed(1)}% acceptance rate`}
                icon="trending-up"
              />
            </div>

            {/* Daily trend chart */}
            {jobsData && jobsData.dailyTrend.length > 0 && (
              <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20, marginBottom: 24 }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Icon name="bar-chart-2" size={16} style={{ color: 'var(--status-info)' }} />
                  Daily Job Trend
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, height: 160 }}>
                  {jobsData.dailyTrend.map((day) => (
                    <div key={day.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 0 }}>
                      <div style={{
                        width: '100%', borderRadius: 4,
                        height: `${Math.max((day.total / maxTrend) * 130, 2)}px`,
                        background: 'var(--status-info)', transition: 'height 300ms',
                      }} title={`${day.date}: ${day.total} jobs`} />
                      <div style={{ fontSize: 10, color: 'var(--fg-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', width: '100%', textAlign: 'center' }}>
                        {new Date(day.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Bottom row: by source + rejection reasons */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {/* Jobs by board */}
              {jobsData && jobsData.byBoard.length > 0 && (
                <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Icon name="layers" size={16} style={{ color: 'var(--status-info)' }} />
                    Jobs by Source
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {jobsData.byBoard.map((b) => (
                      <div key={b.board} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{ width: 90, fontSize: 12, fontWeight: 500, color: 'var(--fg-secondary)', textTransform: 'capitalize', flexShrink: 0 }}>
                          {b.board}
                        </div>
                        <div style={{ flex: 1, height: 8, borderRadius: 4, background: '#F3F4F6', overflow: 'hidden' }}>
                          <div style={{
                            height: '100%', borderRadius: 4, background: 'var(--status-info)',
                            width: `${Math.round((b.count / (jobsData.summary.total || 1)) * 100)}%`,
                            transition: 'width 400ms',
                          }} />
                        </div>
                        <div style={{ width: 40, textAlign: 'right', fontSize: 12, fontWeight: 700, color: 'var(--fg-primary)', flexShrink: 0 }}>
                          {b.count}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Rejection reasons */}
              {jobsData && jobsData.byRejectionReason.length > 0 && (
                <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Icon name="x-circle" size={16} style={{ color: 'var(--status-danger)' }} />
                    Top Rejection Reasons
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {jobsData.byRejectionReason.slice(0, 6).map((r) => (
                      <div key={r.reason} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{ flex: 1, fontSize: 12, fontWeight: 500, color: 'var(--fg-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {r.reason || 'Unknown'}
                        </div>
                        <div style={{ width: 36, textAlign: 'right', fontSize: 12, fontWeight: 700, color: 'var(--status-danger)', flexShrink: 0 }}>
                          {r.count}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
