'use client';

import { useState, useEffect, useMemo } from 'react';
import { Icon } from './Icon';
import { fetchJobProspects, enrichProspect, enrichProspectPhone, type JobProspect } from '@/lib/api';

interface ProspectsSlideOutProps {
  isOpen: boolean;
  onClose: () => void;
  jobId: string | null;
  jobTitle: string;
  companyName: string;
  runId: string;
}

const TABS = ['All', 'Accepted', 'Rejected'] as const;
type Tab = (typeof TABS)[number];

const SENIORITY_STYLE: Record<string, React.CSSProperties> = {
  c_suite:  { background: '#F5F3FF', color: '#7C3AED', border: '1px solid #DDD6FE' },
  vp:       { background: '#EFF6FF', color: '#2563EB', border: '1px solid #BFDBFE' },
  director: { background: '#ECFDF5', color: '#059669', border: '1px solid #A7F3D0' },
  head:     { background: '#FFFBEB', color: '#D97706', border: '1px solid #FDE68A' },
  manager:  { background: '#F3F4F6', color: '#6B7280', border: '1px solid #E5E7EB' },
};

function seniorityStyle(s: string | undefined): React.CSSProperties {
  return SENIORITY_STYLE[(s || '').toLowerCase()] ?? { background: '#F3F4F6', color: '#6B7280', border: '1px solid #E5E7EB' };
}

// Many records ship with lastName set to "—" / "-" / "–" as a placeholder.
// Treat those as empty so we never render the standalone dash next to a first name.
function cleanLastName(lastName?: string | null): string {
  if (!lastName) return '';
  const trimmed = lastName.trim();
  if (!trimmed || trimmed === '—' || trimmed === '-' || trimmed === '–' || trimmed === '--') return '';
  return trimmed;
}

function fullName(firstName?: string, lastName?: string | null): string {
  const ln = cleanLastName(lastName);
  const fn = (firstName || '').trim();
  if (!fn && !ln) return '—';
  if (!ln) return fn;
  return `${fn} ${ln}`;
}

export function ProspectsSlideOut({
  isOpen, onClose, jobId, jobTitle, companyName, runId,
}: ProspectsSlideOutProps) {
  const [prospects, setProspects] = useState<JobProspect[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>('All');
  const [activeId, setActiveId] = useState<string | null>(null);
  const [copiedEmail, setCopiedEmail] = useState<string | null>(null);
  const [enrichingId, setEnrichingId] = useState<string | null>(null);
  const [enrichError, setEnrichError] = useState<string | null>(null);
  const [phoneEnrichingId, setPhoneEnrichingId] = useState<string | null>(null);
  const [phoneEnrichError, setPhoneEnrichError] = useState<string | null>(null);
  const [copiedPhone, setCopiedPhone] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || !jobId) return;
    setLoading(true);
    setProspects([]);
    fetchJobProspects(jobId)
      .then((d) => {
        setProspects(d.prospects);
        setActiveId(d.prospects[0]?._id ?? null);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isOpen, jobId]);

  const filtered = useMemo(() => {
    if (tab === 'All') return prospects;
    if (tab === 'Accepted') return prospects.filter((p) => p.isAccepted);
    return prospects.filter((p) => !p.isAccepted);
  }, [prospects, tab]);

  // Re-sync activeId when tab filter removes current selection
  useEffect(() => {
    if (activeId && !filtered.find((p) => p._id === activeId) && filtered.length > 0) {
      setActiveId(filtered[0]._id);
    }
  }, [filtered, activeId]);

  const active = useMemo(() => prospects.find((p) => p._id === activeId), [prospects, activeId]);

  const copyEmail = (email: string) => {
    navigator.clipboard.writeText(email);
    setCopiedEmail(email);
    setTimeout(() => setCopiedEmail(null), 2000);
  };

  const handleEnrich = async (p: JobProspect) => {
    setEnrichingId(p._id);
    setEnrichError(null);
    try {
      const { prospect, emailRevealed } = await enrichProspect(p._id);
      setProspects((prev) => prev.map((x) => (x._id === prospect._id ? { ...x, ...prospect } : x)));
      if (!emailRevealed) {
        setEnrichError('Apollo had no email on file for this prospect.');
      }
    } catch {
      setEnrichError('Enrichment failed. Check the Apollo API key / credits and try again.');
    } finally {
      setEnrichingId(null);
    }
  };

  const handlePhoneEnrich = async (p: JobProspect) => {
    setPhoneEnrichingId(p._id);
    setPhoneEnrichError(null);
    try {
      const result = await enrichProspectPhone(p._id);
      if (result.status === 'enriched' && result.phone) {
        setProspects((prev) =>
          prev.map((x) =>
            x._id === p._id
              ? {
                  ...x,
                  mobileEnrichmentStatus: 'enriched' as const,
                  prospectDetails: { ...x.prospectDetails, phone: result.phone! },
                }
              : x,
          ),
        );
      } else if (result.status === 'pending') {
        setProspects((prev) =>
          prev.map((x) =>
            x._id === p._id
              ? { ...x, mobileEnrichmentStatus: 'pending' as const }
              : x,
          ),
        );
      } else {
        setPhoneEnrichError('Apollo had no phone number on file for this prospect.');
      }
    } catch (e: any) {
      const msg = e?.message || '';
      if (msg.includes('503') || msg.includes('APOLLO_WEBHOOK_URL')) {
        setPhoneEnrichError('Phone enrichment requires APOLLO_WEBHOOK_URL to be configured in the backend .env file.');
      } else {
        setPhoneEnrichError('Phone enrichment failed. Check Apollo API key / credits.');
      }
    } finally {
      setPhoneEnrichingId(null);
    }
  };

  const copyPhone = (phone: string) => {
    navigator.clipboard.writeText(phone);
    setCopiedPhone(phone);
    setTimeout(() => setCopiedPhone(null), 2000);
  };

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
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--fg-primary)' }}>Prospects</div>
            <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Found for this job posting</div>
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
            {/* Tabs */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border-default)' }}>
              {TABS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  style={{
                    flex: 1, padding: '10px 0', fontSize: 13, fontWeight: 500,
                    cursor: 'pointer', border: 'none', background: 'transparent', fontFamily: 'inherit',
                    borderBottom: tab === t ? '2px solid var(--fg-primary)' : '2px solid transparent',
                    color: tab === t ? 'var(--fg-primary)' : 'var(--fg-muted)',
                    transition: 'all 120ms',
                  }}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Count bar */}
            <div style={{ padding: '8px 14px', background: '#F7F9FB', borderBottom: '1px solid var(--border-default)', display: 'flex', justifyContent: 'flex-end' }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', background: 'var(--border-card)', padding: '2px 8px', borderRadius: 9999 }}>
                {filtered.length} total
              </span>
            </div>

            {/* Prospect rows */}
            <div style={{ flex: 1, overflowY: 'auto' }}>
              {loading ? (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: 'var(--fg-muted)' }}>
                  <Icon name="loader" size={20} />
                </div>
              ) : filtered.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', fontSize: 13, color: 'var(--fg-muted)' }}>
                  No prospects match this filter.
                </div>
              ) : (
                filtered.map((p) => {
                  const isActive = activeId === p._id;
                  return (
                    <div
                      key={p._id}
                      onClick={() => setActiveId(p._id)}
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
                          {(p.firstName?.[0] ?? '?')}{(cleanLastName(p.lastName)[0] ?? '')}
                        </div>
                        <div style={{ position: 'absolute', bottom: -2, right: -2, width: 14, height: 14, borderRadius: 9999, border: '2px solid #FFF', background: p.isAccepted ? 'var(--status-success)' : 'var(--status-danger)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                          <Icon name={p.isAccepted ? 'check' : 'x'} size={8} style={{ color: '#FFF' }} />
                        </div>
                      </div>

                      {/* Info */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {fullName(p.firstName, p.lastName)}
                          </span>
                          {p.seniority && (
                            <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, textTransform: 'uppercase', flexShrink: 0, ...seniorityStyle(p.seniority) }}>
                              {p.seniority.replace('_', ' ')}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--fg-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {p.title} at {companyName}
                        </div>
                        {!p.isEnriched && (
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
            {loading && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.7)', zIndex: 10 }}>
                <Icon name="loader" size={28} style={{ color: '#4F46E5' }} />
              </div>
            )}
            {active ? (
              <div style={{ padding: '32px 36px', maxWidth: 600, margin: '0 auto' }}>
                {/* Avatar + name */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 18, marginBottom: 28 }}>
                  <div style={{ width: 72, height: 72, borderRadius: 9999, background: 'linear-gradient(135deg, #4F46E5, #7C3AED)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 26, fontWeight: 700, color: '#FFF', border: '1px solid #E5E7EB', flexShrink: 0 }}>
                    {(active.firstName?.[0] ?? '?')}{(cleanLastName(active.lastName)[0] ?? '')}
                  </div>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
                      <h2 style={{ fontSize: 22, fontWeight: 700, color: 'var(--fg-primary)', margin: 0 }}>
                        {fullName(active.firstName, active.lastName)}
                      </h2>
                      {active.seniority && (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, textTransform: 'uppercase', ...seniorityStyle(active.seniority) }}>
                          {active.seniority.replace('_', ' ')}
                        </span>
                      )}
                    </div>
                    <p style={{ fontSize: 14, color: 'var(--fg-muted)', margin: 0 }}>
                      {active.title} at <span style={{ color: '#4F46E5', fontWeight: 600 }}>{companyName}</span>
                    </p>
                  </div>
                </div>

                {/* Location + LinkedIn */}
                <div style={{ display: 'flex', gap: 20, marginBottom: 28, fontSize: 13, color: 'var(--fg-muted)', flexWrap: 'wrap' }}>
                  {active.prospectDetails?.location && (
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Icon name="map-pin" size={14} /> {active.prospectDetails.location}
                    </span>
                  )}
                  {active.prospectDetails?.linkedinUrl && (
                    <a href={active.prospectDetails.linkedinUrl} target="_blank" rel="noreferrer" style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#4F46E5', textDecoration: 'none', fontWeight: 500 }}>
                      <Icon name="linkedin" size={14} /> LinkedIn profile
                    </a>
                  )}
                </div>

                {/* Contact + email draft preview */}
                {(() => {
                  // Persona name — use first name only when last name is empty/null/placeholder
                  const cleanedLast = cleanLastName(active.lastName);
                  const personaName = cleanedLast
                    ? `${(active.firstName || '').trim()} ${cleanedLast}`.trim()
                    : ((active.firstName || '').trim() || 'there');

                  const draftSubject = `Quick thought on the ${jobTitle} role at ${companyName}`;
                  const draftBody =
`Hi ${personaName},

I noticed ${companyName} is hiring for the ${jobTitle} role — congrats on the search.

I lead a specialised practice placing senior leaders in roles like this one, and we've already built a shortlist of pre-vetted candidates who'd be a strong fit for what ${companyName} is looking for.

Would you be open to a 15-minute call this week so I can walk you through a few profiles? Happy to share them ahead of time if that's easier.

Best,
[Your name]`;

                  const fullDraft = `Subject: ${draftSubject}\n\n${draftBody}`;
                  const mailtoHref = active.email
                    ? `mailto:${active.email}?subject=${encodeURIComponent(draftSubject)}&body=${encodeURIComponent(draftBody)}`
                    : null;
                  const isEnriching = enrichingId === active._id;

                  return (
                    <div style={{ marginBottom: 24 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <Icon name="mail" size={14} /> Contact
                      </div>

                      {/* Email row — shows enriched email OR a soft "pending enrichment" pill */}
                      <div style={{ border: '1px solid var(--border-card)', borderRadius: 10, overflow: 'hidden', marginBottom: 12 }}>
                        <div style={{ background: '#F2F4F6', padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
                            <span style={{ color: 'var(--fg-muted)' }}>Email:</span>
                            {active.email ? (
                              <span style={{ fontWeight: 700, color: '#4F46E5', background: '#EEF2FF', padding: '2px 8px', borderRadius: 4 }}>{active.email}</span>
                            ) : (
                              <span style={{ fontWeight: 600, color: '#92400E', background: '#FFFBEB', border: '1px solid #FDE68A', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                                Pending enrichment
                              </span>
                            )}
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            {active.email && (
                              <button
                                onClick={() => copyEmail(active.email!)}
                                style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none', background: 'transparent', color: copiedEmail === active.email ? '#059669' : 'var(--fg-muted)', display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'inherit' }}
                              >
                                <Icon name={copiedEmail === active.email ? 'check-check' : 'copy'} size={13} />
                                {copiedEmail === active.email ? 'Copied!' : 'Copy'}
                              </button>
                            )}
                            {!active.isEnriched && (
                              <button
                                onClick={() => handleEnrich(active)}
                                disabled={isEnriching}
                                style={{
                                  fontSize: 12, fontWeight: 700, cursor: isEnriching ? 'wait' : 'pointer',
                                  border: 'none', borderRadius: 6, padding: '5px 12px',
                                  background: isEnriching ? '#C7D2FE' : '#4F46E5', color: '#FFF',
                                  display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'inherit',
                                }}
                              >
                                <Icon name={isEnriching ? 'loader' : 'sparkles'} size={13} />
                                {isEnriching ? 'Enriching…' : active.email ? 'Re-enrich' : 'Enrich email'}
                              </button>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Enrichment error / no-email notice */}
                      {enrichError && enrichingId === null && (
                        <div style={{ marginBottom: 12, padding: '8px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 12, color: '#B91C1C' }}>
                          {enrichError}
                        </div>
                      )}

                      {/* ─── Phone row ─────────────────────────────────── */}
                      {(() => {
                        const phone = active.prospectDetails?.phone;
                        const mobileStatus = active.mobileEnrichmentStatus;
                        const isPhoneEnriching = phoneEnrichingId === active._id;

                        return (
                          <div style={{ border: '1px solid var(--border-card)', borderRadius: 10, overflow: 'hidden', marginBottom: 12 }}>
                            <div style={{ background: '#F2F4F6', padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
                                <span style={{ color: 'var(--fg-muted)' }}>Phone:</span>
                                {phone ? (
                                  <span style={{ fontWeight: 700, color: '#059669', background: '#ECFDF5', padding: '2px 8px', borderRadius: 4 }}>{phone}</span>
                                ) : mobileStatus === 'pending' ? (
                                  <span style={{ fontWeight: 600, color: '#1D4ED8', background: '#EFF6FF', border: '1px solid #BFDBFE', padding: '2px 8px', borderRadius: 4, fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                    <Icon name="loader" size={11} /> Pending webhook
                                  </span>
                                ) : (
                                  <span style={{ fontWeight: 600, color: '#6B7280', background: '#F3F4F6', border: '1px solid #E5E7EB', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                                    Not revealed
                                  </span>
                                )}
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                {phone && (
                                  <button
                                    onClick={() => copyPhone(phone)}
                                    style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none', background: 'transparent', color: copiedPhone === phone ? '#059669' : 'var(--fg-muted)', display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'inherit' }}
                                  >
                                    <Icon name={copiedPhone === phone ? 'check-check' : 'copy'} size={13} />
                                    {copiedPhone === phone ? 'Copied!' : 'Copy'}
                                  </button>
                                )}
                                {phone && (
                                  <a
                                    href={`tel:${phone}`}
                                    style={{ fontSize: 12, fontWeight: 600, textDecoration: 'none', color: '#4F46E5', display: 'flex', alignItems: 'center', gap: 4 }}
                                  >
                                    <Icon name="phone" size={13} /> Call
                                  </a>
                                )}
                                {!phone && mobileStatus !== 'pending' && (
                                  <button
                                    onClick={() => handlePhoneEnrich(active)}
                                    disabled={isPhoneEnriching}
                                    style={{
                                      fontSize: 12, fontWeight: 700, cursor: isPhoneEnriching ? 'wait' : 'pointer',
                                      border: 'none', borderRadius: 6, padding: '5px 12px',
                                      background: isPhoneEnriching ? '#A7F3D0' : '#059669', color: '#FFF',
                                      display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'inherit',
                                    }}
                                  >
                                    <Icon name={isPhoneEnriching ? 'loader' : 'phone'} size={13} />
                                    {isPhoneEnriching ? 'Revealing…' : 'Find phone'}
                                  </button>
                                )}
                                {mobileStatus === 'pending' && !phone && (
                                  <button
                                    onClick={() => handlePhoneEnrich(active)}
                                    disabled={isPhoneEnriching}
                                    style={{
                                      fontSize: 12, fontWeight: 600, cursor: isPhoneEnriching ? 'wait' : 'pointer',
                                      border: '1px solid #BFDBFE', borderRadius: 6, padding: '5px 12px',
                                      background: '#EFF6FF', color: '#1D4ED8',
                                      display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'inherit',
                                    }}
                                  >
                                    <Icon name={isPhoneEnriching ? 'loader' : 'refresh-cw'} size={13} />
                                    {isPhoneEnriching ? 'Checking…' : 'Retry'}
                                  </button>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })()}

                      {/* Phone enrichment error */}
                      {phoneEnrichError && phoneEnrichingId === null && (
                        <div style={{ marginBottom: 12, padding: '8px 14px', borderRadius: 8, background: '#FEF2F2', border: '1px solid #FECACA', fontSize: 12, color: '#B91C1C' }}>
                          {phoneEnrichError}
                        </div>
                      )}

                      {/* Email draft preview */}
                      <div style={{ border: '1px solid var(--border-card)', borderRadius: 10, overflow: 'hidden', background: '#FFF' }}>
                        {/* Preview header */}
                        <div style={{ padding: '10px 16px', background: '#FAFBFC', borderBottom: '1px solid var(--border-card)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Icon name="sparkles" size={13} style={{ color: '#4F46E5' }} />
                            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--fg-primary)' }}>Email Draft Preview</span>
                            <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--fg-muted)', background: 'var(--border-card)', padding: '2px 7px', borderRadius: 9999, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                              Auto-personalised
                            </span>
                          </div>
                          <button
                            onClick={() => copyEmail(fullDraft)}
                            style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none', background: 'transparent', color: copiedEmail === fullDraft ? '#059669' : 'var(--fg-muted)', display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'inherit' }}
                          >
                            <Icon name={copiedEmail === fullDraft ? 'check-check' : 'copy'} size={13} />
                            {copiedEmail === fullDraft ? 'Copied!' : 'Copy'}
                          </button>
                        </div>

                        {/* Subject */}
                        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-card)' }}>
                          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>
                            Subject
                          </div>
                          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>
                            {draftSubject}
                          </div>
                        </div>

                        {/* Body */}
                        <div style={{
                          padding: '14px 16px',
                          fontSize: 13,
                          lineHeight: 1.6,
                          color: 'var(--fg-secondary)',
                          whiteSpace: 'pre-wrap',
                          fontFamily: 'inherit',
                        }}>
                          {draftBody}
                        </div>

                        {/* Action footer — open the prefilled email in the user's mail client */}
                        <div style={{ padding: '10px 16px', background: '#FAFBFC', borderTop: '1px solid var(--border-card)', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 10 }}>
                          {!active.email && (
                            <span style={{ fontSize: 11, color: 'var(--fg-muted)', marginRight: 'auto' }}>
                              Enrich the prospect to unlock their email.
                            </span>
                          )}
                          {mailtoHref ? (
                            <a
                              href={mailtoHref}
                              style={{ fontSize: 12, fontWeight: 700, textDecoration: 'none', borderRadius: 6, padding: '7px 14px', background: '#4F46E5', color: '#FFF', display: 'flex', alignItems: 'center', gap: 6 }}
                            >
                              <Icon name="mail" size={13} /> Send email
                            </a>
                          ) : (
                            <button
                              disabled
                              style={{ fontSize: 12, fontWeight: 700, borderRadius: 6, padding: '7px 14px', background: '#E5E7EB', color: '#9CA3AF', border: 'none', display: 'flex', alignItems: 'center', gap: 6, cursor: 'not-allowed' }}
                            >
                              <Icon name="mail" size={13} /> Send email
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })()}

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
            ) : !loading ? (
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', textAlign: 'center', padding: 40, height: '100%' }}>
                <Icon name="users" size={64} style={{ color: '#EEF2FF', marginBottom: 16 }} />
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-primary)', marginBottom: 8 }}>No prospect selected</div>
                <div style={{ fontSize: 13, color: 'var(--fg-muted)' }}>Pick a prospect from the left to view their details.</div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
