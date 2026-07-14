'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Icon } from './Icon';
import { useI18n, type TKey } from '@/lib/i18n';

interface NavItem {
  href: string;
  icon: string;
  labelKey: TKey;
  matchPrefix?: string;
  badgeKey?: TKey;
}

interface NavGroup {
  labelKey: TKey;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    labelKey: 'group.assistant',
    items: [
      { href: '/agent', icon: 'sparkles', labelKey: 'nav.agent', matchPrefix: '/agent' },
    ],
  },
  {
    labelKey: 'group.pipeline',
    items: [
      { href: '/runs', icon: 'play-circle', labelKey: 'nav.runs', matchPrefix: '/runs' },
      { href: '/candidates', icon: 'users', labelKey: 'nav.candidates', matchPrefix: '/candidates' },
      { href: '/matching', icon: 'sparkles', labelKey: 'nav.matching', matchPrefix: '/matching' },
    ],
  },
  {
    labelKey: 'group.outreach',
    items: [
      { href: '/outreach', icon: 'mail', labelKey: 'nav.outreach' },
    ],
  },
  {
    labelKey: 'group.monitor',
    items: [
      { href: '/dashboards', icon: 'bar-chart-3', labelKey: 'nav.dashboards' },
      // Temporarily hidden — revert to restore the Costs nav item
      // { href: '/cost', icon: 'wallet', labelKey: 'nav.costs', matchPrefix: '/cost' },
    ],
  },
  {
    labelKey: 'group.system',
    items: [
      { href: '/settings', icon: 'settings', labelKey: 'nav.settings' },
      { href: '/integrations', icon: 'zap', labelKey: 'nav.integrations' },
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
  const { t } = useI18n();

  const navItemStyle = (active: boolean): React.CSSProperties => ({
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '7px 12px',
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

  return (
    <aside style={sidebarStyles.root}>
      <div style={sidebarStyles.brand}>
        <img src="/logo-mark.svg" width="18" height="18" alt="" />
        <span style={sidebarStyles.brandText}>Recruitr</span>
      </div>

      <div style={sidebarStyles.workspace}>
        <div style={sidebarStyles.wsAvatar}>R</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={sidebarStyles.wsLabel}>RECRUITR</div>
          <div style={sidebarStyles.wsSub}>{t('sidebar.workspace')}</div>
        </div>
        <Icon name="chevrons-up-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
      </div>

      <div style={sidebarStyles.groupsScroll}>
        {NAV_GROUPS.map((g) => (
          <div key={g.labelKey} style={sidebarStyles.group}>
            <div style={sidebarStyles.groupLabel}>{t(g.labelKey)}</div>
            {g.items.map((item) => {
              const active = isActive(pathname, item);
              return (
                <Link
                  key={item.href}
                  href={item.href}
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
                  <span style={{ flex: 1 }}>{t(item.labelKey)}</span>
                  {item.badgeKey && (
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
                      {t(item.badgeKey)}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </div>

      <div style={sidebarStyles.bottom}>
        <div style={sidebarStyles.planBadge}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 9999,
                background: 'var(--status-success)',
              }}
            />
            {t('sidebar.freeTrial')}
          </span>
          <Icon name="chevron-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
        </div>
        <div style={sidebarStyles.account}>
          <div style={sidebarStyles.accountAvatar}>U</div>
          <span style={sidebarStyles.accountText}>user@recruitr.io</span>
          <Icon name="chevron-down" size={14} style={{ color: 'var(--fg-subtle)' }} />
        </div>
      </div>
    </aside>
  );
}
