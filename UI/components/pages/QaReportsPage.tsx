'use client';

// QA Reports — operator-only instrument panel for BOTH QA auditors.
//
// Two adversarial auditors run over the system's output before a recruiter sees
// it, each reporting here:
//   • Match auditor   — re-reads every match verdict; quote-verifies evidence
//     the scorer missed (false negatives → auto-corrected) and flags credit the
//     evidence doesn't support (false positives → flagged, never auto-cut).
//   • Sourcing auditor — re-reads every discovery result set: a deterministic
//     gate rejects wrong-COUNTRY candidates (the Bavaria→India leak) and a
//     stronger model flags wrong-SPECIALTY leaks (SAP FICO in an SAP HCM search).
//
// Not client-facing: the backend 403s anyone off the ADMIN_EMAILS allowlist and
// the sidebar hides the item until /qa/access confirms. Internal telemetry.

import { useState, useEffect } from 'react';
import { TopBar } from '../TopBar';
import { Icon } from '../Icon';
import {
  fetchQaReports, fetchQaReport,
  type QaMetrics, type QaReportSummary, type QaReportDetail,
} from '@/lib/api';

const card: React.CSSProperties = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-default)',
  borderRadius: 10,
  padding: 16,
};

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'bad' | 'good' | 'warn' }) {
  const color =
    tone === 'bad' ? 'var(--status-error, #DC2626)'
    : tone === 'good' ? 'var(--status-success, #16A34A)'
    : tone === 'warn' ? '#B45309'
    : 'var(--fg-primary)';
  return (
    <div style={{ ...card, flex: 1, minWidth: 140 }}>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>{label}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    completed: { bg: '#DCFCE7', fg: '#166534', label: 'Audited' },
    skipped: { bg: '#FEF3C7', fg: '#92400E', label: 'Skipped' },
    failed: { bg: '#FEE2E2', fg: '#991B1B', label: 'Failed' },
  };
  const s = map[status] || map.failed;
  return (
    <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, background: s.bg, color: s.fg }}>
      {s.label}
    </span>
  );
}

function KindPill({ kind }: { kind: string }) {
  const sourcing = kind === 'sourcing';
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999,
      background: sourcing ? '#EDE9FE' : '#DBEAFE',
      color: sourcing ? '#5B21B6' : '#1E40AF',
    }}>
      {sourcing ? 'Sourcing' : 'Match'}
    </span>
  );
}

/** One-line summary of what the auditor did on this run, kind-aware. */
function issueSummary(r: QaReportSummary): { text: string; tone?: 'bad' | 'warn' | 'good' } {
  const m = r.metrics || ({} as QaMetrics);
  if (r.kind === 'sourcing') {
    const rej = m.locationRejected || 0;
    const flag = m.mismatchesFlagged || 0;
    if (rej === 0 && flag === 0) return { text: 'Clean', tone: 'good' };
    const parts: string[] = [];
    if (rej) parts.push(`${rej} wrong-location removed`);
    if (flag) parts.push(`${flag} off-specialty flagged`);
    return { text: parts.join(' · '), tone: rej ? 'bad' : 'warn' };
  }
  const corrected = m.fnCorrected || 0;
  const fp = m.fpFlagsRaised || 0;
  if (corrected === 0 && fp === 0) return { text: 'Clean', tone: 'good' };
  const parts: string[] = [];
  if (corrected) parts.push(`${corrected} score(s) corrected`);
  if (fp) parts.push(`${fp} false-positive flag(s)`);
  return { text: parts.join(' · '), tone: corrected ? 'bad' : 'warn' };
}

export function QaReportsPage() {
  const [totals, setTotals] = useState<(QaMetrics & { runs: number }) | null>(null);
  const [sourcingTotals, setSourcingTotals] = useState<{ runs: number; kept: number; locationRejected: number; mismatchesFlagged: number } | null>(null);
  const [reports, setReports] = useState<QaReportSummary[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<QaReportDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchQaReports()
      .then((r) => { setTotals(r.totals); setSourcingTotals(r.sourcingTotals); setReports(r.reports); })
      .catch((e) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, []);

  // Only match reports have a fetched detail (perCandidate). Sourcing detail
  // (flags) ships inline on the summary, so no second request is needed.
  useEffect(() => {
    const row = reports.find((r) => r.id === openId);
    if (!openId || !row || row.kind === 'sourcing') { setDetail(null); return; }
    fetchQaReport(openId).then(setDetail).catch(() => setDetail(null));
  }, [openId, reports]);

  const forbidden = error && /403/.test(error);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <TopBar title="QA Reports" />
      <div style={{ flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>

        <div style={{ fontSize: 12, color: 'var(--fg-muted)', maxWidth: 760 }}>
          Internal instrument — two adversarial auditors verify the system before a recruiter sees results.
          <b> Match auditor</b>: quote-verifies evidence the scorer missed (auto-corrects) and flags unsupported credit.
          <b> Sourcing auditor</b>: a deterministic gate removes wrong-country candidates; a stronger model flags wrong-specialty leaks.
          Visible to admins only.
        </div>

        {forbidden ? (
          <div style={{ ...card, color: 'var(--fg-muted)' }}>
            <Icon name="lock" size={14} style={{ marginRight: 6 }} />
            Admin access required. Ask an operator to add your email to <code>ADMIN_EMAILS</code>.
          </div>
        ) : error ? (
          <div style={{ ...card, color: 'var(--status-error, #DC2626)' }}>Failed to load: {error}</div>
        ) : loading ? (
          <div style={{ ...card, color: 'var(--fg-muted)' }}>Loading…</div>
        ) : (
          <>
            {totals && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--fg-muted)', letterSpacing: '0.04em', margin: '0 0 6px 2px' }}>Match auditor</div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <Stat label="Runs audited" value={totals.runs} />
                  <Stat label="Verdicts reviewed" value={totals.candidatesReviewed} />
                  <Stat label="False negatives caught" value={totals.fnFlagsVerified} tone={totals.fnFlagsVerified > 0 ? 'bad' : 'good'} />
                  <Stat label="Scores corrected" value={totals.fnCorrected} tone={totals.fnCorrected > 0 ? 'warn' : 'good'} />
                  <Stat label="False-positive flags" value={totals.fpFlagsRaised} tone={totals.fpFlagsRaised > 0 ? 'warn' : 'good'} />
                  <Stat label="Auditor flags discarded" value={totals.fnFlagsDiscarded} />
                </div>
              </div>
            )}
            {sourcingTotals && (
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--fg-muted)', letterSpacing: '0.04em', margin: '0 0 6px 2px' }}>Sourcing auditor</div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <Stat label="Searches audited" value={sourcingTotals.runs} />
                  <Stat label="Candidates kept" value={sourcingTotals.kept} />
                  <Stat label="Wrong-location removed" value={sourcingTotals.locationRejected} tone={sourcingTotals.locationRejected > 0 ? 'bad' : 'good'} />
                  <Stat label="Off-specialty flagged" value={sourcingTotals.mismatchesFlagged} tone={sourcingTotals.mismatchesFlagged > 0 ? 'warn' : 'good'} />
                </div>
              </div>
            )}

            <div style={{ ...card, padding: 0, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--bg-app)', textAlign: 'left' }}>
                    {['When', 'Type', 'Role', 'Status', 'What the auditor did', ''].map((h) => (
                      <th key={h} style={{ padding: '10px 12px', fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {reports.length === 0 && (
                    <tr><td colSpan={6} style={{ padding: 20, color: 'var(--fg-muted)' }}>
                      No audited runs yet — run a search or a match and its QA report lands here.
                    </td></tr>
                  )}
                  {reports.map((r) => {
                    const open = openId === r.id;
                    const issue = issueSummary(r);
                    return (
                      <>
                        <tr
                          key={r.id}
                          onClick={() => setOpenId(open ? null : r.id)}
                          style={{ borderTop: '1px solid var(--border-default)', cursor: 'pointer', background: open ? 'var(--bg-app)' : undefined }}
                        >
                          <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>{fmtTime(r.createdAt)}</td>
                          <td style={{ padding: '10px 12px' }}><KindPill kind={r.kind} /></td>
                          <td style={{ padding: '10px 12px', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.jdTitle || r.matchRunId || '—'}</td>
                          <td style={{ padding: '10px 12px' }}><StatusPill status={r.status} /></td>
                          <td style={{ padding: '10px 12px', fontWeight: 600, color: issue.tone === 'bad' ? '#DC2626' : issue.tone === 'warn' ? '#B45309' : issue.tone === 'good' ? '#16A34A' : undefined }}>{issue.text}</td>
                          <td style={{ padding: '10px 12px' }}>
                            <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} style={{ color: 'var(--fg-subtle)' }} />
                          </td>
                        </tr>
                        {open && (
                          <tr key={`${r.id}-detail`}>
                            <td colSpan={6} style={{ padding: '12px 16px', background: 'var(--bg-app)', borderTop: '1px solid var(--border-default)' }}>
                              {r.kind === 'sourcing' ? (
                                <SourcingDetail r={r} />
                              ) : (
                                <MatchDetail r={r} detail={detail?.id === r.id ? detail : null} />
                              )}
                            </td>
                          </tr>
                        )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SourcingDetail({ r }: { r: QaReportSummary }) {
  const m = r.metrics || ({} as QaMetrics);
  const flags = r.flags || [];
  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 8 }}>
        {m.kept ?? 0} kept · {m.locationRejected ?? 0} removed for wrong location (deterministic) ·{' '}
        {m.mismatchesFlagged ?? 0} flagged as off-specialty{typeof m.lowConfidenceNoted === 'number' && m.lowConfidenceNoted > 0 ? ` · ${m.lowConfidenceNoted} low-confidence noted` : ''}
      </div>
      {flags.length === 0 ? (
        <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>No specialty mismatches flagged on this search.</div>
      ) : (
        flags.map((f, i) => (
          <div key={i} style={{ ...card, marginBottom: 8, padding: 12 }}>
            <div style={{ fontSize: 12 }}>
              <span style={{ color: '#B45309', fontWeight: 600 }}>Off-specialty</span>
              {f.likelyActualSpecialty && <span> — likely <b>{f.likelyActualSpecialty}</b></span>}
              <span style={{ color: 'var(--fg-subtle)' }}> · {Math.round((f.confidence || 0) * 100)}% conf</span>
            </div>
            {f.reason && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 2 }}>{f.reason}</div>}
          </div>
        ))
      )}
    </div>
  );
}

function MatchDetail({ r, detail }: { r: QaReportSummary; detail: QaReportDetail | null }) {
  return (
    <div>
      {r.scoreCorrections.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--fg-muted)', marginBottom: 6 }}>Score corrections</div>
          {r.scoreCorrections.map((c) => (
            <div key={c.candidateId} style={{ fontSize: 13, padding: '4px 0' }}>
              <b>{c.fullName || c.candidateId}</b>{' '}
              <span style={{ color: '#DC2626', textDecoration: 'line-through' }}>{c.from}</span>
              {' → '}
              <span style={{ color: '#16A34A', fontWeight: 700 }}>{c.to}</span>
              <span style={{ color: 'var(--fg-muted)' }}> · evidence verified for: {c.skills.join(', ')}</span>
            </div>
          ))}
        </div>
      )}
      {detail ? (
        detail.perCandidate.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>No flags on this run — every verdict survived the audit.</div>
        ) : (
          detail.perCandidate.map((pc) => (
            <div key={pc.candidateId} style={{ ...card, marginBottom: 8, padding: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{pc.fullName || pc.candidateId}</div>
              {pc.falseNegativesVerified.map((f, i) => (
                <div key={`fn-${i}`} style={{ fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#DC2626', fontWeight: 600 }}>FN verified</span> — <b>{f.skill}</b>
                  {f.quote && <span style={{ color: 'var(--fg-muted)' }}> · “{f.quote}”</span>}
                  {f.why && <div style={{ color: 'var(--fg-muted)', marginLeft: 12 }}>{f.why}</div>}
                </div>
              ))}
              {pc.falsePositives.map((f, i) => (
                <div key={`fp-${i}`} style={{ fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#B45309', fontWeight: 600 }}>FP flag</span> — <b>{f.skill}</b>
                  {f.why && <span style={{ color: 'var(--fg-muted)' }}> · {f.why}</span>}
                </div>
              ))}
              {pc.falseNegativesDiscarded.length > 0 && (
                <div style={{ fontSize: 12, color: 'var(--fg-subtle)' }}>
                  {pc.falseNegativesDiscarded.length} auditor flag(s) discarded — quote failed verification.
                </div>
              )}
            </div>
          ))
        )
      ) : (
        <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Loading details…</div>
      )}
    </div>
  );
}
