'use client';

import { useState, useEffect } from 'react';
import { TopBar } from '../TopBar';
import { Button } from '../Button';
import { StatusPill } from '../StatusPill';
import { Icon } from '../Icon';
interface Integration {
  id: string;
  name: string;
  type: string;
  status: string;
  lastSync: string | null;
}

const STATIC_INTEGRATIONS: Integration[] = [
  { id: 'int1', name: 'Apollo.io', type: 'ENRICHMENT', status: 'CONNECTED', lastSync: 'Today, 09:30' },
  { id: 'int2', name: 'Microsoft 365', type: 'EMAIL', status: 'CONNECTED', lastSync: 'Today, 08:00' },
  { id: 'int3', name: 'Salesforce', type: 'CRM', status: 'DISCONNECTED', lastSync: null },
  { id: 'int4', name: 'LinkedIn Jobs', type: 'DISCOVERY', status: 'CONNECTED', lastSync: 'Yesterday' },
];

const TYPE_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'EMAIL', label: 'Email' },
  { key: 'CRM', label: 'CRM' },
  { key: 'ENRICHMENT', label: 'Enrichment' },
  { key: 'DISCOVERY', label: 'Discovery' },
];

const tableStyles = {
  scroll: {
    flex: 1,
    overflow: 'auto' as const,
    background: 'var(--bg-app)',
  },
  table: {
    width: '100%',
    minWidth: 1000,
    borderCollapse: 'separate' as const,
    borderSpacing: 0,
  },
  thead: {
    position: 'sticky' as const,
    top: 0,
    zIndex: 1,
    background: 'var(--bg-app)',
  },
  th: {
    textAlign: 'left' as const,
    fontSize: 'var(--text-12)',
    fontWeight: 'var(--w-medium)',
    color: 'var(--fg-muted)',
    textTransform: 'uppercase' as const,
    letterSpacing: 'var(--tracking-wide)',
    padding: '10px 16px',
    borderBottom: '1px solid var(--border-default)',
    background: 'var(--bg-app)',
    whiteSpace: 'nowrap' as const,
  },
  td: {
    fontSize: 'var(--text-14)',
    color: 'var(--fg-primary)',
    padding: '0 16px',
    height: 'var(--h-row)',
    borderBottom: '1px solid #F3F4F6',
    verticalAlign: 'middle' as const,
    whiteSpace: 'nowrap' as const,
  },
};

export function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState('all');

  useEffect(() => {
    const filtered = typeFilter === 'all'
      ? STATIC_INTEGRATIONS
      : STATIC_INTEGRATIONS.filter((i) => i.type === typeFilter);
    setIntegrations(filtered);
    setLoading(false);
  }, [typeFilter]);

  const getStatusTone = (status: string) => {
    if (status === 'CONNECTED') return 'success';
    if (status === 'ERROR') return 'danger';
    return 'neutral';
  };

  const getTypeIcon = (type: string) => {
    if (type === 'EMAIL') return 'mail';
    if (type === 'CRM') return 'database';
    if (type === 'ENRICHMENT') return 'zap';
    if (type === 'DISCOVERY') return 'search';
    return 'plug';
  };

  const chipStyle = (active: boolean): React.CSSProperties => ({
    display: 'inline-flex',
    alignItems: 'center',
    padding: '5px 12px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    border: '1px solid',
    borderColor: active ? 'var(--primary)' : 'var(--border-card)',
    background: active ? 'var(--primary)' : 'var(--bg-app)',
    color: active ? '#FFFFFF' : 'var(--fg-secondary)',
    transition: 'all 120ms',
    fontFamily: 'inherit',
  });

  return (
    <>
      <TopBar
        title="Integrations"
        actions={
          <Button variant="primary" icon="plus">
            Add Integration
          </Button>
        }
      />

      {/* Inline type filter chips */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '12px 24px',
          borderBottom: '1px solid var(--border-default)',
          background: 'var(--bg-app)',
        }}
      >
        {TYPE_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setTypeFilter(f.key)}
            style={chipStyle(typeFilter === f.key)}
          >
            {f.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
          {integrations.length} integration{integrations.length !== 1 ? 's' : ''}
        </span>
      </div>

      {loading ? (
        <div
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--fg-muted)',
          }}
        >
          <div style={{ textAlign: 'center' }}>
            <Icon name="loader" size={24} />
            <div style={{ marginTop: 12, fontSize: 14 }}>Loading integrations...</div>
          </div>
        </div>
      ) : (
        <div style={tableStyles.scroll}>
          <table style={tableStyles.table}>
            <thead style={tableStyles.thead}>
              <tr>
                <th style={tableStyles.th}>Name</th>
                <th style={tableStyles.th}>Type</th>
                <th style={tableStyles.th}>Status</th>
                <th style={tableStyles.th}>Last Sync</th>
                <th style={tableStyles.th}></th>
              </tr>
            </thead>
            <tbody>
              {integrations.map((integration) => (
                <tr
                  key={integration.id}
                  style={{
                    background: hover === integration.id ? 'var(--bg-row-hover)' : 'transparent',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={() => setHover(integration.id)}
                  onMouseLeave={() => setHover(null)}
                >
                  <td style={{ ...tableStyles.td, fontWeight: 500 }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                      <Icon name={getTypeIcon(integration.type)} size={16} style={{ color: 'var(--fg-muted)' }} />
                      {integration.name}
                    </span>
                  </td>
                  <td style={tableStyles.td}>
                    <span
                      style={{
                        padding: '4px 8px',
                        background: 'var(--bg-chip)',
                        borderRadius: 6,
                        fontSize: 12,
                        color: 'var(--fg-secondary)',
                      }}
                    >
                      {integration.type}
                    </span>
                  </td>
                  <td style={tableStyles.td}>
                    <StatusPill label={integration.status} tone={getStatusTone(integration.status)} />
                  </td>
                  <td style={{ ...tableStyles.td, color: 'var(--fg-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                    {integration.lastSync || '—'}
                  </td>
                  <td style={{ ...tableStyles.td, textAlign: 'right' as const }}>
                    <button
                      style={{
                        width: 28,
                        height: 28,
                        border: 'none',
                        background: 'transparent',
                        borderRadius: 6,
                        cursor: 'pointer',
                        color: 'var(--fg-muted)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <Icon name="more-vertical" size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
