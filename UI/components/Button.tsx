import { useState } from 'react';
import { Icon } from './Icon';

interface ButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  icon?: string;
  variant?: 'primary' | 'secondary';
  dropdown?: boolean;
  disabled?: boolean;
}

export function Button({ children, onClick, icon, variant = 'primary', dropdown, disabled }: ButtonProps) {
  const [hover, setHover] = useState(false);
  const primary = variant === 'primary';

  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    height: 32,
    padding: '0 12px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: disabled ? 'not-allowed' : 'pointer',
    userSelect: 'none',
    transition: 'background 120ms, border-color 120ms',
    fontFamily: 'inherit',
    whiteSpace: 'nowrap',
    opacity: disabled ? 0.4 : 1,
  };

  const style = primary
    ? {
        ...base,
        background: hover && !disabled ? '#1F1F1F' : '#0F0F0F',
        color: '#FFFFFF',
        border: '1px solid #0F0F0F',
      }
    : {
        ...base,
        background: hover && !disabled ? '#F9FAFB' : '#FFFFFF',
        color: '#111827',
        border: '1px solid #D1D5DB',
      };

  return (
    <button
      style={style}
      onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      disabled={disabled}
    >
      {icon && <Icon name={icon} size={14} />}
      {children}
      {dropdown && <Icon name="chevron-down" size={14} style={{ opacity: 0.7 }} />}
    </button>
  );
}
