'use client';

import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { suggestLocations, type LocationSuggestion } from '@/lib/api';

/**
 * LinkedIn-style location input: type "kobl", pick "Koblenz, Germany" from the
 * dropdown. Chosen labels are stored as tags (multi-value) and are the canonical,
 * correctly-spelled strings both search engines receive — so a recruiter's typo
 * can never silently zero a search. Suggestions come from the offline gazetteer
 * (no per-keystroke network cost worth worrying about), debounced lightly.
 *
 * A location the recruiter insists on that isn't in the catalogue can still be
 * added verbatim with Enter — the field never blocks a deliberate entry.
 */
export function LocationAutocomplete({
  value, onChange, placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<LocationSuggestion[]>([]);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const reqId = useRef(0);

  const add = (label: string) => {
    const t = label.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setText(''); setItems([]); setOpen(false); setActive(0);
  };

  // Debounced typeahead. A monotonically increasing request id guards against
  // out-of-order responses overwriting a newer query's results.
  useEffect(() => {
    const q = text.trim();
    if (!q) { setItems([]); setOpen(false); return; }
    const id = ++reqId.current;
    const h = setTimeout(async () => {
      try {
        const { suggestions } = await suggestLocations(q, 8);
        if (id === reqId.current) { setItems(suggestions); setOpen(true); setActive(0); }
      } catch { /* typeahead is best-effort — never block typing on it */ }
    }, 120);
    return () => clearTimeout(h);
  }, [text]);

  // Close the dropdown on an outside click.
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (open && items.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => (a + 1) % items.length); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => (a - 1 + items.length) % items.length); return; }
      if (e.key === 'Enter') { e.preventDefault(); add(items[active].label); return; }
      if (e.key === 'Escape') { setOpen(false); return; }
    }
    if ((e.key === 'Enter' || e.key === ',') && text.trim()) { e.preventDefault(); add(text); }
  };

  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center', minHeight: 38, padding: '5px 8px', borderRadius: 8, border: '1px solid var(--border-card)', background: '#FFF' }}>
        {value.map((v) => (
          <span key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: 'var(--accent-soft, #EEF0FE)', color: 'var(--primary)', borderRadius: 6, padding: '3px 8px', fontSize: 12.5, fontWeight: 600 }}>
            <Icon name="map-pin" size={11} style={{ opacity: 0.7 }} />
            {v}
            <button onClick={() => onChange(value.filter((x) => x !== v))} style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--primary)', display: 'inline-flex', padding: 0 }}><Icon name="x" size={12} /></button>
          </span>
        ))}
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => { if (items.length) setOpen(true); }}
          placeholder={value.length ? '' : (placeholder || 'Type a city…')}
          style={{ flex: 1, minWidth: 120, border: 'none', outline: 'none', fontSize: 14, fontFamily: 'inherit', background: 'transparent', height: 26, color: 'var(--fg-primary)' }}
        />
      </div>

      {open && items.length > 0 && <Dropdown items={items} active={active} setActive={setActive} onPick={add} />}
    </div>
  );
}

/** The suggestion dropdown, shared by the multi-tag and single-value inputs. */
function Dropdown({
  items, active, setActive, onPick,
}: {
  items: LocationSuggestion[];
  active: number;
  setActive: (i: number) => void;
  onPick: (label: string) => void;
}) {
  return (
    <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4, zIndex: 50, background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', overflow: 'hidden', maxHeight: 280, overflowY: 'auto' }}>
      {items.map((s, i) => (
        <button
          key={s.label}
          type="button"
          onMouseEnter={() => setActive(i)}
          onMouseDown={(e) => { e.preventDefault(); onPick(s.label); }}
          style={{ display: 'flex', alignItems: 'center', gap: 9, width: '100%', textAlign: 'left', padding: '9px 12px', border: 'none', cursor: 'pointer', fontFamily: 'inherit', fontSize: 13.5, background: i === active ? 'var(--accent-soft, #EEF0FE)' : '#FFF', color: 'var(--fg-primary)' }}
        >
          <Icon name={s.kind === 'country' ? 'globe' : 'map-pin'} size={14} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
          <span style={{ fontWeight: 600 }}>{s.label}</span>
          {s.kind !== 'city' && (
            <span style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--fg-muted)' }}>{s.kind}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/**
 * Single-value location field with the same gazetteer typeahead — for "Job
 * Location" and other one-place inputs. Behaves like a normal text box: what you
 * type IS the value (so a deliberate entry is never blocked), and picking a
 * suggestion fills it with the canonical, correctly-spelled label.
 */
export function LocationInput({
  value, onChange, placeholder, style, disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  style?: React.CSSProperties;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<LocationSuggestion[]>([]);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState('');   // last text the user typed
  const boxRef = useRef<HTMLDivElement>(null);
  const reqId = useRef(0);

  const pick = (label: string) => {
    onChange(label); setItems([]); setOpen(false); setActive(0); setQuery('');
  };

  useEffect(() => {
    const q = query.trim();
    if (!q) { setItems([]); setOpen(false); return; }
    const id = ++reqId.current;
    const h = setTimeout(async () => {
      try {
        const { suggestions } = await suggestLocations(q, 8);
        if (id === reqId.current) { setItems(suggestions); setOpen(true); setActive(0); }
      } catch { /* best-effort */ }
    }, 120);
    return () => clearTimeout(h);
  }, [query]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || !items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => (a + 1) % items.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => (a - 1 + items.length) % items.length); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(items[active].label); }
    else if (e.key === 'Escape') { setOpen(false); }
  };

  return (
    <div ref={boxRef} style={{ position: 'relative' }}>
      <input
        value={value}
        disabled={disabled}
        onChange={(e) => { onChange(e.target.value); setQuery(e.target.value); }}
        onKeyDown={onKeyDown}
        onFocus={() => { if (items.length) setOpen(true); }}
        placeholder={placeholder || 'Type a city, e.g. Koblenz'}
        style={style}
      />
      {open && items.length > 0 && <Dropdown items={items} active={active} setActive={setActive} onPick={pick} />}
    </div>
  );
}
