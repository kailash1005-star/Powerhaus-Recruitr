import { LucideIcon } from 'lucide-react';
import * as Icons from 'lucide-react';

interface IconProps {
  name: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function Icon({ name, size = 16, strokeWidth = 1.75, className, style }: IconProps) {
  const iconName = name
    .split('-')
    .map((word, i) => (i === 0 ? word : word.charAt(0).toUpperCase() + word.slice(1)))
    .join('');
  
  const capitalizedName = iconName.charAt(0).toUpperCase() + iconName.slice(1);
  const LucideIcon = (Icons as any)[capitalizedName] as LucideIcon;

  if (!LucideIcon) {
    console.warn(`Icon "${name}" not found`);
    return null;
  }

  return (
    <span
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: size,
        height: size,
        color: 'currentColor',
        flexShrink: 0,
        ...style,
      }}
    >
      <LucideIcon size={size} strokeWidth={strokeWidth} />
    </span>
  );
}
