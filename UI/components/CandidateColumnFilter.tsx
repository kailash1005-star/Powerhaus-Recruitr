'use client';

import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';

/**
 * A filter control that lives in a table column header.
 *
 * Three kinds, matched to what the column holds:
 *   • `text`    — free-text "contains" (Candidate, Current Role). Facets are
 *                 useless here: the values are near-unique.
 *   • `options` — multi-select checkboxes with counts (Company, Location,
 *                 Status). Selecting several means OR.
 *   • `range`   — numeric min–max (Match).
 *
 * The popover edits a DRAFT and only lifts state on Apply, so ticking four
 * companies is one request, not four. Escape / click-outside discards the draft.
 */

export type FilterKind = 'text' | 'options' | 'range';

export interface FilterOption { value: string; count: number }

interface Props {
  label: string;
  kind: FilterKind;
  /** Whether this column currently has a filter applied (drives the icon). */
  active: boolean;
  onClear: () => void;
  /** Anchor the panel's right edge instead of its left — for columns near the
   *  right edge, where a left-anchored panel would overflow the scroll area. */
  align?: 'left' | 'right';
  // text
  text?: string;
  onText?: (v: string) => void;
  // options
  options?: FilterOption[];
  selected?: string[];
  onOptions?: (v: string[]) => void;
  /** Human labels for option values, e.g. accepted → Accepted. */
  optionLabel?: (v: string) => string;
  // range
  min?: number;
  max?: number;
  onRange?: (min?: number, max?: number) => void;
}

const panel: React.CSSProperties = {
  position: 'absolute', top: 'calc(100% + 6px)', zIndex: 40,
  minWidth: 240, maxWidth: 300, background: '#FFF',
  border: '1px solid var(--border-card)', borderRadius: 10,
  boxShadow: '0 8px 28px rgba(0,0,0,0.14)', padding: 12,
  textTransform: 'none', letterSpacing: 'normal',
};

const input: React.CSSProperties = {
  width: '100%', height: 32, padding: '0 9px', borderRadius: 6,
  border: '1px solid var(--border-card)', fontSize: 13, fontFamily: 'inherit',
  background: '#FFF', color: 'var(--fg-primary)', boxSizing: 'border-box',
};

const btn = (primary: boolean): React.CSSProperties => ({
  height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12, fontWeight: 700,
  fontFamily: 'inherit', cursor: 'pointer',
  border: primary ? 'none' : '1px solid var(--border-card)',
  background: primary ? 'var(--primary)' : '#FFF',
  color: primary ? '#FFF' : 'var(--fg-secondary)',
});

export function CandidateColumnFilter(props: Props) {
  const { label, kind, active, onClear } = props;
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  // Draft state — seeded from the applied filter each time the popover opens.
  const [draftText, setDraftText] = useState('');
  const [draftSel, setDraftSel] = useState<string[]>([]);
  const [draftMin, setDraftMin] = useState('');
  const [draftMax, setDraftMax] = useState('');
  const [optQuery, setOptQuery] = useState('');

  useEffect(() => {
    if (!open) return;
    setDraftText(props.text || '');
    setDraftSel(props.selected || []);
    setDraftMin(props.min != null ? String(props.min) : '');
    setDraftMax(props.max != null ? String(props.max) : '');
    setOptQuery('');
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Close on click-outside / Escape, discarding the draft.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const apply = () => {
    if (kind === 'text') props.onText?.(draftText.trim());
    else if (kind === 'options') props.onOptions?.(draftSel);
    else {
      // Blank = unbounded. Guard the inverted range rather than silently
      // returning zero rows for min>max.
      const lo = draftMin.trim() === '' ? undefined : Number(draftMin);
      const hi = draftMax.trim() === '' ? undefined : Number(draftMax);
      props.onRange?.(
        lo != null && hi != null ? Math.min(lo, hi) : lo,
        lo != null && hi != null ? Math.max(lo, hi) : hi,
      );
    }
    setOpen(false);
  };

  const clear = () => { onClear(); setOpen(false); };

  const visibleOptions = (props.options || []).filter(
    (o) => !optQuery.trim() || o.value.toLowerCase().includes(optQuery.trim().toLowerCase()),
  );

  return (
    <div ref={wrapRef} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span>{label}</span>
      <button
        type="button"
        title={active ? `Filtered by ${label} — click to edit` : `Filter by ${label}`}
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 20, height: 20, borderRadius: 4, cursor: 'pointer', padding: 0,
          border: 'none',
          background: active ? 'var(--primary)' : 'transparent',
          color: active ? '#FFF' : 'var(--fg-muted)',
        }}
      >
        <Icon name="filter" size={11} />
      </button>

      {open && (
        <div
          style={{ ...panel, ...(props.align === 'right' ? { right: 0 } : { left: 0 }) }}
          onClick={(e) => e.stopPropagation()}
        >
          <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 9 }}>
            Filter: {label}
          </div>

          {kind === 'text' && (
            <input
              autoFocus value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') apply(); }}
              placeholder={`${label} contains…`}
              style={input}
            />
          )}

          {kind === 'options' && (
            <>
              {(props.options || []).length > 8 && (
                <input
                  autoFocus value={optQuery}
                  onChange={(e) => setOptQuery(e.target.value)}
                  placeholder="Search…"
                  style={{ ...input, marginBottom: 8 }}
                />
              )}
              <div style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
                {visibleOptions.length === 0 && (
                  <div style={{ fontSize: 12.5, color: 'var(--fg-muted)', padding: '6px 2px' }}>
                    No matching values.
                  </div>
                )}
                {visibleOptions.map((o) => {
                  const on = draftSel.includes(o.value);
                  return (
                    <label
                      key={o.value}
                      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 3px', borderRadius: 5, cursor: 'pointer', fontSize: 13, color: 'var(--fg-primary)' }}
                    >
                      <input
                        type="checkbox" checked={on}
                        onChange={() => setDraftSel(
                          on ? draftSel.filter((v) => v !== o.value) : [...draftSel, o.value],
                        )}
                        style={{ cursor: 'pointer', flexShrink: 0 }}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {props.optionLabel ? props.optionLabel(o.value) : o.value}
                      </span>
                      <span style={{ fontSize: 11.5, color: 'var(--fg-muted)', fontWeight: 600, flexShrink: 0 }}>
                        {o.count}
                      </span>
                    </label>
                  );
                })}
              </div>
            </>
          )}

          {kind === 'range' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                autoFocus type="number" min={0} max={100} value={draftMin}
                onChange={(e) => setDraftMin(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') apply(); }}
                placeholder="Min" style={input}
              />
              <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>to</span>
              <input
                type="number" min={0} max={100} value={draftMax}
                onChange={(e) => setDraftMax(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') apply(); }}
                placeholder="Max" style={input}
              />
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 11 }}>
            <button type="button" onClick={clear} style={btn(false)}>Clear</button>
            <button type="button" onClick={apply} style={btn(true)}>Apply</button>
          </div>
        </div>
      )}
    </div>
  );
}
