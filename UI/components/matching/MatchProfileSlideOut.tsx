'use client';

import { useEffect, useState } from 'react';
import { Icon } from '../Icon';
import { Avatar } from '../Avatar';
import { ApifyProfileView } from '../ApifyProfileView';
import { ScoreBar } from './shared';
import { fetchCandidate, type Candidate, type MatchedCandidate } from '@/lib/api';

/**
 * Deep-profile slide-over for a matched candidate. The match run already carries
 * the score / subscores / reasons / contact; this fetches the full candidate doc
 * by id to render the Apify LinkedIn profile ("the full data from Apify") so the
 * recruiter can judge the match. Only meaningful for pipeline-sourced candidates
 * (CV-sourced results have no candidate doc / Apify profile).
 */
export function MatchProfileSlideOut({ matched, roleTitle, onClose }: {
  matched: MatchedCandidate | null;
  roleTitle?: string;
  onClose: () => void;
}) {
  const isOpen = !!matched;
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!matched) return;
    let alive = true;
    setCandidate(null);
    setError(null);
    setState('loading');
    (async () => {
      try {
        const c = await fetchCandidate(matched.candidateId);
        if (!alive) return;
        setCandidate(c);
        setState('idle');
      } catch (e: any) {
        if (!alive) return;
        setError(e?.message || 'Failed to load candidate');
        setState('error');
      }
    })();
    return () => { alive = false; };
  }, [matched]);

  const contact = matched?.contact || {};
  const subscores = matched?.subscores || {};

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)',
          backdropFilter: 'blur(2px)', zIndex: 90,
          opacity: isOpen ? 1 : 0,
          pointerEvents: isOpen ? 'auto' : 'none',
          transition: 'opacity 250ms',
        }}
      />

      {/* Panel */}
      <div
        style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          width: '95vw', maxWidth: 760,
          background: '#FFF', zIndex: 91,
          display: 'flex', flexDirection: 'column',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.12)',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 280ms cubic-bezier(.22,.68,0,1.2)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 20px', borderBottom: '1px solid var(--border-default)' }}>
          {/* The person, not a generic user glyph — the panel is about them. */}
          <Avatar src={matched?.photoUrl} name={matched?.fullName} size={40} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>
              {matched?.fullName || 'Candidate profile'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {roleTitle ? `Match for ${roleTitle}` : 'Enriched profile from Apify'}
            </div>
          </div>
          <button onClick={onClose} style={{ width: 32, height: 32, border: 'none', background: 'transparent', borderRadius: 6, cursor: 'pointer', color: 'var(--fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', background: '#FFF' }}>
          {matched && (
            <div style={{ padding: '24px 28px' }}>
              {/* Identity + score */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, marginBottom: 20 }}>
                <div style={{ minWidth: 0 }}>
                  <h2 style={{ fontSize: 22, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>
                    {matched.fullName || candidate?.displayName || 'Unnamed candidate'}
                  </h2>
                  <p style={{ fontSize: 14, color: 'var(--fg-muted)', margin: '4px 0 0' }}>
                    {matched.currentTitle || '—'}{matched.location ? ` · ${matched.location}` : ''}
                  </p>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div style={{ fontSize: 30, fontWeight: 800, color: 'var(--fg-primary)', lineHeight: 1 }}>{matched.score}</div>
                  <div style={{ fontSize: 10, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>match</div>
                </div>
              </div>

              {/* Contact */}
              <div style={{ display: 'flex', gap: 18, marginBottom: 20, fontSize: 13, color: 'var(--fg-secondary)', flexWrap: 'wrap', alignItems: 'center' }}>
                {contact.email && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="mail" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.email}</span>}
                {contact.phone && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="phone" size={14} style={{ color: 'var(--fg-muted)' }} />{contact.phone}</span>}
                {contact.linkedin && (
                  <a href={contact.linkedin} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#4F46E5', textDecoration: 'none', fontWeight: 500 }}>
                    <Icon name="linkedin" size={14} /> LinkedIn profile
                  </a>
                )}
              </div>

              {/* Subscores */}
              {Object.keys(subscores).length > 0 && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12, marginBottom: 20 }}>
                  {Object.entries(subscores).map(([k, v]) => (
                    <div key={k}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                        <span style={{ fontSize: 11, color: 'var(--fg-muted)', textTransform: 'capitalize' }}>{k.replace(/([A-Z])/g, ' $1')}</span>
                        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--fg-secondary)' }}>{Math.round(v)}</span>
                      </div>
                      <ScoreBar value={v} />
                    </div>
                  ))}
                </div>
              )}

              {/* Reasons */}
              {matched.reasons?.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Why this match</div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.6 }}>
                    {matched.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              {matched.gaps?.length > 0 && (
                <div style={{ marginBottom: 20, fontSize: 12, color: 'var(--status-danger)' }}>Gaps: {matched.gaps.join('; ')}</div>
              )}

              <div style={{ height: 1, background: 'var(--border-default)', margin: '4px 0 20px' }} />

              {/* Deep Apify profile */}
              {state === 'loading' && (
                <div style={{ textAlign: 'center', padding: 30, color: 'var(--fg-muted)' }}>
                  <Icon name="loader" size={20} />
                  <div style={{ marginTop: 8, fontSize: 13 }}>Loading full profile…</div>
                </div>
              )}
              {state === 'error' && (
                <div style={{ padding: '12px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 13, color: '#B91C1C' }}>
                  {error || 'Failed to load candidate.'}
                </div>
              )}
              {candidate && <ApifyProfileView candidate={candidate} />}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
