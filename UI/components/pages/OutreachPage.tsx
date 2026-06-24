'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  fetchOutreach, fetchOutreachMetrics, fetchOutreachConfig,
  type OutreachRow, type OutreachStatus, type OutreachMetrics,
  type OutreachConfig, type OutreachAudience,
} from '@/lib/api';

/**
 * Outreach CRM — live data from the outreach engine.
 *
 * Two subsections (tabs): Leads (HR decision-makers) and Candidates. Each row's
 * lifecycle is fed by Smartlead webhooks (sent → opened → replied → bounced /
 * unsubscribed) and Cal.com (meeting). Per the deliverability decision, Replied
 * and Meetings are the headline signals; Opens are shown DIMMED because Apple
 * Mail Privacy inflates them.
 */

const STATUS: Record<
  OutreachStatus,
  { label: string; dot: string; bg: string; fg: string; dimmed?: boolean }
> = {
  sent:         { label: 'Sent',           dot: '#9CA3AF', bg: '#F3F4F6', fg: '#4B5563' },
  delivered:    { label: 'Delivered',      dot: '#9CA3AF', bg: '#F3F4F6', fg: '#4B5563' },
  opened:       { label: 'Opened',         dot: '#93C5FD', bg: '#F1F5F9', fg: '#64748B', dimmed: true },
  clicked:      { label: 'Clicked',        dot: '#93C5FD', bg: '#F1F5F9', fg: '#64748B', dimmed: true },
  replied:      { label: 'Replied',        dot: '#10B981', bg: '#ECFDF5', fg: '#047857' },
  meeting:      { label: 'Meeting booked', dot: '#8B5CF6', bg: '#F5F3FF', fg: '#6D28D9' },
  bounced:      { label: 'Bounced',        dot: '#F59E0B', bg: '#FFFBEB', fg: '#B45309' },
  unsubscribed: { label: 'Unsubscribed',   dot: '#EF4444', bg: '#FEF2F2', fg: '#B91C1C' },
};

const FILTER_KEYS: (OutreachStatus | 'all')[] = ['all', 'sent', 'opened', 'replied', 'meeting', 'unsubscribed', 'bounced'];

type TabKey = OutreachAudience;
const TABS: { key: TabKey; label: string; icon: string; sub: string }[] = [
  { key: 'leads',      label: 'Leads',      icon: 'building-2', sub: 'HR decision-makers contacted' },
  { key: 'candidates', label: 'Candidates', icon: 'users',      sub: 'Talent contacted' },
];

function relTime(iso?: string | null): string {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function StatusPill({ status }: { status: OutreachStatus }) {
  const s = STATUS[status] ?? STATUS.sent;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, height: 24,
      padding: '0 9px', borderRadius: 9999, fontSize: 12, fontWeight: 600,
      background: s.bg, color: s.fg, opacity: s.dimmed ? 0.85 : 1,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 9999, background: s.dot, flexShrink: 0 }} />
      {s.label}
    </span>
  );
}

function Avatar({ name }: { name: string }) {
  const initials = name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase();
  return (
    <span style={{
      width: 32, height: 32, borderRadius: 9999, flexShrink: 0,
      background: 'var(--primary)', color: '#FFF',
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      fontSize: 12, fontWeight: 700,
    }}>
      {initials}
    </span>
  );
}

function FunnelTile({
  label, value, total, color, icon, dimmed, note,
}: { label: string; value: number; total: number; color: string; icon: string; dimmed?: boolean; note?: string }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div style={{
      padding: 16, background: '#FFF', borderRadius: 10,
      border: '1px solid var(--border-card)', opacity: dimmed ? 0.7 : 1,
    }} title={note}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {label}{dimmed && <Icon name="info" size={11} style={{ color: 'var(--fg-subtle)' }} />}
        </span>
        <Icon name={icon} size={15} style={{ color }} />
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 28, fontWeight: 700, color: 'var(--fg-primary)', lineHeight: 1 }}>{value}</span>
        <span style={{ fontSize: 12, fontWeight: 600, color }}>{pct}%</span>
      </div>
      <div style={{ marginTop: 10, height: 5, borderRadius: 4, background: '#F3F4F6', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 4, transition: 'width 400ms' }} />
      </div>
    </div>
  );
}

const tabStyle = (active: boolean): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px',
  borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
  border: '1px solid', userSelect: 'none', transition: 'all 120ms',
  borderColor: active ? 'var(--primary)' : 'var(--border-card)',
  background: active ? 'var(--primary)' : '#FFF',
  color: active ? '#FFF' : 'var(--fg-secondary)',
});

export function OutreachPage() {
  const [tab, setTab] = useState<TabKey>('leads');
  const [statusFilter, setStatusFilter] = useState<OutreachStatus | 'all'>('all');
  const [hover, setHover] = useState<string | null>(null);

  const [rows, setRows] = useState<OutreachRow[]>([]);
  const [metrics, setMetrics] = useState<OutreachMetrics | null>(null);
  const [config, setConfig] = useState<OutreachConfig | null>(null);
  const [counts, setCounts] = useState<Record<TabKey, number>>({ leads: 0, candidates: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, m, leadsCount, candCount, cfg] = await Promise.all([
        fetchOutreach(tab, 'all', 1, 200),
        fetchOutreachMetrics(tab),
        fetchOutreachMetrics('leads'),
        fetchOutreachMetrics('candidates'),
        fetchOutreachConfig().catch(() => null),
      ]);
      setRows(list.items);
      setMetrics(m);
      setCounts({ leads: leadsCount.total, candidates: candCount.total });
      if (cfg) setConfig(cfg);
    } catch (e: any) {
      setError(e.message || 'Failed to load outreach');
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  // Live refresh every 8s (webhook-fed data).
  useEffect(() => {
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [load]);

  const m = metrics ?? { total: 0, opened: 0, replied: 0, meetings: 0, unsubscribed: 0, bounced: 0 };
  const visibleRows = useMemo(
    () => (statusFilter === 'all' ? rows : rows.filter((r) => r.status === statusFilter)),
    [rows, statusFilter],
  );
  const noun = tab === 'leads' ? 'leads' : 'candidates';

  return (
    <>
      <TopBar
        title="Outreach"
        showSearch={false}
        actions={
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px',
            borderRadius: 6, fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)',
            border: '1px solid var(--border-card)', background: '#FFF',
          }}>
            <span style={{ width: 7, height: 7, borderRadius: 9999, background: config?.sendEnabled ? 'var(--status-success)' : 'var(--fg-subtle)' }} />
            {config?.sendEnabled ? `Live · ${config.provider}` : 'Tracking ready'}
          </span>
        }
      />

      {/* Subsection tabs */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '12px 24px', borderBottom: '1px solid var(--border-default)', background: 'var(--bg-app)',
      }}>
        {TABS.map((tb) => (
          <button key={tb.key} onClick={() => { setTab(tb.key); setStatusFilter('all'); }} style={tabStyle(tab === tb.key)}>
            <Icon name={tb.icon} size={15} />
            {tb.label}
            <span style={{
              fontSize: 11, fontWeight: 700, padding: '1px 7px', borderRadius: 9999,
              background: tab === tb.key ? 'rgba(255,255,255,0.2)' : 'var(--bg-chip, #F3F4F6)',
              color: tab === tb.key ? '#FFF' : 'var(--fg-muted)',
            }}>
              {counts[tb.key]}
            </span>
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
          {TABS.find((t) => t.key === tab)?.sub}
        </span>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
        {error && (
          <div style={{ padding: '14px 18px', marginBottom: 16, background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        )}

        {/* Funnel summary — Replied & Meetings are headline; Opens dimmed */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginBottom: 20 }}>
          <FunnelTile label="Outreach sent" value={m.total}        total={m.total} color="#6B7280" icon="send" />
          <FunnelTile label="Opened"        value={m.opened}       total={m.total} color="#64748B" icon="mail-open" dimmed note="Open rates are inflated by Apple Mail Privacy Protection — treated as a soft signal." />
          <FunnelTile label="Replied"       value={m.replied}      total={m.total} color="#10B981" icon="reply" />
          <FunnelTile label="Meetings"      value={m.meetings}     total={m.total} color="#8B5CF6" icon="calendar-check" />
          <FunnelTile label="Unsubscribed"  value={m.unsubscribed} total={m.total} color="#EF4444" icon="user-x" />
        </div>

        {/* Status filter chips */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
          {FILTER_KEYS.map((key) => {
            const active = statusFilter === key;
            const label = key === 'all' ? 'All' : STATUS[key as OutreachStatus].label;
            return (
              <button
                key={key}
                onClick={() => setStatusFilter(key)}
                style={{
                  padding: '4px 11px', borderRadius: 9999, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: '1px solid', transition: 'all 120ms',
                  borderColor: active ? 'var(--primary)' : 'var(--border-card)',
                  background: active ? 'var(--primary)' : '#FFF',
                  color: active ? '#FFF' : 'var(--fg-secondary)',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        {/* CRM table */}
        <div style={{ background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 12, overflow: 'hidden' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: '2.2fr 1.6fr 0.9fr 1.3fr 1.8fr',
            gap: 16, padding: '11px 20px', borderBottom: '1px solid var(--border-default)',
            background: '#FAFAFA', fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)',
            textTransform: 'uppercase', letterSpacing: '0.05em',
          }}>
            <span>{tab === 'leads' ? 'Contact' : 'Candidate'}</span>
            <span>{tab === 'leads' ? 'Company' : 'Role matched'}</span>
            <span>Channel</span>
            <span>Status</span>
            <span>Last activity</span>
          </div>

          {loading && rows.length === 0 ? (
            <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--fg-muted)' }}>
              <Icon name="loader" size={22} />
              <div style={{ marginTop: 10, fontSize: 13 }}>Loading outreach…</div>
            </div>
          ) : rows.length === 0 ? (
            <EmptyState noun={noun} config={config} />
          ) : visibleRows.length === 0 ? (
            <div style={{ padding: '40px 20px', textAlign: 'center', color: 'var(--fg-muted)', fontSize: 13 }}>
              No {noun} with this status.
            </div>
          ) : (
            visibleRows.map((row, i) => (
              <div
                key={row.id}
                onMouseEnter={() => setHover(row.id)}
                onMouseLeave={() => setHover(null)}
                style={{
                  display: 'grid', gridTemplateColumns: '2.2fr 1.6fr 0.9fr 1.3fr 1.8fr',
                  gap: 16, padding: '14px 20px', alignItems: 'center',
                  borderBottom: i < visibleRows.length - 1 ? '1px solid var(--border-default)' : 'none',
                  background: hover === row.id ? '#F9FAFB' : '#FFF', transition: 'background 120ms',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
                  <Avatar name={row.name} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.name}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {row.email}
                    </div>
                  </div>
                </div>

                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {row.secondary}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {row.title}
                  </div>
                </div>

                <div style={{ fontSize: 12, color: 'var(--fg-secondary)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <Icon name={row.channel === 'LinkedIn' ? 'linkedin' : 'mail'} size={14} style={{ color: 'var(--fg-muted)' }} />
                  {row.channel}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <StatusPill status={row.status} />
                  {row.replyClass && (
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'capitalize' }}>
                      {row.replyClass.replace('_', ' ')}
                    </span>
                  )}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 0 }}>
                  <span style={{ fontSize: 12, color: 'var(--fg-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {row.lastActivity}{row.lastActivityAt ? ` · ${relTime(row.lastActivityAt)}` : ''}
                  </span>
                  <Icon name="chevron-right" size={14} style={{ color: 'var(--fg-muted)', opacity: hover === row.id ? 1 : 0, transition: 'opacity 120ms', flexShrink: 0 }} />
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}

function EmptyState({ noun, config }: { noun: string; config: OutreachConfig | null }) {
  return (
    <div style={{ padding: '44px 24px', textAlign: 'center', maxWidth: 460, margin: '0 auto' }}>
      <div style={{
        width: 48, height: 48, margin: '0 auto 16px', borderRadius: 12, background: 'var(--bg-chip, #F3F4F6)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)',
      }}>
        <Icon name="mail" size={24} />
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--fg-primary)', marginBottom: 8 }}>
        No {noun} tracked yet
      </div>
      <div style={{ fontSize: 13, color: 'var(--fg-muted)', lineHeight: 1.6, marginBottom: 16 }}>
        Enroll {noun} into a Smartlead campaign and their opens, replies, meetings and
        unsubscribes appear here in real time. Point Smartlead and Cal.com webhooks at this
        backend to start the feed.
      </div>
      <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 6, fontSize: 12 }}>
        <ConfigChip ok={!!config?.sendEnabled} label="Smartlead sending" />
        <ConfigChip ok={!!config?.smartleadWebhookVerified} label="Smartlead webhook verified" soft />
        <ConfigChip ok={!!config?.calcomWebhookVerified} label="Cal.com webhook verified" soft />
      </div>
    </div>
  );
}

function ConfigChip({ ok, label, soft }: { ok: boolean; label: string; soft?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: 'var(--fg-secondary)' }}>
      <Icon
        name={ok ? 'check-circle' : (soft ? 'circle' : 'alert-circle')}
        size={14}
        style={{ color: ok ? 'var(--status-success)' : 'var(--fg-subtle)' }}
      />
      {label}{!ok && !soft ? ' — not configured' : ''}
    </span>
  );
}
