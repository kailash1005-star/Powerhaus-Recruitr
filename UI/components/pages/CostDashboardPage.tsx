'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  fetchCostOverview, fetchCostGroup, fetchCostLineItem, fetchPriceBook, updatePriceEntry,
  type CostOverview, type CostGroup, type CostLineDetail, type CostLineItem,
  type CostInsight, type PriceBookEntry, type CostRange, type CostStage,
} from '@/lib/api';

// ── Presentation maps ───────────────────────────────────────────────────────
const SERVICE: Record<string, { label: string; color: string }> = {
  openai: { label: 'OpenAI', color: '#0E9F8E' },
  gemini: { label: 'Gemini', color: '#7C3AED' },
  apollo: { label: 'Apollo', color: '#E0891B' },
  apify: { label: 'Apify', color: '#2563EB' },
  firecrawl: { label: 'Firecrawl', color: '#EA580C' },
  smartlead: { label: 'Smartlead', color: '#DB2777' },
};
const svc = (s: string) => SERVICE[s] || { label: s, color: '#64748B' };

const STAGE: Record<string, { label: string; color: string; unit: string }> = {
  job_search: { label: 'Job discovery', color: '#EA580C', unit: 'run' },
  candidate_search: { label: 'Candidate search', color: '#E0891B', unit: 'search' },
  matching: { label: 'Matching', color: '#2563EB', unit: 'run' },
  outreach: { label: 'Outreach', color: '#DB2777', unit: 'draft' },
  company_analysis: { label: 'Company analysis', color: '#7C3AED', unit: 'run' },
  other: { label: 'Other', color: '#64748B', unit: 'item' },
};
const stg = (s?: string | null) => STAGE[s || 'other'] || STAGE.other;

const RANGES: CostRange[] = ['7d', '14d', '30d', '90d', 'all'];
const DRILLABLE = ['job_search', 'candidate_search', 'matching', 'outreach', 'company_analysis'];

function usd(v: number | null | undefined): string {
  const n = v || 0;
  if (n === 0) return '$0';
  const a = Math.abs(n);
  if (a < 0.01) return `$${n.toFixed(4)}`;
  if (a < 1) return `$${n.toFixed(2)}`;
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
function fmtQty(q: number | { in?: number; out?: number }): string {
  if (typeof q === 'number') return q.toLocaleString();
  const t = (q.in || 0) + (q.out || 0);
  return t >= 1000 ? `${(t / 1000).toFixed(1)}k` : `${t}`;
}
function fmtTime(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso); if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function searchLink(stage?: string | null, refs?: Record<string, unknown>): string | null {
  if (!refs) return null;
  if (stage === 'candidate_search' && refs.pipelineId && refs.jobId) return `/candidates/${refs.pipelineId}/jobs/${refs.jobId}`;
  if (stage === 'matching' && refs.matchRunId) return `/matching/${refs.matchRunId}`;
  return null;
}

// ── tokens ──────────────────────────────────────────────────────────────────
const ink = 'var(--fg-primary)', ink2 = 'var(--fg-secondary)', muted = 'var(--fg-muted)', line = 'var(--border-card)';
const mono: React.CSSProperties = { fontFamily: 'var(--font-mono, ui-monospace, "SF Mono", Menlo, monospace)', fontVariantNumeric: 'tabular-nums' };
const eyebrow: React.CSSProperties = { fontSize: 10, fontWeight: 700, letterSpacing: '.07em', textTransform: 'uppercase', color: muted };
const card = (x?: React.CSSProperties): React.CSSProperties => ({ background: '#FFF', border: `1px solid ${line}`, borderRadius: 14, boxShadow: '0 1px 2px rgba(17,20,38,.04), 0 4px 16px rgba(17,20,38,.04)', ...x });

function Dot({ c, size = 10 }: { c: string; size?: number }) { return <span style={{ width: size, height: size, borderRadius: 3, background: c, display: 'inline-block', flexShrink: 0 }} />; }

function StackBar({ slices, height = 18 }: { slices: { key: string; color: string; value: number }[]; height?: number }) {
  const t = slices.reduce((s, x) => s + x.value, 0) || 1;
  return <div style={{ display: 'flex', height, borderRadius: 6, overflow: 'hidden', background: '#F0F1F5' }}>
    {slices.map((s) => <span key={s.key} title={`${s.key} · ${usd(s.value)}`} style={{ width: `${Math.max(0, (s.value / t) * 100)}%`, background: s.color }} />)}
  </div>;
}

function Sparkline({ points }: { points: { date: string; cost: number }[] }) {
  if (points.length < 2) return <div style={{ fontSize: 12, color: muted, padding: '10px 0' }}>Not enough daily activity to chart yet.</div>;
  const w = 300, h = 46, pad = 4, max = Math.max(...points.map((p) => p.cost), 1e-6), step = w / (points.length - 1);
  const pts = points.map((p, i) => [i * step, h - pad - (p.cost / max) * (h - pad * 2)]);
  const path = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const [lx, ly] = pts[pts.length - 1];
  return <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" style={{ display: 'block' }}>
    <defs><linearGradient id="sk" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--primary)" stopOpacity="0.2" /><stop offset="100%" stopColor="var(--primary)" stopOpacity="0" /></linearGradient></defs>
    <path d={`${path} L${w},${h} L0,${h} Z`} fill="url(#sk)" />
    <path d={path} fill="none" stroke="var(--primary)" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    <circle cx={lx} cy={ly} r="3" fill="var(--primary)" />
  </svg>;
}

function Insights({ items }: { items?: CostInsight[] }) {
  if (!items || items.length === 0) return null;
  return <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
    {items.map((i, k) => {
      const warn = i.severity === 'warn';
      const col = warn ? 'var(--status-warning, #D97706)' : 'var(--primary)';
      return <div key={k} style={{ borderLeft: `3px solid ${col}`, background: warn ? 'color-mix(in srgb, var(--status-warning, #D97706) 8%, transparent)' : 'var(--bg-app)', borderRadius: '0 10px 10px 0', padding: '11px 14px', fontSize: 12.5, color: ink2 }}>
        <b style={{ color: ink }}>{i.title}.</b> {i.body}
      </div>;
    })}
  </div>;
}

function PanelHead({ title, hint }: { title: string; hint?: string }) {
  return <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 13 }}>
    <span style={{ fontSize: 13, fontWeight: 750, color: ink }}>{title}</span>
    {hint && <span style={{ fontSize: 11, color: muted }}>{hint}</span>}
  </div>;
}

function Stat({ label, value, sub, accent, dot }: { label: string; value: string; sub?: string; accent?: boolean; dot?: string }) {
  return <div style={{ ...card(), padding: '15px 17px' }}>
    <div style={{ ...eyebrow, display: 'inline-flex', alignItems: 'center', gap: 6 }}>{dot && <Dot c={dot} size={8} />}{label}</div>
    <div style={{ ...mono, fontWeight: 800, fontSize: 22, marginTop: 7, color: accent ? 'var(--primary)' : ink }}>{value}</div>
    {sub && <div style={{ fontSize: 11.5, color: muted, marginTop: 3 }}>{sub}</div>}
  </div>;
}

type View = { kind: 'overview' } | { kind: 'group'; stage: CostStage } | { kind: 'item'; groupKey: string };

export function CostDashboardPage() {
  const [range, setRange] = useState<CostRange>('30d');
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [overview, setOverview] = useState<CostOverview | null>(null);
  const [groupData, setGroupData] = useState<CostGroup | null>(null);
  const [itemData, setItemData] = useState<CostLineDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ratesOpen, setRatesOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      if (view.kind === 'overview') setOverview(await fetchCostOverview(range));
      else if (view.kind === 'group') setGroupData(await fetchCostGroup(view.stage, range));
      else setItemData(await fetchCostLineItem(view.groupKey));
    } catch (e: any) { setError(e?.message || 'Failed to load cost data'); }
    finally { setLoading(false); }
  }, [view, range]);
  useEffect(() => { load(); }, [load]);

  const openItem = (gk: string) => setView({ kind: 'item', groupKey: gk });
  const openGroup = (s: string) => { if (DRILLABLE.includes(s)) setView({ kind: 'group', stage: s as CostStage }); };

  return (
    <>
      <TopBar title="Cost Analyser" showSearch={false}
        actions={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ display: 'inline-flex', gap: 2, background: 'var(--bg-app)', border: `1px solid ${line}`, borderRadius: 9, padding: 3 }}>
              {RANGES.map((r) => (
                <button key={r} onClick={() => setRange(r)} style={{ fontSize: 12, fontWeight: 600, padding: '5px 11px', borderRadius: 6, cursor: 'pointer', border: 'none', fontFamily: 'inherit', background: range === r ? 'var(--primary)' : 'transparent', color: range === r ? '#FFF' : ink2 }}>{r === 'all' ? 'All' : r}</button>
              ))}
            </div>
            <button onClick={() => setRatesOpen(true)} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 32, padding: '0 12px', borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', border: `1px solid ${line}`, background: '#FFF', color: ink2 }}>
              <Icon name="sliders-horizontal" size={14} /> Rates
            </button>
          </div>
        }
      />
      <div style={{ flex: 1, overflow: 'auto', padding: '24px 26px 60px', background: 'var(--bg-app)' }}>
        <div style={{ maxWidth: 1080, margin: '0 auto' }}>
          {view.kind !== 'overview' && (
            <button onClick={() => setView({ kind: 'overview' })} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600, color: ink2, background: 'none', border: 'none', cursor: 'pointer', marginBottom: 16, fontFamily: 'inherit', padding: 0 }}>
              <Icon name="arrow-left" size={15} /> Overview
            </button>
          )}
          {error && <div style={{ ...card(), padding: 16, color: 'var(--status-danger)', marginBottom: 16 }}>{error}</div>}
          {loading && <div style={{ textAlign: 'center', padding: 70, color: muted }}><Icon name="loader" size={22} /><div style={{ marginTop: 10, fontSize: 13 }}>Loading cost data…</div></div>}
          {!loading && view.kind === 'overview' && overview && <Overview data={overview} onStage={openGroup} onItem={openItem} onRates={() => setRatesOpen(true)} />}
          {!loading && view.kind === 'group' && groupData && <GroupView data={groupData} onItem={openItem} />}
          {!loading && view.kind === 'item' && itemData && <ItemView data={itemData} />}
        </div>
      </div>
      {ratesOpen && <RatesModal onClose={() => { setRatesOpen(false); load(); }} />}
    </>
  );
}

// ══ OVERVIEW ════════════════════════════════════════════════════════════════
function Overview({ data: raw, onStage, onItem, onRates }: { data: CostOverview; onStage: (s: string) => void; onItem: (gk: string) => void; onRates: () => void }) {
  const d = {
    range: raw.range ?? '30d', operational: raw.operational ?? 0, deltaPct: raw.deltaPct,
    runRate: raw.runRate, fixedMonthly: raw.fixedMonthly ?? 0, apolloRate: raw.apolloRate ?? 0.2,
    unitEconomics: raw.unitEconomics ?? { sourced: 0, enriched: 0, matches: 0 } as any,
    subscriptions: raw.subscriptions ?? [], byService: raw.byService ?? [], byStage: raw.byStage ?? [],
    daily: raw.daily ?? [], insights: raw.insights ?? [], topSearches: raw.topSearches ?? [],
  };
  const ue = d.unitEconomics;
  const maxStage = Math.max(1e-6, ...d.byStage.map((s) => s.cost));
  const apollo = d.subscriptions.find((s) => s.service === 'apollo');
  const smartlead = d.subscriptions.find((s) => s.service === 'smartlead');
  const empty = d.operational === 0 && d.topSearches.length === 0;

  return (
    <>
      {/* HERO — operational-first */}
      <div style={{ ...card({ padding: 0, overflow: 'hidden' }), display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr' }}>
        <div style={{ padding: '20px 22px', borderRight: `1px solid ${line}`, background: 'linear-gradient(180deg, rgba(79,70,229,0.05), transparent)' }}>
          <div style={{ ...eyebrow, display: 'flex', alignItems: 'center', gap: 8 }}>
            Operational spend · {d.range === 'all' ? 'all time' : d.range}
            {d.deltaPct != null && <span style={{ ...mono, fontWeight: 700, fontSize: 11, color: d.deltaPct <= 0 ? 'var(--status-success)' : 'var(--status-danger)' }}>{d.deltaPct <= 0 ? '▼' : '▲'} {Math.abs(d.deltaPct)}%</span>}
          </div>
          <div style={{ ...mono, fontSize: 38, fontWeight: 800, letterSpacing: '-.02em', color: 'var(--primary)', marginTop: 6, lineHeight: 1 }}>{usd(d.operational)}</div>
          <div style={{ fontSize: 12, color: muted, marginTop: 6 }}>what you can influence — metered + Apollo credits</div>
        </div>
        <div style={{ padding: '20px 22px', borderRight: `1px solid ${line}` }}>
          <div style={eyebrow}>Projected run-rate</div>
          <div style={{ ...mono, fontWeight: 800, fontSize: 24, marginTop: 6 }}>{d.runRate != null ? `${usd(d.runRate)}` : '—'}</div>
          <div style={{ fontSize: 11.5, color: muted, marginTop: 4 }}>{d.runRate != null ? 'per month at current pace' : 'select a fixed range'}</div>
        </div>
        <div style={{ padding: '20px 22px' }}>
          <div style={eyebrow}>+ Fixed overhead</div>
          <div style={{ ...mono, fontWeight: 800, fontSize: 24, marginTop: 6 }}>{usd(d.fixedMonthly)}<span style={{ fontSize: 13, color: muted }}> /mo</span></div>
          <div style={{ fontSize: 11.5, color: muted, marginTop: 4 }}>{apollo ? 'Apollo plan' : ''}{smartlead && !smartlead.configured ? ' · Smartlead not set' : ''}</div>
        </div>
      </div>

      {/* UNIT ECONOMICS */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 1, background: line, border: `1px solid ${line}`, borderRadius: 14, overflow: 'hidden', marginTop: 14 }}>
        <UeCell label="Cost / candidate sourced" value={usd(ue.perSourced)} sub={`${(ue.sourced || 0).toLocaleString()} sourced`} />
        <UeCell label="Cost / enriched profile" value={usd(ue.perEnriched)} sub={`${(ue.enriched || 0).toLocaleString()} enriched`} />
        <UeCell label="Cost / match run" value={usd(ue.perMatch)} sub={`${(ue.matches || 0).toLocaleString()} matches`} />
        <UeCell label="Wasted enrichment" value={usd(ue.wastedEnrichmentUsd)} sub={`${(ue.enrichedRejected || 0)} enriched → rejected`} warn={(ue.enrichedRejected || 0) > 0} />
      </div>

      {empty && (
        <div style={{ ...card(), padding: '26px 22px', marginTop: 14, textAlign: 'center' }}>
          <Icon name="wallet" size={26} style={{ color: muted, marginBottom: 8 }} />
          <div style={{ fontSize: 15, fontWeight: 700, color: ink }}>No operational spend in this window yet</div>
          <div style={{ fontSize: 13, color: muted, marginTop: 4 }}>Run a search, enrichment or match — it'll appear here. Your ${usd(d.fixedMonthly)}/mo fixed plan still applies.</div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 14 }}>
        {/* by stage + trend */}
        <div style={{ ...card(), padding: '18px 20px' }}>
          <PanelHead title="Variable spend by stage" hint="click to drill →" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
            {d.byStage.length === 0 && <div style={{ fontSize: 13, color: muted }}>No variable activity yet.</div>}
            {d.byStage.map((s) => {
              const meta = stg(s.stage);
              return <button key={s.stage} onClick={() => onStage(s.stage)} style={{ background: 'none', border: 'none', padding: 0, cursor: DRILLABLE.includes(s.stage) ? 'pointer' : 'default', textAlign: 'left', fontFamily: 'inherit' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, color: ink2, display: 'inline-flex', alignItems: 'center', gap: 7 }}><Dot c={meta.color} size={8} />{meta.label}{s.count ? <span style={{ color: muted, fontWeight: 400 }}> · {s.count} {meta.unit}{s.count !== 1 ? 's' : ''}</span> : null}</span>
                  <span style={{ ...mono, fontWeight: 700 }}>{usd(s.cost)}</span>
                </div>
                <div style={{ height: 7, borderRadius: 999, background: '#F0F1F5', overflow: 'hidden' }}><span style={{ display: 'block', height: '100%', width: `${(s.cost / maxStage) * 100}%`, background: meta.color, borderRadius: 999 }} /></div>
              </button>;
            })}
          </div>
          <div style={{ ...eyebrow, marginTop: 18, marginBottom: 6 }}>Daily trend</div>
          <Sparkline points={d.daily} />
        </div>

        {/* by service + utilization */}
        <div style={{ ...card(), padding: '18px 20px' }}>
          <PanelHead title="Spend by service" hint="bill composition" />
          <StackBar slices={d.byService.map((s) => ({ key: svc(s.service).label, color: svc(s.service).color, value: s.cost }))} />
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {d.byService.map((s) => (
              <div key={s.service} style={{ display: 'grid', gridTemplateColumns: '14px 1fr auto auto', alignItems: 'center', gap: 10, fontSize: 12.5 }}>
                <Dot c={svc(s.service).color} /><span style={{ color: ink2 }}>{svc(s.service).label}</span>
                {s.fixed ? <span style={{ ...eyebrow, fontSize: 9 }}>fixed</span> : <span />}
                <span style={{ ...mono, fontWeight: 700 }}>{usd(s.cost)}</span>
              </div>
            ))}
          </div>
          <div style={{ ...eyebrow, marginTop: 18, marginBottom: 8 }}>Subscription utilisation</div>
          {apollo && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 5 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><Dot c={svc('apollo').color} size={8} /> Apollo credits</span>
                <span style={mono}>{(apollo.creditsUsed || 0).toLocaleString()}{apollo.includedCredits ? ` / ${apollo.includedCredits.toLocaleString()}` : ''}</span>
              </div>
              {apollo.includedCredits ? (
                <><div style={{ height: 9, borderRadius: 999, background: '#F0F1F5', overflow: 'hidden' }}><span style={{ display: 'block', height: '100%', width: `${Math.min(100, apollo.utilizationPct || 0)}%`, background: svc('apollo').color, borderRadius: 999 }} /></div>
                  <div style={{ fontSize: 11.5, color: muted, marginTop: 5 }}>{usd(apollo.monthlyUsd)} plan · effective <b>${(apollo.usdPerCredit ?? 0.2).toFixed(2)}</b>/used credit · {Math.round(100 - (apollo.utilizationPct || 0))}% unused</div></>
              ) : (
                <div style={{ fontSize: 11.5, color: muted, marginTop: 2 }}>{usd(apollo.monthlyUsd)} plan · <button onClick={onRates} style={{ color: 'var(--primary)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'inherit', fontSize: 11.5 }}>set your credit allowance</button> to see utilisation.</div>
              )}
            </div>
          )}
          {smartlead && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 5 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><Dot c={svc('smartlead').color} size={8} /> Smartlead</span>
                <span style={{ ...mono, color: smartlead.configured ? ink2 : 'var(--status-warning, #D97706)' }}>{smartlead.configured ? `${usd(smartlead.monthlyUsd)}/mo` : 'not configured'}</span>
              </div>
              {!smartlead.configured && <div style={{ fontSize: 11.5, color: muted }}>Set your plan in <button onClick={onRates} style={{ color: 'var(--primary)', background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'inherit', fontSize: 11.5 }}>Rates</button> to track outreach cost.</div>}
            </div>
          )}
        </div>
      </div>

      {/* INSIGHTS */}
      {d.insights.length > 0 && (
        <div style={{ ...card(), padding: '18px 20px', marginTop: 14 }}>
          <PanelHead title="Optimisation insights" hint="auto-generated" />
          <Insights items={d.insights} />
        </div>
      )}

      {/* TOP ACTIVITY */}
      <div style={{ ...card(), padding: '18px 20px', marginTop: 14 }}>
        <PanelHead title="Most expensive activity" hint="attributed · click to drill →" />
        {d.topSearches.length === 0 ? <div style={{ padding: '20px 0', textAlign: 'center', color: muted, fontSize: 13 }}>Nothing recorded in this window.</div>
          : <ActivityTable items={d.topSearches} onItem={onItem} showEfficiency />}
      </div>
    </>
  );
}

function UeCell({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return <div style={{ background: '#FFF', padding: '13px 16px' }}>
    <div style={eyebrow}>{label}</div>
    <div style={{ ...mono, fontWeight: 800, fontSize: 19, marginTop: 6, color: warn ? 'var(--status-warning, #D97706)' : ink }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: muted, marginTop: 2 }}>{sub}</div>}
  </div>;
}

function ActivityTable({ items, onItem, showEfficiency }: { items: CostLineItem[]; onItem: (gk: string) => void; showEfficiency?: boolean }) {
  return <div style={{ overflowX: 'auto' }}>
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
      <thead><tr style={eyebrow}>
        <th style={{ textAlign: 'left', padding: '8px', fontWeight: 700 }}>Search</th>
        <th style={{ textAlign: 'right', padding: '8px', fontWeight: 700 }}>Found</th>
        <th style={{ textAlign: 'right', padding: '8px', fontWeight: 700 }}>Enriched</th>
        <th style={{ padding: '8px' }}>Breakdown</th>
        <th style={{ textAlign: 'right', padding: '8px', fontWeight: 700 }}>Cost</th>
        {showEfficiency && <th style={{ textAlign: 'right', padding: '8px', fontWeight: 700 }}>$/enr.</th>}
      </tr></thead>
      <tbody>
        {items.map((it) => (
          <tr key={it.groupKey} onClick={() => onItem(it.groupKey)} style={{ cursor: 'pointer', borderTop: `1px solid #F2F3F7` }}
            onMouseEnter={(e) => (e.currentTarget.style.background = '#FAFBFD')} onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
            <td style={{ padding: '10px 8px', maxWidth: 260 }}>
              <div style={{ fontWeight: 600, color: ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label || it.groupKey}</div>
              <div style={{ fontSize: 11, color: muted, display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 1 }}>
                <Dot c={stg(it.stage).color} size={7} />{stg(it.stage).label}
                {(it.enrichedRejected || 0) > 0 && <span style={{ fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 999, background: 'color-mix(in srgb, var(--status-warning, #D97706) 15%, transparent)', color: 'var(--status-warning, #D97706)' }}>⚑ {it.enrichedRejected} wasted</span>}
              </div>
            </td>
            <td style={{ padding: '10px 8px', textAlign: 'right', ...mono, color: ink2 }}>{it.found ?? '—'}</td>
            <td style={{ padding: '10px 8px', textAlign: 'right', ...mono, color: ink2 }}>{it.enriched ?? '—'}</td>
            <td style={{ padding: '10px 8px', width: 120 }}><StackBar height={7} slices={it.byService.map((s) => ({ key: svc(s.service).label, color: svc(s.service).color, value: s.cost }))} /></td>
            <td style={{ padding: '10px 8px', textAlign: 'right', ...mono, fontWeight: 700, color: ink, whiteSpace: 'nowrap' }}>{usd(it.cost)}</td>
            {showEfficiency && <td style={{ padding: '10px 8px', textAlign: 'right', ...mono, color: muted, fontSize: 11.5 }}>{it.perEnriched != null ? usd(it.perEnriched) : '—'}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  </div>;
}

// ══ GROUP ═══════════════════════════════════════════════════════════════════
function GroupView({ data: raw, onItem }: { data: CostGroup; onItem: (gk: string) => void }) {
  const d = { stage: raw.stage, total: raw.total ?? 0, count: raw.count ?? 0, creditsUsed: raw.creditsUsed ?? 0, apolloRate: raw.apolloRate ?? 0.2, enriched: raw.enriched ?? 0, enrichedRejected: raw.enrichedRejected ?? 0, perEnriched: raw.perEnriched, insights: raw.insights ?? [], items: raw.items ?? [] };
  const st = stg(d.stage);
  return (
    <>
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 9, marginBottom: 4 }}><Dot c={st.color} size={12} /><h2 style={{ fontSize: 21, fontWeight: 750, margin: 0, color: ink }}>{st.label}</h2></div>
      <div style={{ fontSize: 13, color: muted, marginBottom: 16 }}>Every {st.unit} in this stage, with sourcing → enrich efficiency</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12, marginBottom: 14 }}>
        <Stat label="Stage total" value={usd(d.total)} sub={`${d.count} ${st.unit}${d.count !== 1 ? 's' : ''}`} accent />
        <Stat label="Avg / item" value={usd(d.count ? d.total / d.count : 0)} />
        {d.enriched > 0 && <Stat label="Cost / enriched" value={usd(d.perEnriched)} sub={`${d.enriched} enriched`} />}
        {d.enrichedRejected > 0 && <Stat label="Wasted enrichment" value={usd(d.enrichedRejected * d.apolloRate)} sub={`${d.enrichedRejected} enriched → rejected`} />}
      </div>
      {d.insights.length > 0 && <div style={{ marginBottom: 14 }}><Insights items={d.insights} /></div>}
      <div style={{ ...card(), padding: '18px 20px' }}>
        <PanelHead title={`${st.unit[0].toUpperCase()}${st.unit.slice(1)}es`} hint="sorted by cost · ⚑ = enriched then rejected" />
        {d.items.length === 0 ? <div style={{ padding: '20px 0', textAlign: 'center', color: muted, fontSize: 13 }}>Nothing recorded in this stage yet.</div>
          : <ActivityTable items={d.items} onItem={onItem} showEfficiency />}
      </div>
    </>
  );
}

// ══ LINE ITEM ═══════════════════════════════════════════════════════════════
function ItemView({ data: raw }: { data: CostLineDetail }) {
  const d = { groupKey: raw.groupKey, stage: raw.stage, label: raw.label, total: raw.total ?? 0, apolloRate: raw.apolloRate ?? 0.2, found: raw.found, enriched: raw.enriched, rejected: raw.rejected, enrichedRejected: raw.enrichedRejected, byService: raw.byService ?? [], events: raw.events ?? [], insights: raw.insights ?? [], refs: raw.refs };
  const st = stg(d.stage);
  const link = searchLink(d.stage, d.refs);
  const volLine = [d.found != null ? `${d.found} found` : null, d.enriched != null ? `${d.enriched} enriched` : null, d.rejected != null ? `${d.rejected} rejected` : null].filter(Boolean).join(' → ');
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ fontSize: 21, fontWeight: 750, margin: '0 0 4px', color: ink }}>{d.label || d.groupKey}</h2>
          <div style={{ fontSize: 13, color: muted, display: 'inline-flex', alignItems: 'center', gap: 7 }}><Dot c={st.color} size={8} />{st.label}{volLine ? ` · ${volLine}` : ''}</div>
        </div>
        {link && <Link href={link} style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--primary)', textDecoration: 'none', whiteSpace: 'nowrap' }}>Open this search's candidates →</Link>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 12, margin: '16px 0' }}>
        <Stat label="Attributed cost" value={usd(d.total)} accent />
        {d.byService.slice(0, 3).map((s) => <Stat key={s.service} label={svc(s.service).label} value={usd(s.cost)} dot={svc(s.service).color} />)}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14 }}>
        <div style={{ ...card(), padding: '18px 20px' }}>
          <PanelHead title="Activity log" hint="every billable call, in order" />
          <div style={{ fontFamily: 'var(--font-mono, ui-monospace, Menlo, monospace)', fontSize: 12, background: '#0B1020', borderRadius: 10, padding: '12px 14px', color: '#D1D5DB', lineHeight: 1.85, overflowX: 'auto' }}>
            {d.events.length === 0 ? <span style={{ color: '#6B7280' }}>No events.</span> : d.events.map((e, i) => (
              <div key={i} style={{ display: 'flex', gap: 10, whiteSpace: 'nowrap' }}>
                <span style={{ color: '#4B5563' }}>{fmtTime(e.createdAt)}</span>
                <span style={{ color: svc(e.service).color, fontWeight: 700 }}>{e.service}</span>
                <span style={{ color: '#9CA3AF' }}>{e.operation}</span>
                <span style={{ color: '#6B7280', flex: 1 }}>· {fmtQty(e.quantity)}{e.unit === 'credit' ? ' cr' : e.unit === 'profile' ? ' prof' : e.unit === 'token' ? ' tok' : ''}</span>
                <span style={{ color: '#E5E7EB', fontWeight: 700 }}>{usd(e.costUsd)}{e.allocated ? '*' : ''}</span>
              </div>
            ))}
          </div>
          {d.events.some((e) => e.allocated) && <div style={{ fontSize: 11, color: muted, marginTop: 9 }}>* Apollo attributed at ${(d.apolloRate).toFixed(2)}/credit — the plan is a flat monthly line.</div>}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ ...card(), padding: '18px 20px' }}>
            <PanelHead title="Breakdown" />
            <StackBar slices={d.byService.map((s) => ({ key: svc(s.service).label, color: svc(s.service).color, value: s.cost }))} />
            <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {d.byService.map((s) => <div key={s.service} style={{ display: 'grid', gridTemplateColumns: '14px 1fr auto', alignItems: 'center', gap: 10, fontSize: 12.5 }}><Dot c={svc(s.service).color} /><span style={{ color: ink2 }}>{svc(s.service).label}</span><span style={{ ...mono, fontWeight: 700 }}>{usd(s.cost)}</span></div>)}
            </div>
          </div>
          {d.insights.length > 0 && <Insights items={d.insights} />}
        </div>
      </div>
    </>
  );
}

// ══ RATES ═══════════════════════════════════════════════════════════════════
function RatesModal({ onClose }: { onClose: () => void }) {
  const [entries, setEntries] = useState<PriceBookEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  useEffect(() => { fetchPriceBook().then((r) => { setEntries(r.items); setLoading(false); }).catch(() => setLoading(false)); }, []);

  const keyOf = (e: PriceBookEntry) => `${e.service}:${e.model ?? ''}`;
  const getVal = (e: PriceBookEntry, f: string) => draft[`${keyOf(e)}:${f}`] ?? ((e as any)[f] ?? 0);
  const setVal = (e: PriceBookEntry, f: string, v: number) => setDraft((x) => ({ ...x, [`${keyOf(e)}:${f}`]: v }));
  const save = async (e: PriceBookEntry, fields: string[]) => {
    setSavingKey(keyOf(e));
    try { const body: any = { service: e.service, model: e.model ?? null }; fields.forEach((f) => (body[f] = getVal(e, f))); const u = await updatePriceEntry(body); setEntries((p) => p.map((x) => keyOf(x) === keyOf(e) ? { ...x, ...u } : x)); }
    catch { } finally { setSavingKey(null); }
  };
  const input = (e: PriceBookEntry, f: string, step: number, w = 84) => <input type="number" step={step} value={getVal(e, f)} onChange={(ev) => setVal(e, f, parseFloat(ev.target.value) || 0)} style={{ width: w, height: 30, padding: '0 8px', borderRadius: 6, border: `1px solid ${line}`, fontSize: 13, ...mono, boxSizing: 'border-box' }} />;
  const saveBtn = (e: PriceBookEntry, fields: string[], primary?: boolean) => <button onClick={() => save(e, fields)} disabled={savingKey === keyOf(e)} style={{ height: 30, padding: '0 13px', borderRadius: 6, border: primary ? 'none' : `1px solid ${line}`, background: primary ? 'var(--primary)' : '#FFF', color: primary ? '#FFF' : ink2, fontWeight: 600, fontSize: 12, cursor: 'pointer', fontFamily: 'inherit' }}>{savingKey === keyOf(e) ? '…' : 'Save'}</button>;

  const subs = entries.filter((e) => e.kind === 'subscription');
  const metered = entries.filter((e) => e.kind === 'metered');
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 120, padding: 20 }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ background: '#FFF', borderRadius: 14, width: '100%', maxWidth: 680, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 16px 56px rgba(0,0,0,0.28)' }}>
        <div style={{ padding: '16px 20px', borderBottom: `1px solid var(--border-default)`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div><div style={{ fontSize: 16, fontWeight: 700, color: ink }}>Cost settings</div><div style={{ fontSize: 12, color: muted }}>Your real plans &amp; metered rates — edits re-price the dashboard instantly.</div></div>
          <button onClick={onClose} style={{ width: 32, height: 32, border: 'none', background: 'transparent', cursor: 'pointer', color: muted }}><Icon name="x" size={18} /></button>
        </div>
        <div style={{ padding: 20 }}>
          {loading ? <div style={{ textAlign: 'center', padding: 30, color: muted }}><Icon name="loader" size={20} /></div> : <>
            <div style={{ ...eyebrow, marginBottom: 10 }}>Subscriptions</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 24 }}>
              {subs.map((e) => {
                const apollo = e.service === 'apollo';
                const fields = apollo ? ['monthlyUsd', 'usdPerCredit', 'includedCredits'] : ['monthlyUsd'];
                return <div key={keyOf(e)} style={{ ...card(), padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <Dot c={svc(e.service).color} /><span style={{ fontWeight: 600, flex: 1, minWidth: 80, color: ink }}>{svc(e.service).label}</span>
                  <span style={{ fontSize: 11, color: muted }}>$/mo</span>{input(e, 'monthlyUsd', 1, 84)}
                  {apollo && <><span style={{ fontSize: 11, color: muted }}>$/credit</span>{input(e, 'usdPerCredit', 0.01, 66)}<span style={{ fontSize: 11, color: muted }}>credits/mo</span>{input(e, 'includedCredits', 50, 78)}</>}
                  {saveBtn(e, fields, true)}
                </div>;
              })}
            </div>
            <div style={{ ...eyebrow, marginBottom: 10 }}>Metered rates (USD)</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {metered.map((e) => {
                const isToken = e.unit === 'token';
                const fields = isToken ? ['inUsdPer1M', 'outUsdPer1M'] : ['usdPerUnit'];
                return <div key={keyOf(e)} style={{ ...card(), padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <Dot c={svc(e.service).color} /><span style={{ fontWeight: 600, flex: 1, minWidth: 110, color: ink }}>{svc(e.service).label}{e.model && <span style={{ color: muted, fontWeight: 400 }}> · {e.model}</span>}</span>
                  {isToken ? <><span style={{ fontSize: 11, color: muted }}>in/1M</span>{input(e, 'inUsdPer1M', 0.01, 70)}<span style={{ fontSize: 11, color: muted }}>out/1M</span>{input(e, 'outUsdPer1M', 0.01, 70)}</>
                    : <><span style={{ fontSize: 11, color: muted }}>$/{e.unit}</span>{input(e, 'usdPerUnit', 0.001, 86)}</>}
                  {saveBtn(e, fields)}
                </div>;
              })}
            </div>
          </>}
        </div>
      </div>
    </div>
  );
}
