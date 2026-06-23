'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Icon } from './Icon';

interface NavItem {
  href: string;
  icon: string;
  label: string;
  matchPrefix?: string;
  badge?: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'ASSISTANT',
    items: [
      { href: '/agent', icon: 'sparkles', label: 'AI Engineer', matchPrefix: '/agent' },
    ],
  },
  {
    label: 'PIPELINE',
    items: [
      { href: '/runs', icon: 'play-circle', label: 'Runs', matchPrefix: '/runs' },
      { href: '/candidates', icon: 'users', label: 'Candidates', matchPrefix: '/candidates' },
    ],
  },
  {
    label: 'OUTREACH',
    items: [
      { href: '/outreach', icon: 'mail', label: 'Outreach', badge: 'Soon' },
    ],
  },
  {
    label: 'MONITOR',
    items: [
      { href: '/dashboards', icon: 'bar-chart-3', label: 'Dashboards' },
    ],
  },
  {
    label: 'SYSTEM',
    items: [
      { href: '/settings', icon: 'settings', label: 'Settings' },
      { href: '/integrations', icon: 'zap', label: 'Integrations' },
    ],
  },
];

const sidebarStyles = {
  root: {
    width: 230,
    minWidth: 230,
    background: 'var(--bg-sidebar)',
    borderRight: '1px solid var(--border-default)',
    display: 'flex',
    flexDirection: 'column' as const,
    height: '100%',
  },
  brand: {
    padding: '14px 14px 10px 14px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  brandText: {
    fontSize: 15,
    fontWeight: 700,
    color: 'var(--fg-primary)',
    letterSpacing: '-0.01em',
  },
  workspace: {
    margin: '4px 12px 12px 12px',
    padding: '8px 10px',
    background: 'var(--bg-app)',
    border: '1px solid var(--border-default)',
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    cursor: 'pointer',
  },
  wsAvatar: {
    width: 20,
    height: 20,
    borderRadius: 4,
    background: 'var(--primary)',
    color: 'var(--primary-fg)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 600,
    flexShrink: 0,
  },
  wsLabel: {
    flex: 1,
    minWidth: 0,
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--fg-secondary)',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  wsSub: {
    fontSize: 10,
    color: 'var(--fg-subtle)',
  },
  groupsScroll: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '4px 8px',
  },
  group: { marginBottom: 14 },
  groupLabel: {
    padding: '6px 12px',
    fontSize: 10,
    fontWeight: 500,
    color: 'var(--fg-subtle)',
    textTransform: 'uppercase' as const,
    letterSpacing: 'var(--tracking-widest)',
  },
  bottom: {
    borderTop: '1px solid var(--border-default)',
    padding: 12,
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 8,
  },
  planBadge: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 10px',
    background: 'var(--bg-app)',
    border: '1px solid var(--border-default)',
    borderRadius: 8,
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--fg-secondary)',
    cursor: 'pointer',
  },
  account: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 8px',
    borderRadius: 6,
    cursor: 'pointer',
  },
  accountAvatar: {
    width: 24,
    height: 24,
    borderRadius: 9999,
    background: 'var(--fg-secondary)',
    color: '#FFF',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 11,
    fontWeight: 600,
    flexShrink: 0,
  },
  accountText: {
    flex: 1,
    minWidth: 0,
    fontSize: 12,
    color: 'var(--fg-secondary)',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
};

function isActive(pathname: string, item: NavItem): boolean {
  const prefix = item.matchPrefix ?? item.href;
  if (prefix === '/') return pathname === '/';
  return pathname === prefix || pathname.startsWith(prefix + '/');
}

export function Sidebar() {
  const pathname = usePathname() ?? '';
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (typeof window !== 'undefined' && localStorage.getItem('sidebarCollapsed') === '1') {
      setCollapsed(true);
    }
  }, []);

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      if (typeof window !== 'undefined') localStorage.setItem('sidebarCollapsed', next ? '1' : '0');
      return next;
    });

  const navItemStyle = (active: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    justifyContent: collapsed ? 'center' : 'flex-start',
    gap: collapsed ? 0 : 10,
    padding: collapsed ? '8px 0' : '7px 12px',
    margin: '1px 0',
    borderRadius: 6,
    background: active ? 'var(--bg-nav-active)' : 'transparent',
    color: active ? 'var(--fg-primary)' : 'var(--fg-secondary)',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background 120ms',
    userSelect: 'none',
    textDecoration: 'none',
  });

  const toggleBtnStyle: React.CSSProperties = {
    width: 24, height: 24, borderRadius: 6, border: 'none', background: 'transparent',
    display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0,
  };

  return (
    <aside style={{ ...sidebarStyles.root, width: collapsed ? 64 : 230, minWidth: collapsed ? 64 : 230 }}>
      <div style={{ ...sidebarStyles.brand, justifyContent: collapsed ? 'center' : 'flex-start' }}>
        {!collapsed && <img src="/logo-mark.svg" width="18" height="18" alt="" />}
        {!collapsed && <span style={{ ...sidebarStyles.brandText, flex: 1 }}>Recruitr</span>}
        <button style={toggleBtnStyle} onClick={toggle} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          <Icon name={collapsed ? 'chevrons-right' : 'chevrons-left'} size={15} style={{ color: 'var(--fg-subtle)' }} />
        </button>
      </div>

      {!collapsed ? (
        <div style={sidebarStyles.workspace}>
          <div style={sidebarStyles.wsAvatar}>R</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={sidebarStyles.wsLabel}>RECRUITR</div>
            <div style={sidebarStyles.wsSub}>Workspace</div>
          </div>
          <Icon name="chevrons-up-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
        </div>
      ) : (
        <div style={{ display: 'flex', justifyContent: 'center', margin: '4px 0 12px' }}>
          <div style={sidebarStyles.wsAvatar}>R</div>
        </div>
      )}

      <div style={sidebarStyles.groupsScroll}>
        {NAV_GROUPS.map((g) => (
          <div key={g.label} style={sidebarStyles.group}>
            {!collapsed && <div style={sidebarStyles.groupLabel}>{g.label}</div>}
            {g.items.map((item) => {
              const active = isActive(pathname, item);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  title={item.label}
                  style={navItemStyle(active)}
                  onMouseEnter={(e) => {
                    if (!active) (e.currentTarget as HTMLAnchorElement).style.background = '#EFEFEF';
                  }}
                  onMouseLeave={(e) => {
                    if (!active) (e.currentTarget as HTMLAnchorElement).style.background = 'transparent';
                  }}
                >
                  <Icon
                    name={item.icon}
                    size={16}
                    style={{ color: active ? 'var(--fg-primary)' : 'var(--fg-muted)' }}
                  />
                  {!collapsed && <span style={{ flex: 1 }}>{item.label}</span>}
                  {!collapsed && item.badge && (
                    <span
                      style={{
                        fontSize: 10,
                        fontWeight: 600,
                        padding: '2px 6px',
                        borderRadius: 4,
                        background: 'var(--bg-app)',
                        color: 'var(--fg-muted)',
                        border: '1px solid var(--border-default)',
                        textTransform: 'uppercase',
                        letterSpacing: '0.04em',
                      }}
                    >
                      {item.badge}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </div>

      <div style={sidebarStyles.bottom}>
        {!collapsed ? (
          <>
            <div style={sidebarStyles.planBadge}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: 9999, background: 'var(--status-success)' }} />
                Free Trial
              </span>
              <Icon name="chevron-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
            </div>
            <div style={sidebarStyles.account}>
              <div style={sidebarStyles.accountAvatar}>U</div>
              <span style={sidebarStyles.accountText}>user@recruitr.io</span>
              <Icon name="chevron-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
            </div>
          </>
        ) : (
          <div style={{ display: 'flex', justifyContent: 'center' }} title="user@recruitr.io">
            <div style={sidebarStyles.accountAvatar}>U</div>
          </div>
        )}
      </div>
    </aside>
  );
}
