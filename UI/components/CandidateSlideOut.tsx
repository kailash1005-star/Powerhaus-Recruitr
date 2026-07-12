'use client';

import { useMemo, useState } from 'react';
import { Icon } from './Icon';
import { ApifyProfileView } from './ApifyProfileView';
import type { Candidate } from '@/lib/api';

interface CandidateSlideOutProps {
  isOpen: boolean;
  onClose: () => void;
  candidates: Candidate[];
  activeId: string | null;
  setActiveId: (id: string | null) => void;
  jobTitle: string;
  companyName: string;
  busyId: string | null;
  onEnrich: (id: string) => void;
  onToggleAccept: (c: Candidate) => void;
}

// Many records ship with lastName set to "—" / "-" / "–" as a placeholder.
function cleanLastName(lastName?: string | null): string {
  if (!lastName) return '';
  const trimmed = lastName.trim();
  if (!trimmed || trimmed === '—' || trimmed === '-' || trimmed === '–' || trimmed === '--') return '';
  return trimmed;
}

function fullName(c: Candidate): string {
  if (c.displayName && c.displayName.trim()) return c.displayName.trim();
  const ln = cleanLastName(c.lastName);
  const fn = (c.firstName || '').trim();
  if (!fn && !ln) return '—';
  if (!ln) return fn;
  return `${fn} ${ln}`;
}

function formatDateRange(start?: unknown, end?: unknown, current?: unknown): string {
  const s = typeof start === 'string' ? start.slice(0, 4) : '';
  if (current) return s ? `${s} – Present` : 'Present';
  const e = typeof end === 'string' ? end.slice(0, 4) : '';
  if (s && e) return `${s} – ${e}`;
  return s || e || '';
}

export function CandidateSlideOut({
  isOpen, onClose, candidates, activeId, setActiveId,
  jobTitle, companyName, busyId, onEnrich, onToggleAccept,
}: CandidateSlideOutProps) {
  const [copied, setCopied] = useState<string | null>(null);

  const active = useMemo(
    () => candidates.find((c) => c._id === activeId) || null,
    [candidates, activeId],
  );

  const copy = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 2000);
  };

  const enriched = active?.enrichedData;
  const empHistory = enriched?.employmentHistory ?? [];
  const socials = enriched?.socials;
  const org = enriched?.organization;

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

      {/* Slide-over panel */}
      <div
        style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          width: '95vw', maxWidth: 900,
          background: '#FFF', zIndex: 91,
          display: 'flex', flexDirection: 'column',
          boxShadow: '-8px 0 40px rgba(0,0,0,0.12)',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 280ms cubic-bezier(.22,.68,0,1.2)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '16px 20px', borderBottom: '1px solid var(--border-default)' }}>
          <div style={{ width: 40, height: 40, borderRadius: 10, background: '#EEF2FF', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Icon name="users" size={18} style={{ color: '#4F46E5' }} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>Candidates</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Enriched profiles from Apollo</div>
          </div>
          <button onClick={onClose} style={{ width: 32, height: 32, border: 'none', background: 'transparent', borderRadius: 6, cursor: 'pointer', color: 'var(--fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Job context */}
        <div style={{ padding: '10px 20px', background: '#F7F9FB', borderBottom: '1px solid var(--border-default)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Icon name="briefcase" size={14} style={{ color: '#4F46E5', flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{jobTitle}</div>
            <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>{companyName}</div>
          </div>
        </div>

        {/* Body — two columns */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

          {/* Left: list */}
          <div style={{ width: '40%', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border-default)', background: '#FFF' }}>
            <div style={{ padding: '8px 14px', background: '#F7F9FB', borderBottom: '1px solid var(--border-default)', display: 'flex', justifyContent: 'flex-end' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', background: 'var(--border-card)', padding: '2px 8px', borderRadius: 9999 }}>
                {candidates.length} total
              </span>
            </div>

            <div style={{ flex: 1, overflowY: 'auto' }}>
              {candidates.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', fontSize: 13, color: 'var(--fg-muted)' }}>
                  No candidates to show.
                </div>
              ) : (
                candidates.map((c) => {
                  const isActive = activeId === c._id;
                  return (
                    <div
                      key={c._id}
                      onClick={() => setActiveId(c._id)}
                      style={{
                        display: 'flex', alignItems: 'flex-start', gap: 12, padding: '12px 14px',
                        cursor: 'pointer', borderBottom: '1px solid var(--border-card)',
                        borderLeft: `3px solid ${isActive ? 'var(--fg-primary)' : 'transparent'}`,
                        background: isActive ? '#F4F7FC' : 'transparent',
                        transition: 'background 120ms',
                      }}
                    >
                      {/* Avatar */}
                      <div style={{ position: 'relative', flexShrink: 0 }}>
                        <div style={{ width: 40, height: 40, borderRadius: 9999, background: 'linear-gradient(135deg, #6B7280, #4B5563)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700, color: '#FFF', border: '1px solid #E5E7EB' }}>
                          {(c.firstName?.[0] ?? '?')}{(cleanLastName(c.lastName)[0] ?? '')}
                        </div>
                        <div style={{ position: 'absolute', bottom: -2, right: -2, width: 14, height: 14, borderRadius: 9999, border: '2px solid #FFF', background: c.isAccepted ? 'var(--status-success)' : 'var(--status-danger)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Icon name={c.isAccepted ? 'check' : 'x'} size={8} style={{ color: '#FFF' }} />
                        </div>
                      </div>

                      {/* Info */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {fullName(c)}
                          </span>
                          <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, flexShrink: 0, background: '#EEF2FF', color: '#4F46E5', border: '1px solid #DDD6FE' }}>
                            {c.matchScore}
                          </span>
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {c.currentTitle || c.headline || '—'}
                        </div>
                        {!c.isEnriched && (
                          <div style={{ fontSize: 10, fontWeight: 700, color: '#D97706', textTransform: 'uppercase', letterSpacing: '0.04em', marginTop: 2 }}>
                            Needs Enrichment
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right: detail */}
          <div style={{ flex: 1, overflowY: 'auto', background: '#FFF', position: 'relative' }}>
            {active ? (
              <div style={{ padding: '24px 28px' }}>
                {/* Avatar + name */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 18, marginBottom: 24 }}>
                  <div style={{ width: 72, height: 72, borderRadius: 9999, background: 'linear-gradient(135deg, #4F46E5, #7C3AED)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26, fontWeight: 700, color: '#FFF', border: '1px solid #E5E7EB', flexShrink: 0 }}>
                    {(active.firstName?.[0] ?? '?')}{(cleanLastName(active.lastName)[0] ?? '')}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
                      <h2 style={{ fontSize: 22, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>
                        {fullName(active)}
                      </h2>
                      <span style={{
                        fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 9999,
                        background: '#EEF2FF', color: '#4F46E5', border: '1px solid #DDD6FE',
                      }}>
                        {active.matchScore} match
                      </span>
                    </div>
                    <p style={{ fontSize: 14, color: 'var(--fg-muted)', margin: 0 }}>
                      {active.currentTitle || active.headline || '—'}
                      {active.currentCompany && <> at <span style={{ color: '#4F46E5', fontWeight: 600 }}>{active.currentCompany}</span></>}
                    </p>
                  </div>
                </div>

                {/* Location + LinkedIn + status */}
                <div style={{ display: 'flex', gap: 20, marginBottom: 24, fontSize: 13, color: 'var(--fg-muted)', flexWrap: 'wrap', alignItems: 'center' }}>
                  {active.location && (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Icon name="map-pin" size={14} /> {active.location}
                    </span>
                  )}
                  {active.externalLinkedinUrl && (
                    <a href={active.externalLinkedinUrl} target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#4F46E5', textDecoration: 'none', fontWeight: 500 }}>
                      <Icon name="linkedin" size={14} /> LinkedIn profile
                    </a>
                  )}
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 8px', borderRadius: 9999,
                    fontSize: 11, fontWeight: 600,
                    background: active.isAccepted ? 'var(--status-success)1A' : 'var(--status-danger)1A',
                    color: active.isAccepted ? 'var(--status-success)' : 'var(--status-danger)',
                    border: `1px solid ${active.isAccepted ? 'var(--status-success)40' : 'var(--status-danger)40'}`,
                  }}>
                    {active.isAccepted ? 'Accepted' : 'Rejected'}
                  </span>
                </div>

                {/* Action buttons */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
                  {active.isAccepted ? (
                    <button
                      disabled={busyId === active._id}
                      onClick={() => onToggleAccept(active)}
                      style={{
                        flex: 1, height: 36, borderRadius: 6, fontSize: 13, fontWeight: 600,
                        cursor: busyId === active._id ? 'not-allowed' : 'pointer',
                        border: '1px solid var(--status-danger)40', background: 'var(--status-danger)1A',
                        color: 'var(--status-danger)', fontFamily: 'inherit',
                      }}
                    >Reject</button>
                  ) : (
                    <button
                      disabled={busyId === active._id}
                      onClick={() => onToggleAccept(active)}
                      style={{
                        flex: 1, height: 36, borderRadius: 6, fontSize: 13, fontWeight: 600,
                        cursor: busyId === active._id ? 'not-allowed' : 'pointer',
                        border: '1px solid var(--status-success)40', background: 'var(--status-success)1A',
                        color: 'var(--status-success)', fontFamily: 'inherit',
                      }}
                    >Accept</button>
                  )}
                  <button
                    disabled={busyId === active._id || active.isEnriched}
                    onClick={() => onEnrich(active._id)}
                    title={active.isEnriched ? 'Already enriched' : 'Pull full profile from Apollo (uses Apollo credits)'}
                    style={{
                      flex: 1, height: 36, borderRadius: 6, fontSize: 13, fontWeight: 600,
                      cursor: busyId === active._id || active.isEnriched ? 'not-allowed' : 'pointer',
                      border: 'none', background: active.isEnriched ? 'var(--bg-app)' : 'var(--primary)',
                      color: active.isEnriched ? 'var(--fg-muted)' : '#FFF',
                      fontFamily: 'inherit', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    }}
                  >
                    {busyId === active._id ? <Icon name="loader" size={14} /> : <Icon name="sparkles" size={14} />}
                    {active.isEnriched ? 'Enriched' : 'Enrich'}
                  </button>
                </div>

                {/* Match reasons */}
                {active.matchReasons && active.matchReasons.length > 0 && (
                  <div style={{ marginBottom: 24 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
                      Match Reasons
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {active.matchReasons.map((r, i) => (
                        <span key={i} style={{ padding: '4px 10px', borderRadius: 6, fontSize: 12, background: '#F3F4F6', border: '1px solid var(--border-card)', color: 'var(--fg-secondary)' }}>
                          {r.replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Apollo enrichment */}
                {active.isEnriched && enriched ? (
                  <div style={{ marginBottom: 24 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Icon name="sparkles" size={14} style={{ color: '#4F46E5' }} /> Apollo Enrichment
                    </div>

                    {/* Contact card — email + email_status + personal emails */}
                    <div style={{ border: '1px solid var(--border-card)', borderRadius: 10, overflow: 'hidden', marginBottom: 14 }}>
                      <div style={{ background: '#F2F4F6', padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, minWidth: 0 }}>
                          <Icon name="mail" size={13} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
                          {enriched.email ? (
                            <span style={{ fontWeight: 700, color: '#4F46E5', background: '#EEF2FF', padding: '2px 8px', borderRadius: 4, overflow: 'hidden', textOverflow: 'ellipsis' }}>{enriched.email}</span>
                          ) : (
                            <span style={{ fontWeight: 600, color: '#92400E', background: '#FFFBEB', border: '1px solid #FDE68A', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                              No email found
                            </span>
                          )}
                          {enriched.emailStatus && enriched.email && (
                            <span title="Apollo email confidence" style={{
                              fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                              textTransform: 'uppercase', letterSpacing: '0.04em',
                              background: enriched.emailStatus === 'verified' ? '#ECFDF5' : '#FFFBEB',
                              color: enriched.emailStatus === 'verified' ? '#059669' : '#D97706',
                              border: `1px solid ${enriched.emailStatus === 'verified' ? '#A7F3D0' : '#FDE68A'}`,
                            }}>{enriched.emailStatus}</span>
                          )}
                        </div>
                        {enriched.email && (
                          <button
                            onClick={() => copy(enriched.email!)}
                            style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none', background: 'transparent', color: copied === enriched.email ? '#059669' : 'var(--fg-muted)', display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'inherit', flexShrink: 0 }}
                          >
                            <Icon name={copied === enriched.email ? 'check-check' : 'copy'} size={13} />
                            {copied === enriched.email ? 'Copied!' : 'Copy'}
                          </button>
                        )}
                      </div>
                      {(enriched.personalEmails?.length ?? 0) > 0 && (
                        <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border-card)', fontSize: 12, color: 'var(--fg-secondary)' }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Personal emails</div>
                          {enriched.personalEmails!.map((pe) => (
                            <div key={pe} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span>{pe}</span>
                              <button onClick={() => copy(pe)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: copied === pe ? '#059669' : 'var(--fg-muted)', display: 'inline-flex', alignItems: 'center' }}>
                                <Icon name={copied === pe ? 'check-check' : 'copy'} size={12} />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Socials */}
                    {socials && (socials.twitter || socials.github || socials.facebook) && (
                      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                        {socials.twitter && (
                          <a href={socials.twitter} target="_blank" rel="noreferrer" title="Twitter"
                            style={{ width: 32, height: 32, borderRadius: 8, border: '1px solid var(--border-card)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-secondary)', textDecoration: 'none' }}>
                            <Icon name="twitter" size={14} />
                          </a>
                        )}
                        {socials.github && (
                          <a href={socials.github} target="_blank" rel="noreferrer" title="GitHub"
                            style={{ width: 32, height: 32, borderRadius: 8, border: '1px solid var(--border-card)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-secondary)', textDecoration: 'none' }}>
                            <Icon name="github" size={14} />
                          </a>
                        )}
                        {socials.facebook && (
                          <a href={socials.facebook} target="_blank" rel="noreferrer" title="Facebook"
                            style={{ width: 32, height: 32, borderRadius: 8, border: '1px solid var(--border-card)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-secondary)', textDecoration: 'none' }}>
                            <Icon name="facebook" size={14} />
                          </a>
                        )}
                      </div>
                    )}

                    {/* Headline / pitch */}
                    {enriched.headline && (
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Headline</div>
                        <div style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.5 }}>{enriched.headline}</div>
                      </div>
                    )}

                    {/* Role taxonomy — seniority + functions + departments */}
                    {(enriched.seniority || (enriched.functions?.length ?? 0) > 0 || (enriched.departments?.length ?? 0) > 0) && (
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Role taxonomy</div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                          {enriched.seniority && (
                            <span style={{ padding: '3px 9px', borderRadius: 9999, fontSize: 11, fontWeight: 600, background: '#EEF2FF', color: '#4F46E5', border: '1px solid #DDD6FE' }}>
                              {enriched.seniority}
                            </span>
                          )}
                          {(enriched.functions ?? []).map((f) => (
                            <span key={`fn-${f}`} style={{ padding: '3px 9px', borderRadius: 9999, fontSize: 11, background: '#F3F4F6', border: '1px solid var(--border-card)', color: 'var(--fg-secondary)' }}>
                              {f}
                            </span>
                          ))}
                          {(enriched.departments ?? []).map((d) => (
                            <span key={`dp-${d}`} style={{ padding: '3px 9px', borderRadius: 9999, fontSize: 11, background: '#F3F4F6', border: '1px solid var(--border-card)', color: 'var(--fg-secondary)' }}>
                              {d}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Employment history */}
                    {empHistory.length > 0 && (
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                          Experience
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {empHistory.slice(0, 8).map((e, i) => (
                            <div key={i} style={{ padding: '10px 14px', background: 'var(--bg-app)', borderRadius: 8, border: '1px solid var(--border-card)' }}>
                              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>
                                {e.title || '—'}
                              </div>
                              <div style={{ fontSize: 12, color: 'var(--fg-muted)', display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {e.organizationName || '—'}
                                </span>
                                <span style={{ flexShrink: 0 }}>{formatDateRange(e.startDate, e.endDate, e.current)}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Current organization snapshot */}
                    {org && org.name && (
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                          Current employer
                        </div>
                        <div style={{ padding: '12px 14px', background: 'var(--bg-app)', borderRadius: 8, border: '1px solid var(--border-card)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                            {org.logoUrl ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img src={org.logoUrl} alt="" width={36} height={36} style={{ borderRadius: 6, objectFit: 'cover', flexShrink: 0 }} />
                            ) : (
                              <div style={{ width: 36, height: 36, borderRadius: 6, background: '#EEF2FF', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                                <Icon name="building-2" size={16} style={{ color: '#4F46E5' }} />
                              </div>
                            )}
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)' }}>{org.name}</div>
                              <div style={{ fontSize: 11, color: 'var(--fg-muted)' }}>
                                {[org.industry, org.estimatedNumEmployees ? `${org.estimatedNumEmployees.toLocaleString()} employees` : null, org.foundedYear ? `est. ${org.foundedYear}` : null].filter(Boolean).join(' · ') || '—'}
                              </div>
                            </div>
                          </div>
                          {(org.hqCity || org.hqCountry) && (
                            <div style={{ fontSize: 12, color: 'var(--fg-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                              <Icon name="map-pin" size={12} />
                              {[org.hqCity, org.hqCountry].filter(Boolean).join(', ')}
                            </div>
                          )}
                          {org.shortDescription && (
                            <div style={{ fontSize: 12, color: 'var(--fg-secondary)', marginTop: 6, lineHeight: 1.5 }}>
                              {org.shortDescription}
                            </div>
                          )}
                          <div style={{ display: 'flex', gap: 10, marginTop: 8, fontSize: 12 }}>
                            {org.websiteUrl && <a href={org.websiteUrl} target="_blank" rel="noreferrer" style={{ color: '#4F46E5', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="globe" size={12} /> Website</a>}
                            {org.linkedinUrl && <a href={org.linkedinUrl} target="_blank" rel="noreferrer" style={{ color: '#4F46E5', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4 }}><Icon name="linkedin" size={12} /> LinkedIn</a>}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  /* Not yet enriched — prompt */
                  <div style={{
                    marginBottom: 24, padding: '24px 20px', textAlign: 'center',
                    border: '1px dashed var(--border-card)', borderRadius: 10, background: '#FAFAFA',
                  }}>
                    <Icon name="sparkles" size={28} style={{ color: '#4F46E5', marginBottom: 10 }} />
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg-primary)', marginBottom: 4 }}>
                      Not enriched yet
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginBottom: 14 }}>
                      Enrich this candidate to pull their email, phone, experience, education and skills from Apollo.
                    </div>
                    <button
                      disabled={busyId === active._id}
                      onClick={() => onEnrich(active._id)}
                      style={{
                        height: 36, padding: '0 18px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                        cursor: busyId === active._id ? 'not-allowed' : 'pointer',
                        border: 'none', background: 'var(--primary)', color: '#FFF', fontFamily: 'inherit',
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                      }}
                    >
                      {busyId === active._id ? <Icon name="loader" size={14} /> : <Icon name="sparkles" size={14} />}
                      Enrich now
                    </button>
                  </div>
                )}

                {/* Apify deep LinkedIn profile (+ its background-fetch states) */}
                <ApifyProfileView candidate={active} />

                {/* Rejection reason */}
                {!active.isAccepted && active.rejectionReason && (
                  <div style={{ marginBottom: 24 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
                      Rejection Reason
                    </div>
                    <div style={{ padding: '10px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 13, color: '#B91C1C' }}>
                      {active.rejectionReason}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 40, height: '100%' }}>
                <Icon name="users" size={64} style={{ color: '#EEF2FF', marginBottom: 16 }} />
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-primary)', marginBottom: 8 }}>No candidate selected</div>
                <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Pick a candidate from the left to view their details.</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
