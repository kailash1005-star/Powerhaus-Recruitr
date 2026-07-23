'use client';

import { useState, useEffect, useRef } from 'react';
import { Icon } from '../Icon';
import { Avatar } from '../Avatar';
import {
  draftOutreach, enrollOutreach, cvDownloadUrl,
  enrichCandidateMobile, fetchCandidateMobile,
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
  // Same cut points as BANDS below — every view must tell the same story.
  const color = value >= 80 ? 'var(--status-success)' : value >= 60 ? 'var(--status-info)' : 'var(--status-danger)';
  return (
    <div style={{ width: '100%', height: 6, background: 'var(--bg-app)', borderRadius: 9999, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', background: color }} />
    </div>
  );
}

// ── Score bands ───────────────────────────────────────────────────────────────
// A number alone doesn't tell a recruiter whether to pick up the phone; a verdict
// does. Deliberately a strong three-color traffic-light read (green/blue/red), not
// a muted palette — the color itself is the fast signal a recruiter scans a whole
// list for, and it needs to land the same way every time it's seen. Thresholds are
// a product decision, kept here so they're changed in one place.
type Band = { label: string; fg: string; bg: string; line: string };
const BANDS: Array<{ min: number } & Band> = [
  { min: 80, label: 'Strong match', fg: 'var(--status-success)', bg: 'var(--status-success-bg)', line: '#A7F3D0' },
  { min: 60, label: 'Worth a look', fg: 'var(--status-info)', bg: 'var(--status-info-bg)', line: '#BFDBFE' },
  { min: -1, label: 'Underfit', fg: 'var(--status-danger)', bg: 'var(--status-danger-bg)', line: '#FECACA' },
];
export function bandFor(score: number): Band {
  return BANDS.find((b) => score >= b.min)!;
}

/** Plain-language verdict for the whole-profile read. The underlying number is a
 *  similarity score; nobody outside engineering needs to know that.
 *  Cut points match BANDS — the same number must never read as "Strong" in one
 *  view and "Worth a look" in another. */
function fitVerdict(v: number): { word: string; tone: string } {
  if (v >= 80) return { word: 'Strong', tone: 'var(--status-success)' };
  if (v >= 60) return { word: 'Good', tone: 'var(--status-info)' };
  return { word: 'Weak', tone: 'var(--status-danger)' };
}

// ── Requirement evidence ──────────────────────────────────────────────────────
// The scorer credits a requirement through NINE distinct methods — a plain name
// match is just one of them; the rest are exactly the "same meaning, different
// words" cases (a compound term, every word present but scattered across a
// sentence, a claim made only in a narrative bullet, a quote the QA pass
// independently re-verified against the CV). Every one of them is real,
// evidenced credit and MUST read as such — a method missing from this table used
// to silently fall through to "Missing / nothing evidences this" even when the
// backend had already scored it as fully covered, which is exactly the false
// "MISSING" the recruiter should never see next to a 100% match.
const EV_TAG: Record<SkillEvidence['method'], { text: string; fg: string; bg: string; line: string }> = {
  exact:               { text: 'Covered', fg: '#047857', bg: '#ECFDF5', line: '#A7F3D0' },
  specific:            { text: 'Covered', fg: '#047857', bg: '#ECFDF5', line: '#A7F3D0' },
  'all-terms':         { text: 'Covered', fg: '#047857', bg: '#ECFDF5', line: '#A7F3D0' },
  'profile-text':      { text: 'Covered', fg: '#047857', bg: '#ECFDF5', line: '#A7F3D0' },
  qa_verified:         { text: 'Covered', fg: '#047857', bg: '#ECFDF5', line: '#A7F3D0' },
  fuzzy:               { text: 'Close',   fg: '#B45309', bg: '#FFFBEB', line: '#FDE68A' },
  'profile-text-terms':{ text: 'Close',   fg: '#B45309', bg: '#FFFBEB', line: '#FDE68A' },
  broader:             { text: 'Partial', fg: '#9A3412', bg: '#FFF7ED', line: '#FED7AA' },
  none:                { text: 'Missing', fg: '#B91C1C', bg: '#FEF2F2', line: '#FECACA' },
};

/** Recruiter-facing sentence for one requirement. The backend's note is written
 *  for the audit trail; this is written for the person reading it next to a name.
 *  `qa_verified` reads identically to a normal specific match — the quote WAS
 *  found on their profile, just by the QA re-check rather than the first pass;
 *  the reader needs "this is covered", not the internal fact that a second pass
 *  is what found it. */
function evidenceLine(e: SkillEvidence): React.ReactNode {
  const via = <q style={{ fontStyle: 'normal', color: 'var(--fg-secondary)' }}>{e.via}</q>;
  switch (e.method) {
    case 'exact':
      return 'Named directly on their profile';
    case 'specific':
    case 'all-terms':
    case 'profile-text':
    case 'qa_verified':
      return <>Evidenced by {via}</>;
    case 'fuzzy':
      return <>They have {via} — the same area, not the exact term named</>;
    case 'profile-text-terms':
      return <>They have {via} — every part of it appears on their profile, just not as one exact phrase</>;
    case 'broader':
      return <>They have {via} — related, but narrower than the requirement</>;
    default:
      return <span style={{ color: 'var(--fg-subtle)', fontStyle: 'italic' }}>Nothing on their profile evidences this</span>;
  }
}

const pLabel: React.CSSProperties = {
  fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em',
  color: 'var(--fg-subtle)', marginBottom: 12,
};

/** "Why this score", in words. Deliberately carries NO weights, points, formula or
 *  similarity maths — a recruiter needs to know what was found and how solid it is,
 *  and the arithmetic only got in the way of that. */
export function ScoreBreakdown({ bd }: { bd: ScoreBreakdownData }) {
  const comp = (k: string) => bd.components.find((c) => c.key === k);
  const sem = comp('semantic');
  const skills = comp('skillCoverage');
  const exp = comp('experience');
  const loc = comp('location');
  const ev = skills?.skills || [];
  const proven = ev.filter((e) => e.credit >= 1).length;
  const fit = fitVerdict(sem?.value ?? 0);
  // Strongest evidence first — a reader scans for what's solid, then what isn't.
  const ordered = [...ev].sort((a, b) => b.credit - a.credit);

  const dim = (
    key: string, name: string, verdict: React.ReactNode, tone: string,
    detail: React.ReactNode, off = false,
  ) => (
    <div key={key} style={{
      display: 'grid', gridTemplateColumns: '124px auto', gap: '4px 12px',
      padding: '9px 0', borderTop: '1px solid var(--border-default)', alignItems: 'baseline',
      opacity: off ? 0.75 : 1,
    }}>
      <span style={{ fontSize: 12.5, fontWeight: 600, color: off ? 'var(--fg-subtle)' : 'var(--fg-secondary)' }}>{name}</span>
      <span style={{ fontSize: 12.5, fontWeight: 600, color: tone }}>{verdict}</span>
      <span style={{ gridColumn: 2, fontSize: 12.5, color: 'var(--fg-muted)', lineHeight: 1.5 }}>{detail}</span>
    </div>
  );

  return (
    <div className="match-evidence" style={{
      borderTop: '1px solid var(--border-card)', background: 'var(--bg-muted)',
      borderRadius: '0 0 9px 9px',
    }}>
      <div style={{ padding: '18px 20px', minWidth: 0 }}>
        <div style={pLabel}>Assessment</div>
        {sem && dim('fit', 'Profile fit', fit.word, fit.tone,
          'How the whole profile reads against the whole role — not just a keyword count.')}
        {skills?.applicable && dim('skills', 'Must-have skills',
          `${proven} of ${ev.length} proven`, proven === ev.length ? '#047857' : '#B45309',
          ev.length - proven > 0
            ? `${ev.length - proven} more ${ev.length - proven === 1 ? 'is' : 'are'} partly evidenced or absent — see the right.`
            : 'Every requirement is evidenced on their profile.')}
        {exp?.applicable && dim('exp', 'Experience',
          exp.value >= 100 ? 'Met' : 'Short', exp.value >= 100 ? '#047857' : '#B45309', exp.note)}
        {loc && !loc.applicable
          ? dim('loc', 'Location', 'Not scored', 'var(--fg-subtle)',
              'This role names no location, so it counts neither for nor against anyone.', true)
          : loc && dim('loc', 'Location', loc.value >= 100 ? 'Match' : 'Away', loc.value >= 100 ? '#047857' : '#B45309', loc.note)}
        {bd.cappedBy && (
          <div style={{
            marginTop: 12, padding: '8px 10px', borderRadius: 6, background: '#FFF7ED',
            border: '1px solid #FED7AA', color: '#9A3412', fontSize: 12, lineHeight: 1.5,
          }}>
            Their score is held at <strong>{bd.ceiling}</strong> because must-have skills are missing.
            Everything else about the profile would otherwise place them higher.
          </div>
        )}
      </div>

      {/* The divider is owned by .match-evidence so it can flip to a top border
          when the two columns stack. */}
      <div style={{ padding: '18px 20px', minWidth: 0 }}>
        <div style={pLabel}>What we found for each requirement</div>
        {ordered.length === 0 ? (
          <div style={{ fontSize: 12.5, color: 'var(--fg-muted)' }}>
            This role lists no must-have skills, so there was nothing to check against.
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <tbody>
              {ordered.map((e, i) => {
                const t = EV_TAG[e.method] || EV_TAG.none;
                return (
                  <tr key={`${e.skill}-${i}`}>
                    <td style={{
                      padding: '8px 12px 8px 0', borderTop: i ? '1px solid var(--border-default)' : 'none',
                      verticalAlign: 'baseline', fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap',
                    }}>{e.skill}</td>
                    <td style={{
                      padding: '8px 12px 8px 0', borderTop: i ? '1px solid var(--border-default)' : 'none',
                      verticalAlign: 'baseline', width: '1%',
                    }}>
                      <span style={{
                        fontSize: 9.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
                        padding: '2px 7px', borderRadius: 4, whiteSpace: 'nowrap',
                        background: t.bg, color: t.fg, border: `1px solid ${t.line}`,
                      }}>{t.text}</span>
                    </td>
                    <td style={{
                      padding: '8px 0', borderTop: i ? '1px solid var(--border-default)' : 'none',
                      verticalAlign: 'baseline', fontSize: 12.5, color: 'var(--fg-muted)', lineHeight: 1.5,
                    }}>{evidenceLine(e)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
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

  // ── Phone reveal (Apollo) ──────────────────────────────────────────────────
  // Pipeline candidates carry no phone until revealed on demand. Apollo may return
  // it at once or deliver it via webhook, so a "pending" reveal auto-polls rather
  // than making the user click again (each click re-bills a credit).
  const [phone, setPhone] = useState<string | null>(contact.phone || null);
  const [phoneState, setPhoneState] = useState<'idle' | 'revealing' | 'pending'>('idle');
  const [phoneErr, setPhoneErr] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const pollForPhone = () => {
    if (pollRef.current) return;
    let polls = 0;
    pollRef.current = setInterval(async () => {
      polls += 1;
      try {
        const r = await fetchCandidateMobile(c.candidateId);
        if (r.phone) {
          setPhone(r.phone);
          setPhoneState('idle');
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          return;
        }
      } catch { /* transient — keep polling to the cap */ }
      if (polls >= 6) {
        setPhoneState('idle');
        setPhoneErr('Apollo hasn’t delivered a number yet — try again shortly.');
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      }
    }, 5000);
  };

  const revealPhone = async () => {
    setPhoneState('revealing');
    setPhoneErr(null);
    try {
      const r = await enrichCandidateMobile(c.candidateId);
      if (r.status === 'enriched' && r.phone) {
        setPhone(r.phone);
        setPhoneState('idle');
      } else if (r.status === 'pending') {
        setPhoneState('pending');
        pollForPhone();
      } else {
        setPhoneState('idle');
        setPhoneErr('Apollo had no phone number on file for this candidate.');
      }
    } catch (e: any) {
      setPhoneState('idle');
      const msg = e?.message || '';
      if (msg.includes('503') || msg.includes('APOLLO_WEBHOOK_URL')) {
        setPhoneErr('Phone reveal needs APOLLO_WEBHOOK_URL set in the backend .env.');
      } else if (msg.includes('422')) {
        setPhoneErr('Couldn’t resolve this candidate on Apollo — no phone available.');
      } else {
        setPhoneErr('Phone reveal failed. Check the Apollo API key / credits.');
      }
    }
  };
  const band = bandFor(c.score);
  const missing = c.gaps || [];
  const partial = c.partial || [];

  const flag = (tone: string, word: string, items: string[]) => (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 6, fontSize: 12 }}>
      <span style={{
        fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
        letterSpacing: '0.05em', color: tone, flexShrink: 0,
      }}>{word}</span>
      <span style={{ color: 'var(--fg-muted)' }}>{items.join(', ')}</span>
    </span>
  );

  return (
    <article
      className="match-card"
      style={{
        border: `1px solid ${showWhy ? 'var(--border-strong)' : 'var(--border-card)'}`,
        borderRadius: 10, background: '#FFF',
        boxShadow: showWhy ? '0 1px 3px rgba(17,24,39,0.05)' : 'none',
        transition: 'border-color 120ms, box-shadow 120ms',
      }}
    >
      <div style={{ padding: '18px 20px', minWidth: 0 }}>
        <div
          onClick={clickable ? () => onOpen!(c) : undefined}
          style={{ cursor: clickable ? 'pointer' : 'default', display: 'inline-block', maxWidth: '100%' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {/* The face replaces the rank digits — a recruiter recognises a person
                faster than a position, and the ordering is already the list order.
                The rank rides on the avatar so it isn't lost. */}
            <div style={{ position: 'relative', flexShrink: 0 }}>
              <Avatar src={c.photoUrl} name={c.fullName} size={44} />
              <span style={{
                position: 'absolute', bottom: -3, right: -5, minWidth: 17, height: 17,
                padding: '0 3px', borderRadius: 9999, background: '#FFF',
                border: '1px solid var(--border-card)', color: 'var(--fg-muted)',
                fontFamily: 'var(--font-mono)', fontSize: 9.5, fontWeight: 700,
                fontVariantNumeric: 'tabular-nums', display: 'flex',
                alignItems: 'center', justifyContent: 'center',
              }}>{String(rank).padStart(2, '0')}</span>
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--fg-primary)' }}>
                  {c.fullName || 'Unnamed candidate'}
                </span>
                {c.openToWork && (
                  <span title="LinkedIn: open to work — likely to respond" style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    padding: '2px 8px', borderRadius: 9999, fontSize: 10.5, fontWeight: 700,
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                    background: '#ECFDF5', color: '#047857', border: '1px solid #A7F3D0',
                  }}>
                    <Icon name="hand" size={10} />Open to work
                  </span>
                )}
                {c.qa?.corrected && (
                  <span
                    // Client-safe: states what was confirmed, never the before/after
                    // score — that reads as "the tool was wrong", not as the trust
                    // signal this badge is meant to be.
                    title={`Profile evidence for ${(c.qa.verifiedSkills || []).map((s) => s.skill).join(', ')} was independently re-verified against their CV.`}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      padding: '2px 8px', borderRadius: 9999, fontSize: 10.5, fontWeight: 700,
                      textTransform: 'uppercase', letterSpacing: '0.04em',
                      background: '#EFF6FF', color: '#1D4ED8', border: '1px solid #BFDBFE',
                    }}>
                    <Icon name="shield" size={10} />QA verified
                  </span>
                )}
                {c.retrieval?.channels?.length === 1 && c.retrieval.channels[0] === 'lexical' && (
                  <span
                    title="Surfaced by the job description's own keywords in the CV text — profile-meaning search alone would have missed this candidate."
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 4,
                      padding: '2px 8px', borderRadius: 9999, fontSize: 10.5, fontWeight: 700,
                      textTransform: 'uppercase', letterSpacing: '0.04em',
                      background: '#FAF5FF', color: '#7E22CE', border: '1px solid #E9D5FF',
                    }}>
                    <Icon name="search" size={10} />Keyword find
                  </span>
                )}
                {clickable && <Icon name="arrow-up-right" size={13} style={{ color: 'var(--fg-subtle)' }} />}
              </div>
              <div style={{ fontSize: 13, color: 'var(--fg-muted)', marginTop: 3 }}>
                {c.currentTitle || '—'}
                {c.location && <><span style={{ color: 'var(--fg-disabled)', margin: '0 6px' }}>·</span>{c.location}</>}
              </div>
            </div>
          </div>
        </div>

        {c.reasons?.length > 0 && (
          <ul style={{ margin: '12px 0 0', padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 5 }}>
            {c.reasons.map((r, i) => (
              <li key={i} style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.5, paddingLeft: 14, position: 'relative' }}>
                <span style={{
                  position: 'absolute', left: 0, top: 8, width: 4, height: 4,
                  borderRadius: '50%', background: 'var(--fg-disabled)',
                }} />
                {r}
              </li>
            ))}
          </ul>
        )}

        {(missing.length > 0 || partial.length > 0) && (
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 11 }}>
            {missing.length > 0 && flag('#B91C1C', 'Missing', missing)}
            {partial.length > 0 && flag('#9A3412', 'Partially met', partial.map((p) => p.skill))}
          </div>
        )}

        <div style={{
          marginTop: 14, paddingTop: 13, borderTop: '1px solid var(--border-default)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap',
        }}>
          {c.breakdown ? (
            <button
              onClick={() => setShowWhy((s) => !s)}
              style={{
                fontSize: 12, fontWeight: 600, color: 'var(--primary)', background: 'none',
                border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              <Icon name={showWhy ? 'chevron-down' : 'chevron-right'} size={13} />
              {showWhy ? 'Hide the evidence' : 'See the evidence'}
            </button>
          ) : <span />}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {contact.email && (
              <span style={{ fontSize: 12, color: 'var(--fg-muted)', marginRight: 4 }}>{contact.email}</span>
            )}
            {/* Phone — a tel link once revealed, else an on-demand Apollo reveal.
                Sits before LinkedIn so contact actions read email → phone → profile. */}
            {phone ? (
              <a
                href={`tel:${phone}`} title={`Call ${phone}`}
                style={{
                  height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                  border: '1px solid var(--border-card)', textDecoration: 'none', background: '#FFF',
                  color: 'var(--fg-secondary)', display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                <Icon name="phone" size={13} />{phone}
              </a>
            ) : c.source === 'pipeline' ? (
              <button
                onClick={revealPhone}
                disabled={phoneState !== 'idle'}
                title={phoneErr || 'Reveal this candidate’s mobile number via Apollo'}
                style={{
                  height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                  cursor: phoneState === 'idle' ? 'pointer' : 'wait',
                  border: `1px solid ${phoneErr ? 'var(--status-danger)' : 'var(--border-card)'}`,
                  background: '#FFF', fontFamily: 'inherit',
                  color: phoneErr ? 'var(--status-danger)' : 'var(--fg-secondary)',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                <Icon name={phoneState === 'idle' ? 'phone' : 'loader'} size={13} />
                {phoneState === 'revealing' ? 'Revealing…'
                  : phoneState === 'pending' ? 'Waiting for Apollo…'
                  : phoneErr ? 'Retry phone' : 'Reveal phone'}
              </button>
            ) : null}
            {c.source !== 'pipeline' ? (
              <a
                href={cvDownloadUrl(c.candidateId)} download title="Download this candidate's CV"
                style={{
                  height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                  border: '1px solid var(--border-card)', textDecoration: 'none', background: '#FFF',
                  color: 'var(--fg-secondary)', display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                <Icon name="download" size={13} />Download CV
              </a>
            ) : contact.linkedin ? (
              <a
                href={contact.linkedin} target="_blank" rel="noopener noreferrer" title="Open LinkedIn profile"
                style={{
                  height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                  border: '1px solid var(--border-card)', textDecoration: 'none', background: '#FFF',
                  color: 'var(--fg-secondary)', display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                <Icon name="linkedin" size={13} />LinkedIn
              </a>
            ) : null}
            <button
              onClick={() => onReachOut(c)}
              disabled={!contact.email}
              title={contact.email ? 'Draft an outreach email' : 'No email found for this candidate'}
              style={{
                height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12.5, fontWeight: 600,
                cursor: contact.email ? 'pointer' : 'not-allowed', border: '1px solid var(--primary)',
                background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6, opacity: contact.email ? 1 : 0.45,
              }}
            >
              <Icon name="mail" size={13} />Reach out
            </button>
          </div>
        </div>
      </div>

      {/* Score rail — .match-rail turns it into a footer strip on narrow screens */}
      <div className="match-rail" style={{
        borderLeft: '1px solid var(--border-card)', padding: '18px 16px',
        display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8, textAlign: 'right',
      }}>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: 32, fontWeight: 600, lineHeight: 1,
          letterSpacing: '-0.03em', fontVariantNumeric: 'tabular-nums', color: band.fg,
        }}>{c.score.toFixed(1)}</div>
        <span style={{
          fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em',
          padding: '3px 8px', borderRadius: 5, whiteSpace: 'nowrap',
          background: band.bg, color: band.fg, border: `1px solid ${band.line}`,
        }}>{band.label}</span>
        {c.breakdown?.cappedBy && missing.length > 0 && (
          <div style={{ fontSize: 11, color: '#9A3412', lineHeight: 1.4 }}>
            Held back by {missing.length} missing must-have{missing.length === 1 ? '' : 's'}
          </div>
        )}
      </div>

      {showWhy && c.breakdown && (
        <div style={{ gridColumn: '1 / -1' }}>
          <ScoreBreakdown bd={c.breakdown} />
        </div>
      )}
    </article>
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
