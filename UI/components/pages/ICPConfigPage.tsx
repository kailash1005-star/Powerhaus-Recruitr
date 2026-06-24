'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  fetchICPConfig,
  addIndustry,
  addTitle,
  addLocation,
  startRun,
  type ICPBackendConfig,
} from '@/lib/api';

// ── local UI state types ────────────────────────────────────────
interface UiIndustry  { id: string; name: string; selected: boolean }
interface UiTitle     { id: string; name: string; selected: boolean }
interface UiLocation  { id: string; name: string; selected: boolean }

// ── shared primitives ───────────────────────────────────────────
const inputStyle: React.CSSProperties = {
  flex: 1,
  height: 34,
  padding: '0 12px',
  border: '1px solid var(--border-card)',
  borderRadius: 6,
  fontSize: 13,
  color: 'var(--fg-primary)',
  background: '#FAFAFA',
  fontFamily: 'inherit',
  outline: 'none',
};

const sectionCard: React.CSSProperties = {
  background: '#FFFFFF',
  border: '1px solid var(--border-card)',
  borderRadius: 10,
  padding: 20,
  marginBottom: 16,
};

const sectionTitle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 600,
  color: 'var(--fg-primary)',
  marginBottom: 14,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
};

const chipBase: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '5px 12px',
  borderRadius: 20,
  fontSize: 13,
  fontWeight: 500,
  cursor: 'pointer',
  border: '1px solid',
  transition: 'all 120ms',
  userSelect: 'none',
  fontFamily: 'inherit',
};

const btnAdd: React.CSSProperties = {
  height: 34,
  padding: '0 14px',
  borderRadius: 6,
  fontSize: 13,
  fontWeight: 500,
  cursor: 'pointer',
  border: '1px solid var(--border-strong)',
  background: 'var(--primary)',
  color: '#FFF',
  fontFamily: 'inherit',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  whiteSpace: 'nowrap' as const,
  flexShrink: 0,
};

const ctrlBtn: React.CSSProperties = {
  height: 32,
  padding: '0 11px',
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 500,
  cursor: 'pointer',
  border: '1px solid var(--border-card)',
  background: '#FFF',
  color: 'var(--fg-secondary)',
  fontFamily: 'inherit',
  whiteSpace: 'nowrap' as const,
};

const sourceCard = (selected: boolean): React.CSSProperties => ({
  flex: 1,
  padding: 14,
  borderRadius: 10,
  border: `1px solid ${selected ? 'var(--primary)' : 'var(--border-card)'}`,
  background: selected ? '#F0F0F0' : '#FAFAFA',
  cursor: 'pointer',
  textAlign: 'left' as const,
  transition: 'all 120ms',
  fontFamily: 'inherit',
});

// Back link node used as the TopBar "title"
const BackLink = () => (
  <Link
    href="/runs"
    style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 7,
      fontSize: 15,
      fontWeight: 600,
      color: 'var(--fg-primary)',
      textDecoration: 'none',
    }}
  >
    <Icon name="arrow-left" size={16} />
    Back to Runs
  </Link>
);

export function ICPConfigPage() {
  const router = useRouter();

  const [icpConfig, setIcpConfig] = useState<ICPBackendConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingRun, setStartingRun] = useState(false);
  // Sleek multi-phase launch sequence — replaces the abrupt button → push flow.
  // idle → starting (overlay fades in, API call in flight + min-display delay)
  //      → created (success tick on the create step)
  //      → opening (brief "opening results" beat)
  //      → navigate
  type LaunchPhase = 'idle' | 'starting' | 'created' | 'opening';
  const [launchPhase, setLaunchPhase] = useState<LaunchPhase>('idle');
  const [launchSummary, setLaunchSummary] = useState<{
    industries: number; titles: number; locations: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // UI state
  const [industries, setIndustries] = useState<UiIndustry[]>([]);
  const [titles, setTitles] = useState<UiTitle[]>([]);
  const [locations, setLocations] = useState<UiLocation[]>([]);

  // Title filter state
  const [titleSearch, setTitleSearch] = useState('');
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);

  // Location filter state
  const [locationSearch, setLocationSearch] = useState('');
  const [showSelectedLocOnly, setShowSelectedLocOnly] = useState(false);

  // Add-new state
  const [newIndustryName, setNewIndustryName] = useState('');
  const [addingIndustry, setAddingIndustry] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [addingTitle, setAddingTitle] = useState(false);
  const [newLocation, setNewLocation] = useState('');
  const [addingLocation, setAddingLocation] = useState(false);

  // Pipeline / scraper config
  // Naukri option is hidden for now — only linkedin is active
  const [activeSources] = useState<string[]>(['linkedin']);
  const [resultsPerBatch, setResultsPerBatch] = useState('50');
  const [maxPostingAge, setMaxPostingAge] = useState('24');

  // ── Load ICP config ───────────────────────────────────────────

  const mapIcpToState = (data: ICPBackendConfig) => {
    setIcpConfig(data);
    setIndustries(data.industries.map((ind) => ({ id: ind.slug, name: ind.displayName, selected: ind.isTarget })));
    setTitles(data.titles.map((t, i) => ({ id: i.toString(), name: t.title, selected: t.isDefault })));
    setLocations(data.locations.map((loc, i) => ({ id: i.toString(), name: loc.location, selected: loc.isDefault })));
  };

  const refetch = async () => {
    const data = await fetchICPConfig();
    setIcpConfig(data);
    setIndustries((prev) => {
      const prevSel: Record<string, boolean> = Object.fromEntries(prev.map((p) => [p.id, p.selected]));
      return data.industries.map((ind) => ({ id: ind.slug, name: ind.displayName, selected: prevSel[ind.slug] ?? ind.isTarget }));
    });
    setTitles((prev) => {
      const prevSel: Record<string, boolean> = Object.fromEntries(prev.map((p) => [p.name.toLowerCase(), p.selected]));
      return data.titles.map((t, i) => ({ id: i.toString(), name: t.title, selected: prevSel[t.title.toLowerCase()] ?? t.isDefault }));
    });
    setLocations((prev) => {
      const prevSel: Record<string, boolean> = Object.fromEntries(prev.map((p) => [p.name.toLowerCase(), p.selected]));
      return data.locations.map((loc, i) => ({ id: i.toString(), name: loc.location, selected: prevSel[loc.location.toLowerCase()] ?? loc.isDefault }));
    });
  };

  useEffect(() => {
    fetchICPConfig()
      .then(mapIcpToState)
      .catch(() => setError('Failed to load ICP config. Is the backend running?'))
      .finally(() => setLoading(false));
  }, []);

  // ── Toggles ───────────────────────────────────────────────────

  const toggleIndustry = (id: string) =>
    setIndustries((prev) => prev.map((i) => (i.id === id ? { ...i, selected: !i.selected } : i)));
  const toggleTitle = (id: string) =>
    setTitles((prev) => prev.map((t) => (t.id === id ? { ...t, selected: !t.selected } : t)));
  const toggleLocation = (id: string) =>
    setLocations((prev) => prev.map((l) => (l.id === id ? { ...l, selected: !l.selected } : l)));

  // ── Add helpers ───────────────────────────────────────────────

  const handleAddIndustry = async () => {
    const name = newIndustryName.trim();
    if (!name) return;
    setAddingIndustry(true);
    try {
      await addIndustry(name);
      await refetch();
      setIndustries((prev) => prev.map((i) => (i.name.toLowerCase() === name.toLowerCase() ? { ...i, selected: true } : i)));
      setNewIndustryName('');
    } catch (e: any) {
      alert('Failed to add industry: ' + e.message);
    } finally {
      setAddingIndustry(false);
    }
  };

  const handleAddTitle = async () => {
    const t = newTitle.trim();
    if (!t) return;
    setAddingTitle(true);
    try {
      await addTitle(t);
      await refetch();
      setTitles((prev) => prev.map((x) => (x.name.toLowerCase() === t.toLowerCase() ? { ...x, selected: true } : x)));
      setNewTitle('');
    } catch (e: any) {
      alert('Failed to add title: ' + e.message);
    } finally {
      setAddingTitle(false);
    }
  };

  const handleAddLocation = async () => {
    const loc = newLocation.trim();
    if (!loc) return;
    setAddingLocation(true);
    try {
      await addLocation(loc);
      await refetch();
      setLocations((prev) => prev.map((x) => (x.name.toLowerCase() === loc.toLowerCase() ? { ...x, selected: true } : x)));
      setNewLocation('');
    } catch (e: any) {
      alert('Failed to add location: ' + e.message);
    } finally {
      setAddingLocation(false);
    }
  };

  // ── Start Run ─────────────────────────────────────────────────

  const handleStartRun = async () => {
    const selectedTitles    = titles.filter((t) => t.selected).map((t) => t.name);
    const selectedLocations = locations.filter((l) => l.selected).map((l) => l.name);
    const selectedIndustries = industries.filter((i) => i.selected).map((i) => i.name);

    if (selectedIndustries.length === 0) { alert('Please select at least one target industry.'); return; }

    setStartingRun(true);
    setError(null);
    setLaunchSummary({
      industries: selectedIndustries.length,
      titles: selectedTitles.length,
      locations: selectedLocations.length,
    });
    setLaunchPhase('starting');

    // Pace the visual transitions. Each phase has a minimum display time so
    // the user sees the work happen instead of getting yanked across screens.
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

    try {
      // Fire the API and a minimum-display delay in parallel — whichever
      // takes longer wins, so a fast API doesn't make the spinner flash.
      const [result] = await Promise.all([
        startRun({
          title: `Run (LinkedIn) — ${new Date().toLocaleDateString()}`,
          source: 'jobspy',
          runConfig: {
            searchTitles: selectedTitles,
            searchLocations: selectedLocations,
            targetIndustries: selectedIndustries,
            customIndustries: [],
            hoursOld: parseInt(maxPostingAge) || 24,
            resultsPerSearch: parseInt(resultsPerBatch) || 50,
            siteName: ['linkedin'],
            icpConfigSnapshot: icpConfig ? { version: icpConfig.version } : null,
          },
        }),
        sleep(900),
      ]);
      const newId = result.id || result._id;

      setLaunchPhase('created');
      await sleep(550);
      setLaunchPhase('opening');
      await sleep(450);

      router.push(newId ? `/runs/${newId}` : '/runs');
    } catch (e: any) {
      setLaunchPhase('idle');
      setError('Failed to start run: ' + e.message);
    } finally {
      setStartingRun(false);
    }
  };

  // ── Derived / filtered ────────────────────────────────────────

  const filteredTitles = titles.filter((t) => {
    const matchesSearch = t.name.toLowerCase().includes(titleSearch.toLowerCase());
    return matchesSearch && (showSelectedOnly ? t.selected : true);
  });
  const selectedTitleCount = titles.filter((t) => t.selected).length;

  const filteredLocations = locations.filter((l) => {
    const matchesSearch = l.name.toLowerCase().includes(locationSearch.toLowerCase());
    return matchesSearch && (showSelectedLocOnly ? l.selected : true);
  });
  const selectedLocCount = locations.filter((l) => l.selected).length;

  // ── Loading / error screens ───────────────────────────────────

  if (loading) {
    return (
      <>
        <TopBar titleNode={<BackLink />} showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-muted)' }}>
          <div style={{ textAlign: 'center' }}>
            <Icon name="loader" size={24} />
            <div style={{ marginTop: 12, fontSize: 14 }}>Loading ICP config...</div>
          </div>
        </div>
      </>
    );
  }

  if (error && !icpConfig) {
    return (
      <>
        <TopBar titleNode={<BackLink />} showSearch={false} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ textAlign: 'center', maxWidth: 360 }}>
            <Icon name="alert-circle" size={32} style={{ color: 'var(--status-danger)', marginBottom: 12 }} />
            <div style={{ fontSize: 14, color: 'var(--fg-secondary)', marginBottom: 16 }}>{error}</div>
          </div>
        </div>
      </>
    );
  }

  // ── Main render ───────────────────────────────────────────────

  return (
    <>
      <TopBar titleNode={<BackLink />} showSearch={false} />

      <div style={{ flex: 1, overflow: 'auto', padding: 24, paddingBottom: 88 }}>
        {error && (
          <div style={{ marginBottom: 16, padding: '10px 14px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: 8, fontSize: 13, color: '#B91C1C' }}>
            {error}
          </div>
        )}

        {/* Two-column layout: left ~60% / right ~40% */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 420px', gap: 20 }}>

          {/* ── LEFT COLUMN ──────────────────────────────────── */}
          <div>

            {/* Target Industries */}
            <div style={sectionCard}>
              <div style={sectionTitle}>
                <Icon name="factory" size={16} style={{ color: 'var(--status-info)' }} />
                Target Industries
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-muted)', fontWeight: 400 }}>
                  {industries.filter((i) => i.selected).length} selected
                </span>
              </div>

              {/* Chip toggles */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
                {industries.map((ind) => (
                  <button
                    key={ind.id}
                    onClick={() => toggleIndustry(ind.id)}
                    style={{
                      ...chipBase,
                      borderColor: ind.selected ? 'var(--primary)' : 'var(--border-card)',
                      background: ind.selected ? 'var(--primary)' : '#FAFAFA',
                      color: ind.selected ? '#FFF' : 'var(--fg-secondary)',
                    }}
                  >
                    {ind.name}
                    {ind.selected && <Icon name="x" size={12} />}
                  </button>
                ))}
                {industries.length === 0 && (
                  <span style={{ fontSize: 13, color: 'var(--fg-muted)' }}>No industries configured yet.</span>
                )}
              </div>

              {/* Add industry — name only (description removed) */}
              <div style={{ borderTop: '1px solid var(--border-card)', paddingTop: 12, display: 'flex', gap: 8 }}>
                <input
                  style={inputStyle}
                  value={newIndustryName}
                  onChange={(e) => setNewIndustryName(e.target.value)}
                  placeholder="Industry name (e.g. AI Research Labs)..."
                  onKeyDown={(e) => e.key === 'Enter' && handleAddIndustry()}
                />
                <button
                  onClick={handleAddIndustry}
                  disabled={addingIndustry || !newIndustryName.trim()}
                  style={{ ...btnAdd, opacity: addingIndustry || !newIndustryName.trim() ? 0.5 : 1 }}
                >
                  {addingIndustry ? <Icon name="loader" size={14} /> : <Icon name="plus" size={14} />}
                  Add
                </button>
              </div>
            </div>

            {/* Executive Search Titles */}
            <div style={sectionCard}>
              <div style={sectionTitle}>
                <Icon name="badge-check" size={16} style={{ color: 'var(--status-info)' }} />
                Executive Search Titles
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-muted)', fontWeight: 400 }}>
                  {selectedTitleCount} / {titles.length} selected
                </span>
              </div>

              {/* Search + controls */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <input
                  style={{ ...inputStyle, flex: 1, minWidth: 0 }}
                  value={titleSearch}
                  onChange={(e) => setTitleSearch(e.target.value)}
                  placeholder="Search titles..."
                />
                <button onClick={() => setShowSelectedOnly((p) => !p)} style={ctrlBtn}>
                  {showSelectedOnly ? 'Show All' : 'Selected Only'}
                </button>
                <button onClick={() => setTitles((prev) => prev.map((t) => ({ ...t, selected: true })))} style={ctrlBtn}>
                  Select All
                </button>
                <button onClick={() => setTitles((prev) => prev.map((t) => ({ ...t, selected: false })))} style={ctrlBtn}>
                  Clear
                </button>
              </div>

              {/* Two-column checklist */}
              <div style={{ maxHeight: 260, overflowY: 'auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {filteredTitles.map((title) => (
                  <label
                    key={title.id}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px',
                      borderRadius: 6, cursor: 'pointer',
                      background: title.selected ? '#F0F7FF' : '#FAFAFA',
                      border: `1px solid ${title.selected ? '#BFDBFE' : 'transparent'}`,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={title.selected}
                      onChange={() => toggleTitle(title.id)}
                      style={{ width: 14, height: 14, accentColor: 'var(--fg-primary)', cursor: 'pointer', flexShrink: 0 }}
                    />
                    <span style={{ fontSize: 13, fontWeight: 500, color: title.selected ? 'var(--fg-primary)' : 'var(--fg-muted)' }}>
                      {title.name}
                    </span>
                  </label>
                ))}
                {filteredTitles.length === 0 && (
                  <div style={{ gridColumn: '1 / -1', padding: '20px 0', textAlign: 'center', fontSize: 13, color: 'var(--fg-muted)' }}>
                    No titles match your filter.
                  </div>
                )}
              </div>

              {/* Add custom title */}
              <div style={{ borderTop: '1px solid var(--border-card)', paddingTop: 12, marginTop: 12, display: 'flex', gap: 8 }}>
                <input
                  style={inputStyle}
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Add custom title..."
                  onKeyDown={(e) => e.key === 'Enter' && handleAddTitle()}
                />
                <button
                  onClick={handleAddTitle}
                  disabled={addingTitle || !newTitle.trim()}
                  style={{ ...btnAdd, opacity: addingTitle || !newTitle.trim() ? 0.5 : 1 }}
                >
                  {addingTitle ? <Icon name="loader" size={14} /> : <Icon name="plus" size={14} />}
                </button>
              </div>
            </div>

            {/* Scraper Mode — Naukri option hidden for now */}
            <div style={sectionCard}>
              <div style={sectionTitle}>
                <Icon name="play" size={16} style={{ color: 'var(--status-info)' }} />
                Scraper Mode &amp; Source
              </div>
              <div style={{ display: 'flex', gap: 12, marginBottom: 4 }}>
                {/* LinkedIn / JobSpy — always active */}
                <button style={{ ...sourceCard(true), flex: 'none', width: '100%', maxWidth: 360 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>LinkedIn / JobSpy</span>
                    <span style={{
                      width: 16, height: 16, borderRadius: 9999,
                      border: '2px solid var(--fg-primary)',
                      background: 'var(--primary)',
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <Icon name="check" size={10} style={{ color: '#FFF' }} />
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                    Search professional networks via JobSpy. Best for US/EU.
                  </div>
                </button>

                {/* Naukri — hidden from UI for now
                <button key="naukri" onClick={() => toggleSource('naukri')} style={sourceCard(activeSources.includes('naukri'))}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>Naukri.com</span>
                    <span style={{ ... }}>...</span>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Scrape Naukri India listings via Firecrawl.</div>
                </button>
                */}
              </div>
            </div>

            {/* Pipeline Configuration — compact inputs */}
            <div style={sectionCard}>
              <div style={sectionTitle}>
                <Icon name="bar-chart-3" size={16} style={{ color: 'var(--status-info)' }} />
                Pipeline Configuration
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--fg-muted)', display: 'block', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Results per Batch
                  </label>
                  <input
                    type="number"
                    value={resultsPerBatch}
                    onChange={(e) => setResultsPerBatch(e.target.value)}
                    style={{ ...inputStyle, flex: 'none', width: '50%' }}
                  />
                  <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
                    Jobs scraped per search query.
                  </div>
                </div>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--fg-muted)', display: 'block', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Max Posting Age (hrs)
                  </label>
                  <input
                    type="number"
                    value={maxPostingAge}
                    onChange={(e) => setMaxPostingAge(e.target.value)}
                    style={{ ...inputStyle, flex: 'none', width: '50%' }}
                  />
                  <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 4 }}>
                    Only include jobs posted within this window.
                  </div>
                </div>
              </div>
            </div>

          </div>

          {/* ── RIGHT COLUMN ─────────────────────────────────── */}
          <div>

            {/* Geography — redesigned as searchable two-column checklist */}
            <div style={sectionCard}>
              <div style={sectionTitle}>
                <Icon name="map-pin" size={16} style={{ color: 'var(--status-info)' }} />
                Geography
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-muted)', fontWeight: 400 }}>
                  {selectedLocCount} / {locations.length} selected
                </span>
              </div>

              {/* Search + controls */}
              <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
                <input
                  style={{ ...inputStyle, flex: 1, minWidth: 0 }}
                  value={locationSearch}
                  onChange={(e) => setLocationSearch(e.target.value)}
                  placeholder="Search locations..."
                />
                <button onClick={() => setShowSelectedLocOnly((p) => !p)} style={ctrlBtn}>
                  {showSelectedLocOnly ? 'Show All' : 'Selected'}
                </button>
                <button onClick={() => setLocations((prev) => prev.map((l) => ({ ...l, selected: true })))} style={ctrlBtn}>
                  All
                </button>
                <button onClick={() => setLocations((prev) => prev.map((l) => ({ ...l, selected: false })))} style={ctrlBtn}>
                  Clear
                </button>
              </div>

              {/* Two-column checklist */}
              <div style={{ maxHeight: 240, overflowY: 'auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 4 }}>
                {filteredLocations.map((loc) => (
                  <label
                    key={loc.id}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px',
                      borderRadius: 6, cursor: 'pointer',
                      background: loc.selected ? '#F0F7FF' : '#FAFAFA',
                      border: `1px solid ${loc.selected ? '#BFDBFE' : 'transparent'}`,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={loc.selected}
                      onChange={() => toggleLocation(loc.id)}
                      style={{ width: 13, height: 13, accentColor: 'var(--fg-primary)', cursor: 'pointer', flexShrink: 0 }}
                    />
                    <span style={{ fontSize: 12, fontWeight: 500, color: loc.selected ? 'var(--fg-primary)' : 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {loc.name}
                    </span>
                  </label>
                ))}
                {filteredLocations.length === 0 && (
                  <div style={{ gridColumn: '1 / -1', padding: '16px 0', textAlign: 'center', fontSize: 13, color: 'var(--fg-muted)' }}>
                    {locationSearch ? 'No locations match.' : 'No locations configured yet.'}
                  </div>
                )}
              </div>

              {/* Add new location — single input row */}
              <div style={{ borderTop: '1px solid var(--border-card)', paddingTop: 12, marginTop: 6, display: 'flex', gap: 8 }}>
                <input
                  style={inputStyle}
                  value={newLocation}
                  onChange={(e) => setNewLocation(e.target.value)}
                  placeholder="Add new location..."
                  onKeyDown={(e) => e.key === 'Enter' && handleAddLocation()}
                />
                <button
                  onClick={handleAddLocation}
                  disabled={addingLocation || !newLocation.trim()}
                  style={{ ...btnAdd, opacity: addingLocation || !newLocation.trim() ? 0.5 : 1 }}
                >
                  {addingLocation ? <Icon name="loader" size={14} /> : <Icon name="plus" size={14} />}
                </button>
              </div>
            </div>

            {/* Run Summary */}
            <div style={{ ...sectionCard, background: '#FAFAFA', borderColor: 'var(--border-default)' }}>
              <div style={sectionTitle}>
                <Icon name="info" size={16} style={{ color: 'var(--fg-muted)' }} />
                Run Summary
              </div>
              <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 10, fontSize: 13 }}>
                {[
                  { label: 'Industries', value: `${industries.filter((i) => i.selected).length}`, warn: industries.filter((i) => i.selected).length === 0 },
                  { label: 'Titles', value: `${selectedTitleCount}` },
                  { label: 'Locations', value: `${selectedLocCount}` },
                  { label: 'Sources', value: 'linkedin' },
                  { label: 'Max age', value: `${maxPostingAge}h` },
                  { label: 'Batch size', value: resultsPerBatch },
                ].map(({ label, value, warn }) => (
                  <div key={label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--fg-muted)' }}>{label}</span>
                    <span style={{ fontWeight: 600, color: warn ? 'var(--status-danger)' : 'var(--fg-primary)' }}>{value}</span>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </div>
      </div>

      {/* Sticky footer */}
      <div style={{
        position: 'fixed', bottom: 0, right: 0, left: 230,
        background: '#FFFFFF', borderTop: '1px solid var(--border-default)',
        padding: '12px 28px', display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', zIndex: 40,
      }}>
        <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
          {industries.filter((i) => i.selected).length === 0
            ? '⚠ Select at least one industry to start.'
            : `${industries.filter((i) => i.selected).length} industr${industries.filter((i) => i.selected).length === 1 ? 'y' : 'ies'} · ${selectedTitleCount} title${selectedTitleCount !== 1 ? 's' : ''} · ${selectedLocCount} location${selectedLocCount !== 1 ? 's' : ''}`}
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <Link href="/runs" style={{ textDecoration: 'none' }}>
            <button style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: 'pointer', border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-secondary)', fontFamily: 'inherit' }}>
              Cancel
            </button>
          </Link>
          <button
            onClick={handleStartRun}
            disabled={startingRun || industries.filter((i) => i.selected).length === 0}
            style={{
              height: 36, padding: '0 20px', borderRadius: 6, fontSize: 13, fontWeight: 600,
              cursor: startingRun || industries.filter((i) => i.selected).length === 0 ? 'not-allowed' : 'pointer',
              border: 'none',
              background: startingRun || industries.filter((i) => i.selected).length === 0 ? '#999' : 'var(--primary)',
              color: '#FFF', fontFamily: 'inherit',
              display: 'inline-flex', alignItems: 'center', gap: 8,
            }}
          >
            {startingRun ? <Icon name="loader" size={14} /> : <Icon name="play" size={14} />}
            {startingRun ? 'Starting...' : 'Start Lead Generation'}
          </button>
        </div>
      </div>

      <LaunchOverlay phase={launchPhase} summary={launchSummary} />
    </>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// LaunchOverlay — sleek multi-phase "your run is starting" sequence.
// Phases drive a 3-step checklist; the panel fades in on mount and fades
// out cleanly just before the router push.
// ───────────────────────────────────────────────────────────────────────────

type LaunchPhase = 'idle' | 'starting' | 'created' | 'opening';

function LaunchOverlay({
  phase, summary,
}: {
  phase: LaunchPhase;
  summary: { industries: number; titles: number; locations: number } | null;
}) {
  const visible = phase !== 'idle';

  const steps: Array<{ key: LaunchPhase; label: string; sublabel: string }> = [
    { key: 'starting', label: 'Creating your run',          sublabel: 'Saving configuration & spinning up the pipeline' },
    { key: 'created',  label: 'Run created',                sublabel: 'Background workers are scraping jobs now' },
    { key: 'opening',  label: 'Opening results',            sublabel: 'You\'ll see live updates as jobs flow in' },
  ];

  const phaseOrder: LaunchPhase[] = ['starting', 'created', 'opening'];
  const currentIdx = phaseOrder.indexOf(phase as LaunchPhase);

  return (
    <div
      aria-hidden={!visible}
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(15, 23, 42, 0.55)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        opacity: visible ? 1 : 0,
        pointerEvents: visible ? 'auto' : 'none',
        transition: 'opacity 240ms ease',
      }}
    >
      <div
        style={{
          width: '92%', maxWidth: 440, background: '#FFFFFF',
          borderRadius: 16, padding: '28px 28px 24px',
          boxShadow: '0 24px 80px rgba(0,0,0,0.25)',
          transform: visible ? 'translateY(0) scale(1)' : 'translateY(8px) scale(0.98)',
          opacity: visible ? 1 : 0,
          transition: 'opacity 220ms ease, transform 260ms cubic-bezier(.2,.7,.2,1.1)',
        }}
      >
        {/* Header: animated icon + title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
          <div style={{
            width: 44, height: 44, borderRadius: 12,
            background: phase === 'opening' ? '#ECFDF5' : '#EEF2FF',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'background 240ms ease',
          }}>
            {phase === 'starting' && (
              <span style={{
                display: 'inline-block', width: 18, height: 18,
                border: '2.5px solid #C7D2FE', borderTopColor: '#4F46E5',
                borderRadius: '50%', animation: 'launchspin 0.8s linear infinite',
              }} />
            )}
            {phase === 'created' && (
              <Icon name="check" size={22} style={{ color: '#4F46E5' }} />
            )}
            {phase === 'opening' && (
              <Icon name="rocket" size={22} style={{ color: '#059669' }} />
            )}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>
              {phase === 'opening' ? 'Launching pipeline' : phase === 'created' ? 'Run created' : 'Starting your run'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>
              {summary
                ? `${summary.industries} industr${summary.industries === 1 ? 'y' : 'ies'} · ${summary.titles} title${summary.titles === 1 ? '' : 's'} · ${summary.locations} location${summary.locations === 1 ? '' : 's'}`
                : 'Preparing your pipeline'}
            </div>
          </div>
        </div>

        {/* Step checklist */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 8 }}>
          {steps.map((step, i) => {
            const done = i < currentIdx;
            const active = i === currentIdx;
            const pending = i > currentIdx;
            return (
              <div
                key={step.key}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 12px', borderRadius: 8,
                  background: active ? '#F5F3FF' : 'transparent',
                  border: `1px solid ${active ? '#DDD6FE' : 'transparent'}`,
                  transition: 'background 240ms ease, border-color 240ms ease',
                  opacity: pending ? 0.55 : 1,
                }}
              >
                <span style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  width: 22, height: 22, borderRadius: 9999, flexShrink: 0,
                  background: done ? '#059669' : active ? '#4F46E5' : '#E5E7EB',
                  color: '#FFF',
                  transition: 'background 240ms ease, transform 240ms cubic-bezier(.2,.7,.2,1.4)',
                  transform: done ? 'scale(1)' : 'scale(0.9)',
                }}>
                  {done ? (
                    <Icon name="check" size={12} />
                  ) : active ? (
                    <span style={{
                      width: 10, height: 10, borderRadius: 9999,
                      background: '#FFF', animation: 'launchpulse 1.2s ease-in-out infinite',
                    }} />
                  ) : (
                    <span style={{ fontSize: 10, fontWeight: 700, color: '#9CA3AF' }}>{i + 1}</span>
                  )}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>{step.label}</div>
                  <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{step.sublabel}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <style>{`
        @keyframes launchspin { to { transform: rotate(360deg); } }
        @keyframes launchpulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%      { opacity: 0.4; transform: scale(0.7); }
        }
      `}</style>
    </div>
  );
}
