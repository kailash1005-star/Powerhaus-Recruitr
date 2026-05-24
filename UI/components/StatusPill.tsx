type StatusTone = 'neutral' | 'amber' | 'warning' | 'info' | 'success' | 'purple' | 'danger';

const STATUS_TONES = {
  neutral: { dot: '#6B7280', bg: '#F3F4F6', fg: '#374151' },
  amber: { dot: '#F59E0B', bg: '#F3F4F6', fg: '#374151' },
  warning: { dot: '#F59E0B', bg: '#F3F4F6', fg: '#374151' },
  info: { dot: '#3B82F6', bg: '#F3F4F6', fg: '#374151' },
  success: { dot: '#10B981', bg: '#F3F4F6', fg: '#374151' },
  purple: { dot: '#8B5CF6', bg: '#F3F4F6', fg: '#374151' },
  danger: { dot: '#EF4444', bg: '#F3F4F6', fg: '#374151' },
};

interface StatusPillProps {
  label: string;
  tone?: StatusTone;
  showDot?: boolean;
  mono?: boolean;
}

export function StatusPill({ label, tone = 'neutral', showDot = true, mono = false }: StatusPillProps) {
  const t = STATUS_TONES[tone] || STATUS_TONES.neutral;

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        height: 22,
        padding: '0 8px',
        background: t.bg,
        color: t.fg,
        borderRadius: 8,
        fontSize: 12,
        fontWeight: 500,
        lineHeight: 1,
        letterSpacing: mono ? '0.02em' : 0,
        fontFamily: mono ? 'var(--font-mono)' : 'inherit',
        whiteSpace: 'nowrap',
      }}
    >
      {showDot && (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: 9999,
            background: t.dot,
            flexShrink: 0,
          }}
        />
      )}
      {label}
    </span>
  );
}

interface ChipProps {
  children: React.ReactNode;
}

export function Chip({ children }: ChipProps) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 22,
        padding: '0 8px',
        background: '#F3F4F6',
        color: '#374151',
        borderRadius: 8,
        fontSize: 12,
        fontWeight: 500,
        lineHeight: 1,
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}
