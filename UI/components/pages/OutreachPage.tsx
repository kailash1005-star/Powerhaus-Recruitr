'use client';

import { TopBar } from '../TopBar';
import { Icon } from '../Icon';

export function OutreachPage() {
  return (
    <>
      <TopBar title="Outreach" showSearch={false} />
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--bg-app)',
        }}
      >
        <div style={{ textAlign: 'center', color: 'var(--fg-subtle)', maxWidth: 320 }}>
          <div
            style={{
              width: 48,
              height: 48,
              margin: '0 auto 16px',
              borderRadius: 12,
              background: 'var(--bg-chip)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--fg-muted)',
            }}
          >
            <Icon name="mail" size={24} />
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)', marginBottom: 8 }}>
            Outreach Management
          </div>
          <div style={{ fontSize: 14, color: 'var(--fg-muted)', lineHeight: 1.5 }}>
            Email campaign management, templates, and send queue functionality will be implemented here.
          </div>
        </div>
      </div>
    </>
  );
}
