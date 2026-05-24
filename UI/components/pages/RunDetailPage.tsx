'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import { fetchRun, type Run } from '@/lib/api';

interface RunDetailPageProps {
  runId: string;
}

function StatCard({
  label, value, color = 'var(--fg-primary)', bg = 'var(--bg-app)',
}: { label: string; value: React.ReactNode; color?: string; bg?: string }) {
  return (
    <div style={{ padding: 20, background: bg, border: '1px solid var(--border-card)', borderRadius: 10 }}>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 500 }}>
        {label}
      </div>
      <div style={{ fontSize: 32, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: Run['status'] }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    active:    { bg: '#ECFDF5', color: '#059669', label: 'Active' },
    completed: { bg: '#EFF6FF', color: '#2563EB', label: 'Completed' },
    paused:    { bg: '#FFFBEB', color: '#D97706', label: 'Paused' },
    cancelled: { bg: '#FEF2F2', color: '#DC2626', label: 'Cancelled' },
  };
  const cfg = map[status] ?? { bg: '#F3F4F6', color: '#6B7280', label: status };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 9999, fontSize: 12, fontWeight: 600, background: cfg.bg, color: cfg.color }}>
      {status === 'active' && <span style={{ width: 6, height: 6, borderRadius: 9999, background: cfg.color, animation: 'pulse 2s infinite' }} />}
      {cfg.label}
    </span>
  );
}

// Back link used as the TopBar title slot
function BackLink() {
  return (
    <Link
      href="/runs"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', textDecoration: 'none' }}
    >
      <Icon name="arrow-left" size={16} />
      Back to Runs
    </Link>
  );
}

export function RunDetailPage({ runId }: RunDetailPageProps) {
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRun(await fetchRun(runId));
    } catch (e: any) {
      setError(e.message || 'Failed to load run');
    }
  }, [runId]);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  // Auto-refresh every 6s while active
  useEffect(() => {
    if (!run || run.status !== 'active') return;
    const id = setInterval(load, 6000);
    return () => clearInterval(id);
  }, [run, load]);

  if (loading) {
    return (
      <>
        <TopBar titleNode={<BackLink />} showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)' }}>
          <div style={{ textAlign: 'center' }}>
            <Icon name="loader" size={24} />
            <div style={{ marginTop: 12, fontSize: 14 }}>Loading run...</div>
          </div>
        </div>
        <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
      </>
    );
  }

  if (error || !run) {
    return (
      <>
        <TopBar titleNode={<BackLink />} showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ textAlign: 'center', color: 'var(--fg-muted)' }}>
            <div style={{ marginBottom: 12 }}>{error || 'Run not found'}</div>
          </div>
        </div>
      </>
    );
  }

  const stats = run.stats;
  const mode = run.source || 'jobspy';
  const modeDisplay = mode === 'mixed' ? 'LinkedIn + Naukri' : mode === 'naukri' ? 'Naukri' : 'LinkedIn';

  const fmtDate = (d: string | null) => {
    if (!d) return '—';
    return new Date(d).toLocaleString('en-CA', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  return (
    <>
      {/* Back button in title slot — no actions in topbar */}
      <TopBar titleNode={<BackLink />} showSearch={false} />

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        <div style={{ maxWidth: 960, margin: '0 auto' }}>

          {/* Page header: title + status badge */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
                <h1 style={{ fontSize: 24, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>{run.title}</h1>
                <StatusBadge status={run.status} />
              </div>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)' }}>
                ID: {run.id || run._id}
              </div>
            </div>
          </div>

          {/* Info cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, marginBottom: 24 }}>
            {/* Scraper mode */}
            <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Scraper Mode</span>
                <Icon name="layers" size={16} style={{ color: 'var(--status-info)' }} />
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--fg-primary)', textTransform: 'capitalize' }}>{modeDisplay}</div>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 6 }}>
                {mode === 'mixed' ? 'JobSpy + Naukri Scraper' : mode === 'naukri' ? 'Naukri Scraper' : 'JobSpy Library'}
              </div>
            </div>

            {/* Timeline */}
            <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Timeline</span>
                <Icon name="calendar" size={16} style={{ color: 'var(--status-info)' }} />
              </div>
              <div style={{ fontSize: 12, color: 'var(--fg-secondary)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span><span style={{ fontWeight: 600 }}>Start:</span> {fmtDate(run.runStartedAt)}</span>
                <span><span style={{ fontWeight: 600 }}>End:</span> {fmtDate(run.runEndedAt)}</span>
              </div>
            </div>

            {/* Parameters */}
            <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Parameters</span>
                <Icon name="settings" size={16} style={{ color: 'var(--status-info)' }} />
              </div>
              <div style={{ fontSize: 12, color: 'var(--fg-secondary)', display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div><span style={{ fontWeight: 600 }}>Titles:</span> {run.runConfig?.searchTitles?.join(', ') || '—'}</div>
                <div><span style={{ fontWeight: 600 }}>Locations:</span> {run.runConfig?.searchLocations?.join(', ') || '—'}</div>
                <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
                  Limit: {run.runConfig?.resultsPerSearch ?? 0} jobs/search
                </div>
              </div>
            </div>
          </div>

          {/* Execution stats */}
          <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20, marginBottom: 20 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="sparkles" size={16} style={{ color: 'var(--status-info)' }} />
              Execution Stats
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
              <StatCard label="Scraped" value={stats.totalJobsScraped.toLocaleString()} />
              <StatCard label="Accepted" value={(stats.acceptedJobs ?? 0).toLocaleString()} color="var(--status-success)" bg="#ECFDF5" />
              <StatCard label="Rejected" value={(stats.rejectedJobs ?? 0).toLocaleString()} color="var(--status-danger)" bg="#FEF2F2" />
              <StatCard label="Inserted" value={(stats.inserted ?? 0).toLocaleString()} color="var(--status-info)" bg="#EFF6FF" />
              <StatCard label="Duplicates" value={(stats.duplicates ?? 0).toLocaleString()} color="var(--status-warning)" bg="#FFFBEB" />
            </div>
          </div>

          {/* View Results — below stats */}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Link href={`/runs/${runId}/results`} style={{ textDecoration: 'none' }}>
              <button style={{
                height: 38, padding: '0 20px', borderRadius: 8, fontSize: 14, fontWeight: 600,
                cursor: 'pointer', border: 'none', background: 'var(--fg-primary)', color: '#FFF',
                fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8,
              }}>
                View Results <Icon name="chevron-right" size={16} />
              </button>
            </Link>
          </div>

        </div>
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
    </>
  );
}
