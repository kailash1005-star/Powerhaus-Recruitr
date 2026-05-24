import { Icon } from './Icon';

interface TopBarProps {
  title?: string;
  /** Pass a ReactNode to override the plain text title (e.g. a back-link) */
  titleNode?: React.ReactNode;
  actions?: React.ReactNode;
  showSearch?: boolean;
}

export function TopBar({ title, titleNode, actions, showSearch = true }: TopBarProps) {
  return (
    <div
      style={{
        height: 'var(--h-topbar)',
        minHeight: 'var(--h-topbar)',
        padding: '0 24px',
        display: 'flex',
        alignItems: 'center',
        borderBottom: '1px solid var(--border-default)',
        background: 'var(--bg-app)',
        position: 'relative',
      }}
    >
      {/* Left — title */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center' }}>
        {titleNode ?? (
          <span
            style={{
              fontSize: 'var(--text-18)',
              fontWeight: 'var(--w-semibold)',
              color: 'var(--fg-primary)',
              letterSpacing: 'var(--tracking-tight)',
            }}
          >
            {title}
          </span>
        )}
      </div>

      {/* Center — search (absolutely centered so it's always in the middle) */}
      {showSearch && (
        <div
          style={{
            position: 'absolute',
            left: '50%',
            transform: 'translateX(-50%)',
            width: 280,
          }}
        >
          <span
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--fg-subtle)',
              pointerEvents: 'none',
            }}
          >
            <Icon name="search" size={14} />
          </span>
          <input
            style={{
              width: '100%',
              height: 32,
              padding: '0 10px 0 30px',
              background: 'var(--bg-app)',
              border: '1px solid var(--border-card)',
              borderRadius: 6,
              fontSize: 13,
              color: 'var(--fg-primary)',
              outline: 'none',
              fontFamily: 'inherit',
              boxSizing: 'border-box',
            }}
            placeholder="Search…"
          />
        </div>
      )}

      {/* Right — actions */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
        {actions}
      </div>
    </div>
  );
}
