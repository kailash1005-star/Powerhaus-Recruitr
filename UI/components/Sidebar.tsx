'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { useUser } from '@auth0/nextjs-auth0';
import { Icon } from './Icon';
import { fetchQaAccess } from '@/lib/api';
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

/** Initials for the avatar. Prefers a real name, falls back to the email local
 *  part, then to a neutral glyph — never renders an empty circle. */
function initialsFor(user: { name?: string | null; email?: string | null } | undefined): string {
  const source = user?.name || user?.email?.split('@')[0] || '';
  const parts = source.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return '·';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Sidebar() {
  const pathname = usePathname() ?? '';
  const { t } = useI18n();

  // Reads the session from the SDK's /auth/profile endpoint — the ID token's
  // claims, not the access token. There is no token here and there cannot be:
  // this is a client component, and anything it touched would be in the bundle.
  const { user, isLoading } = useUser();

  // Operator-only QA nav item. The backend is the authority (its routes 403
  // non-admins regardless); this probe only decides whether to RENDER the item,
  // so a beta client never sees that an internal QA ledger exists. Defaults to
  // hidden; stays hidden on any error.
  const [isQaAdmin, setIsQaAdmin] = useState(false);
  useEffect(() => {
    let alive = true;
    fetchQaAccess()
      .then((r) => { if (alive) setIsQaAdmin(!!r.isAdmin); })
      .catch(() => { /* hidden */ });
    return () => { alive = false; };
  }, []);

  const displayName = user?.email ?? user?.name ?? '';

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
            {(g.labelKey === 'group.monitor' && isQaAdmin
              ? [...g.items, { href: '/qa', icon: 'shield-check', labelKey: 'nav.qa' as TKey, matchPrefix: '/qa' }]
              : g.items
            ).map((item) => {
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
          {user?.picture ? (
            // eslint-disable-next-line @next/next/no-img-element -- avatar host is
            // Auth0/Google/Gravatar and varies per connection; next/image would
            // need every one allow-listed in next.config for no real benefit.
            <img
              src={user.picture}
              alt=""
              width={24}
              height={24}
              style={{ ...sidebarStyles.accountAvatar, objectFit: 'cover' }}
            />
          ) : (
            <div style={sidebarStyles.accountAvatar}>{initialsFor(user ?? undefined)}</div>
          )}
          <span style={sidebarStyles.accountText}>
            {isLoading ? '…' : displayName}
          </span>
          {/* A plain <a>, not a Link: /auth/logout is served by middleware, not by
              the App Router, so client-side navigation would 404. It must be a
              real document request. */}
          <a
            href="/auth/logout"
            title="Sign out"
            aria-label="Sign out"
            style={{ display: 'inline-flex', color: 'var(--fg-subtle)', textDecoration: 'none' }}
          >
            <Icon name="log-out" size={14} />
          </a>
        </div>
      </div>
    </aside>
  );
}
