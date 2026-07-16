'use client';

import { useState, useEffect } from 'react';
import { Icon } from '../Icon';
import {
  draftOutreach, enrollOutreach, cvDownloadUrl,
  type MatchedCandidate, type ScoreBreakdown as ScoreBreakdownData, type SkillEvidence,
} from '@/lib/api';

// ── Shared styles ─────────────────────────────────────────────────────────────
export const card: React.CSSProperties = {
  background: '#FFF', border: '1px solid var(--border-card)', borderRadius: 10, padding: 20,
};
export const label: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
};
export const primaryBtn = (disabled: boolean): React.CSSProperties => ({
  height: 38, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600,
  cursor: disabled ? 'not-allowed' : 'pointer', border: 'none',
  background: disabled ? 'var(--fg-subtle)' : 'var(--primary)', color: '#FFF',
  fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 8, opacity: disabled ? 0.7 : 1,
});

export function fmtRunDate(d?: string | null): string {
  if (!d) return '';
  return new Date(d).toLocaleString('en-CA', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// ── Score bar ─────────────────────────────────────────────────────────────────
export function ScoreBar({ value }: { value: number }) {
  const color = value >= 75 ? 'var(--status-success)' : value >= 50 ? 'var(--status-info)' : 'var(--status-warning)';
  return (
    <div style={{ width: '100%', height: 6, background: 'var(--bg-app)', borderRadius: 9999, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', background: color }} />
    </div>
  );
}

// ── Score breakdown ("why this score") ────────────────────────────────────────
const SKILL_METHOD_STYLE: Record<SkillEvidence['method'], { bg: string; fg: string; text: string }> = {
  exact:    { bg: '#DCFCE7', fg: '#166534', text: 'exact' },
  specific: { bg: '#DCFCE7', fg: '#166534', text: 'covered' },
  fuzzy:    { bg: '#FEF9C3', fg: '#854D0E', text: 'close' },
  broader:  { bg: '#FFEDD5', fg: '#9A3412', text: 'broader' },
  none:     { bg: '#FEE2E2', fg: '#991B1B', text: 'missing' },
};

function SkillEvidenceRow({ e }: { e: SkillEvidence }) {
  const s = SKILL_METHOD_STYLE[e.method] || SKILL_METHOD_STYLE.none;
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '5px 0', borderTop: '1px solid var(--border-default)' }}>
      <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-primary)', minWidth: 150 }}>{e.skill}</span>
      <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 9999, background: s.bg, color: s.fg, textTransform: 'uppercase', letterSpacing: '0.04em', flexShrink: 0 }}>
        {s.text}
      </span>
      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-secondary)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
        {e.credit.toFixed(2)}
      </span>
      <span style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.5 }}>{e.note}</span>
    </div>
  );
}

/** The full audit of one candidate's number: every component, its weight, the
 *  points it won, the points it lost, and the per-skill evidence underneath. */
export function ScoreBreakdown({ bd }: { bd: ScoreBreakdownData }) {
  return (
    <div style={{ marginTop: 12, padding: 14, background: 'var(--bg-app)', borderRadius: 8, border: '1px solid var(--border-default)' }}>
      <div style={{ ...label, marginBottom: 10 }}>How this score was built</div>

      {bd.components.map((c) => {
        const na = !c.applicable;
        return (
          <div key={c.key} style={{ marginBottom: 12, opacity: na ? 0.6 : 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>
                {c.label}
                {na && (
                  <span style={{ fontSize: 10, fontWeight: 700, marginLeft: 7, padding: '1px 6px', borderRadius: 4, background: 'var(--border-default)', color: 'var(--fg-muted)', textTransform: 'uppercase' }}>
                    not stated in JD
                  </span>
                )}
              </span>
              <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--fg-secondary)', flexShrink: 0 }}>
                {na ? (
                  <em style={{ color: 'var(--fg-muted)' }}>0 of 0 pts</em>
                ) : (
                  <>
                    <strong style={{ color: 'var(--fg-primary)' }}>{c.points.toFixed(1)}</strong>
                    {' / '}{c.maxPoints.toFixed(1)} pts
                    {c.lost > 0.05 && (
                      <span style={{ color: 'var(--status-danger)', marginLeft: 6 }}>−{c.lost.toFixed(1)}</span>
                    )}
                  </>
                )}
              </span>
            </div>

            {!na && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '5px 0 4px' }}>
                <div style={{ flex: 1 }}><ScoreBar value={c.value} /></div>
                <span style={{ fontSize: 11, color: 'var(--fg-muted)', fontVariantNumeric: 'tabular-nums', minWidth: 96, textAlign: 'right' }}>
                  {c.value.toFixed(1)} × {(c.weight * 100).toFixed(1)}%
                </span>
              </div>
            )}

            <div style={{ fontSize: 12, color: 'var(--fg-muted)', lineHeight: 1.55 }}>{c.note}</div>

            {c.skills && c.skills.length > 0 && (
              <div style={{ marginTop: 6 }}>
                {c.skills.map((e, i) => <SkillEvidenceRow key={`${e.skill}-${i}`} e={e} />)}
              </div>
            )}
          </div>
        );
      })}

      <div style={{ borderTop: '1px solid var(--border-card)', paddingTop: 10, marginTop: 4 }}>
        <div style={{ fontSize: 12, color: 'var(--fg-secondary)', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>
          {bd.formula}
        </div>
        {bd.cappedBy && (
          <div style={{ marginTop: 8, padding: '7px 10px', borderRadius: 6, background: '#FEF3C7', color: '#92400E', fontSize: 12, lineHeight: 1.5 }}>
            <strong>Capped at {bd.ceiling}.</strong> {bd.cappedBy} The uncapped score was {bd.base.toFixed(1)}.
          </div>
        )}
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--fg-subtle)' }}>
          Cosine similarity {bd.similarity.toFixed(4)} · scoring {bd.version}
        </div>
      </div>
    </div>
  );
}

// ── Candidate result card ─────────────────────────────────────────────────────
export function CandidateCard({ c, rank, onReachOut, onOpen }: {
  c: MatchedCandidate; rank: number;
  onReachOut: (c: MatchedCandidate) => void;
  /** When set (pipeline candidates), the card opens the deep-profile slide-over. */
  onOpen?: (c: MatchedCandidate) => void;
}) {
  const [showWhy, setShowWhy] = useState(false);
  const contact = c.contact || {};
  const clickable = !!onOpen;
  return (
    <div
      onClick={clickable ? () => onOpen!(c) : undefined}
      style={{
        ...card, marginBottom: 14,
        cursor: clickable ? 'pointer' : 'default',
        transition: 'box-shadow 120ms, border-color 120ms',
      }}
      onMouseEnter={clickable ? (e) => { (e.currentTarget as HTMLElement).style.boxShadow = '0 4px 16px rgba(0,0,0,0.08)'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--primary)'; } : undefined}
      onMouseLeave={clickable ? (e) => { (e.currentTarget as HTMLElement).style.boxShadow = 'none'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-card)'; } : undefined}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div style={{
            width: 30, height: 30, borderRadius: 8, background: 'var(--primary)', color: '#FFF',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700, flexShrink: 0,
          }}>{rank}</div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              {c.fullName || 'Unnamed candidate'}
              {clickable && <Icon name="arrow-up-right" size={14} style={{ color: 'var(--fg-muted)' }} />}
            </div>
            <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>
              {c.currentTitle || '—'}{c.location ? ` · ${c.location}` : ''}
            </div>
          </div>
        </div>
        <div style={{ textAlign: 'right', minWidth: 70 }}>
          <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--fg-primary)', lineHeight: 1 }}>{c.score}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>match</div>
          {c.breakdown?.cappedBy && (
            <div title={c.breakdown.cappedBy} style={{ marginTop: 5, fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: '#FEF3C7', color: '#92400E', whiteSpace: 'nowrap' }}>
              capped
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, margin: '14px 0' }}>
        {Object.entries(c.subscores || {}).map(([k, v]) => {
          // A component the JD never stated scores 100 but earns nothing — show it
          // as excluded rather than as a full green bar the reader would read as merit.
          const comp = c.breakdown?.components.find((x) => x.key === k);
          const na = comp ? !comp.applicable : false;
          return (
            <div key={k} style={{ opacity: na ? 0.45 : 1 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-muted)', textTransform: 'capitalize' }}>{k.replace(/([A-Z])/g, ' $1')}</span>
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-secondary)' }}>{na ? 'n/a' : Math.round(v)}</span>
              </div>
              <ScoreBar value={na ? 0 : v} />
            </div>
          );
        })}
      </div>

      {c.reasons?.length > 0 && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.6 }}>
          {c.reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      {/* Missing = nothing evidences it. Partial = related evidence exists. Keeping
          them apart is the whole point: a 92%-similar skill listed under "Gaps"
          contradicts the breakdown directly below it. */}
      {c.gaps?.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--status-danger)' }}>
          <strong>Missing:</strong> {c.gaps.join('; ')}
        </div>
      )}
      {c.partial && c.partial.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 12, color: '#9A3412' }}>
          <strong>Partially met:</strong>{' '}
          {c.partial.map((p) => `${p.skill} (via ${p.via ?? '—'})`).join('; ')}
        </div>
      )}

      {c.breakdown && (
        <div onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => setShowWhy((s) => !s)}
            style={{
              marginTop: 10, background: 'none', border: 'none', padding: 0, cursor: 'pointer',
              fontFamily: 'inherit', fontSize: 12, fontWeight: 600, color: 'var(--primary)',
              display: 'inline-flex', alignItems: 'center', gap: 5,
            }}
          >
            <Icon name={showWhy ? 'chevron-up' : 'chevron-down'} size={13} />
            {showWhy ? 'Hide scoring detail' : 'Why this score?'}
          </button>
          {showWhy && <ScoreBreakdown bd={c.breakdown} />}
        </div>
      )}

      <div onClick={(e) => e.stopPropagation()} style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-default)', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center', fontSize: 13 }}>
        {contact.email && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="mail" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.email}</span>}
        {contact.phone && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><Icon name="phone" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.phone}</span>}
        {!contact.email && !contact.phone && <span style={{ color: 'var(--fg-subtle)' }}>No contact details parsed</span>}
        <div style={{ flex: 1 }} />
        {c.source !== 'pipeline' ? (
          <a
            href={cvDownloadUrl(c.candidateId)}
            download
            title="Download this candidate's CV"
            style={{
              height: 32, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: 'pointer', border: '1px solid var(--border-card)', textDecoration: 'none',
              background: '#FFF', color: 'var(--fg-secondary)',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}
          >
            <Icon name="download" size={14} />Download CV
          </a>
        ) : (
          contact.linkedin && (
            <a
              href={contact.linkedin as string}
              target="_blank"
              rel="noopener noreferrer"
              title="Open LinkedIn profile"
              style={{
                height: 32, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                cursor: 'pointer', border: '1px solid var(--border-card)', textDecoration: 'none',
                background: '#FFF', color: 'var(--fg-secondary)',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              <Icon name="linkedin" size={14} />LinkedIn
            </a>
          )
        )}
        <button
          onClick={() => onReachOut(c)}
          disabled={!contact.email}
          title={contact.email ? 'Draft an outreach email' : 'No email parsed for this candidate'}
          style={{
            height: 32, padding: '0 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
            cursor: contact.email ? 'pointer' : 'not-allowed', border: 'none',
            background: contact.email ? 'var(--primary)' : 'var(--fg-subtle)', color: '#FFF',
            fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 6, opacity: contact.email ? 1 : 0.6,
          }}
        >
          <Icon name="mail" size={14} />Reach out
        </button>
      </div>
    </div>
  );
}

// ── Outreach email modal ──────────────────────────────────────────────────────
export function EmailModal({ candidate, roleTitle, onClose }: { candidate: MatchedCandidate; roleTitle?: string; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [to, setTo] = useState(candidate.contact?.email || '');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [sendEnabled, setSendEnabled] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = await draftOutreach(candidate.candidateId, roleTitle);
        if (!alive) return;
        setTo(d.to || candidate.contact?.email || '');
        setSubject(d.subject);
        setBody(d.body);
        setSendEnabled(d.sendEnabled);
      } catch (e: any) {
        if (alive) setErr(e.message || 'Failed to draft email');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [candidate, roleTitle]);

  const onCopy = () => {
    navigator.clipboard?.writeText(`Subject: ${subject}\n\n${body}`);
    setNote('Copied to clipboard ✓');
  };

  const onTrack = async () => {
    setErr(null); setNote(null); setTracking(true);
    try {
      const res = await enrollOutreach({
        email: to,
        name: candidate.fullName || undefined,
        title: candidate.currentTitle || undefined,
        roleTitle,
        audience: 'candidate',
        campaignName: roleTitle,
        candidateId: candidate.candidateId,
      });
      setNote(res.sent
        ? `Sent & tracking in Outreach → Candidates ✓`
        : `Added to Outreach → Candidates ✓ ${res.note ? `(${res.note})` : ''}`);
    } catch (e: any) {
      setErr(e.message || 'Failed to add to outreach');
    } finally {
      setTracking(false);
    }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ background: '#FFF', borderRadius: 12, width: '100%', maxWidth: 620, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 10px 40px rgba(0,0,0,0.2)' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-default)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Reach out to {candidate.fullName || 'candidate'}</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--fg-muted)' }}><Icon name="x" size={18} /></button>
        </div>

        <div style={{ padding: 20 }}>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 30, color: 'var(--fg-muted)' }}>
              <Icon name="loader" size={22} /><div style={{ marginTop: 8, fontSize: 14 }}>Drafting a professional email…</div>
            </div>
          ) : (
            <>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>To</label>
              <input value={to} onChange={(e) => setTo(e.target.value)} style={{ width: '100%', height: 36, padding: '0 10px', borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 14px', boxSizing: 'border-box', fontFamily: 'inherit' }} />

              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>Subject</label>
              <input value={subject} onChange={(e) => setSubject(e.target.value)} style={{ width: '100%', height: 36, padding: '0 10px', borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 14px', boxSizing: 'border-box', fontFamily: 'inherit' }} />

              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-muted)' }}>Message</label>
              <textarea value={body} onChange={(e) => setBody(e.target.value)} style={{ width: '100%', minHeight: 220, padding: 12, borderRadius: 6, border: '1px solid var(--border-card)', fontSize: 14, margin: '4px 0 8px', boxSizing: 'border-box', fontFamily: 'inherit', lineHeight: 1.6, resize: 'vertical' }} />

              {!sendEnabled && (
                <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 8 }}>
                  Direct SMTP send isn’t configured. Use <strong>Add to Outreach</strong> to track this candidate in the CRM and deliver via Smartlead once connected.
                </div>
              )}
              {note && <div style={{ fontSize: 13, color: 'var(--status-success)', marginBottom: 8 }}>{note}</div>}
              {err && <div style={{ fontSize: 13, color: 'var(--status-danger)', marginBottom: 8 }}>{err}</div>}

              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 6, flexWrap: 'wrap' }}>
                <button onClick={onCopy} style={{ height: 38, padding: '0 16px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', background: 'var(--bg-app)', color: 'var(--fg-secondary)', border: '1px solid var(--border-card)', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                  <Icon name="copy" size={15} />Copy
                </button>
                <button onClick={onTrack} disabled={tracking || !to} style={{ height: 38, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: (tracking || !to) ? 'not-allowed' : 'pointer', border: 'none', background: !to ? 'var(--fg-subtle)' : 'var(--primary)', color: '#FFF', fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                  <Icon name="user-plus" size={15} />{tracking ? 'Adding…' : (sendEnabled ? 'Send & track' : 'Add to Outreach')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
