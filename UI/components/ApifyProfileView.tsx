'use client';

import { Icon } from './Icon';
import type { Candidate, ApifyProfile } from '@/lib/api';

/**
 * Renders the deep LinkedIn profile pulled from Apify (about, experience with
 * descriptions, education, skills, certifications, languages). Shared by the
 * candidates slide-out and the matching-run slide-over so both surfaces show the
 * same "full data" the user judges the candidate on.
 *
 * Also owns the intermediate states of the background Apify stage
 * (pending / not_found / failed) so callers just hand it a candidate.
 */

const sectionLabel: React.CSSProperties = {
  fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)',
  textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10,
};

const YEAR_RE = /^(\d{4})/;

// Apify date keys are '2022-11' or '2022'; show just the year.
function yearOf(key?: string | null): string {
  if (!key) return '';
  const m = YEAR_RE.exec(String(key));
  return m ? m[1] : '';
}

function dateRange(start?: string | null, end?: string | null, current?: boolean): string {
  const s = yearOf(start);
  if (current) return s ? `${s} – Present` : 'Present';
  const e = yearOf(end);
  if (s && e) return s === e ? s : `${s} – ${e}`;
  return s || e || '';
}

function Chip({ children, tone = 'muted' }: { children: React.ReactNode; tone?: 'muted' | 'accent' }) {
  const accent = tone === 'accent';
  return (
    <span style={{
      padding: '3px 9px', borderRadius: 9999, fontSize: 11,
      fontWeight: accent ? 600 : 500,
      background: accent ? '#EEF2FF' : '#F3F4F6',
      color: accent ? '#4F46E5' : 'var(--fg-secondary)',
      border: `1px solid ${accent ? '#DDD6FE' : 'var(--border-card)'}`,
    }}>{children}</span>
  );
}

function StateBox({ icon, iconColor, title, subtitle, spinning }: {
  icon: string; iconColor: string; title: string; subtitle?: string; spinning?: boolean;
}) {
  return (
    <div style={{
      padding: '20px', textAlign: 'center', border: '1px dashed var(--border-card)',
      borderRadius: 10, background: '#FAFAFA',
    }}>
      <Icon name={icon} size={22} style={{ color: iconColor, marginBottom: 8, ...(spinning ? {} : {}) }} />
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>{title}</div>
      {subtitle && <div style={{ fontSize: 12, color: 'var(--fg-muted)', marginTop: 4 }}>{subtitle}</div>}
    </div>
  );
}

export function ApifyProfileView({ candidate }: { candidate: Candidate }) {
  const status = candidate.apifyEnrichmentStatus;
  const profile: ApifyProfile | null | undefined = candidate.apifyEnrichment?.profile;
  const hasProfile = !!(candidate.isApifyEnriched && profile);

  // Header shared by every non-empty state.
  const Header = (
    <div style={{ ...sectionLabel, display: 'flex', alignItems: 'center', gap: 6 }}>
      <Icon name="linkedin" size={14} style={{ color: '#0A66C2' }} /> LinkedIn Profile
      {hasProfile && profile?.totalYears != null && (
        <span style={{
          marginLeft: 4, fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 9999,
          background: '#ECFDF5', color: '#059669', border: '1px solid #A7F3D0',
          textTransform: 'none', letterSpacing: 0,
        }}>{profile.totalYears} yrs exp</span>
      )}
    </div>
  );

  // ── Intermediate states of the background Apify stage ─────────────────────
  if (!hasProfile) {
    if (status === 'pending') {
      return (
        <div style={{ marginBottom: 20 }}>
          {Header}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px',
            border: '1px solid #DDD6FE', borderRadius: 10, background: '#F5F3FF',
          }}>
            <Icon name="loader" size={16} style={{ color: '#4F46E5' }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#4F46E5' }}>Fetching full LinkedIn profile…</div>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>Pulling experience, education & skills. This runs in the background.</div>
            </div>
          </div>
        </div>
      );
    }
    if (status === 'not_found') {
      return (
        <div style={{ marginBottom: 20 }}>
          {Header}
          <StateBox icon="user-x" iconColor="var(--fg-muted)"
            title="No public LinkedIn profile found"
            subtitle="The contact details above are still available." />
        </div>
      );
    }
    if (status === 'failed') {
      return (
        <div style={{ marginBottom: 20 }}>
          {Header}
          <StateBox icon="alert-triangle" iconColor="#D97706"
            title="Couldn't fetch the LinkedIn profile"
            subtitle={candidate.apifyEnrichmentError || 'A transient error occurred — try enriching again.'} />
        </div>
      );
    }
    // No status yet and nothing to show → render nothing.
    return null;
  }

  const p = profile as ApifyProfile;
  const experience = p.experience ?? [];
  const education = p.education ?? [];
  const skills = p.skills ?? [];
  const certifications = p.certifications ?? [];
  const languages = p.languages ?? [];

  return (
    <div style={{ marginBottom: 20 }}>
      {Header}

      {/* About / summary */}
      {p.summary && (
        <div style={{ marginBottom: 16 }}>
          <div style={sectionLabel}>About</div>
          <div style={{ fontSize: 13, color: 'var(--fg-secondary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
            {p.summary}
          </div>
        </div>
      )}

      {/* Experience — with descriptions + per-role skills */}
      {experience.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={sectionLabel}>Experience</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {experience.map((e, i) => (
              <div key={i} style={{ padding: '12px 14px', background: 'var(--bg-app)', borderRadius: 8, border: '1px solid var(--border-card)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-primary)' }}>
                    {e.title || '—'}
                    {e.is_current && (
                      <span style={{ marginLeft: 8, fontSize: 10, fontWeight: 700, color: '#059669', background: '#ECFDF5', border: '1px solid #A7F3D0', padding: '1px 6px', borderRadius: 9999 }}>Current</span>
                    )}
                  </div>
                  <span style={{ fontSize: 12, color: 'var(--fg-muted)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                    {dateRange(e.starts_at, e.ends_at, e.is_current)}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--fg-secondary)', marginTop: 2, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {e.company_name && <span style={{ fontWeight: 600 }}>{e.company_name}</span>}
                  {e.employment_type && <span style={{ color: 'var(--fg-muted)' }}>· {e.employment_type}</span>}
                  {e.location && <span style={{ color: 'var(--fg-muted)' }}>· {e.location}</span>}
                </div>
                {e.description && (
                  <div style={{ fontSize: 12, color: 'var(--fg-secondary)', marginTop: 8, lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
                    {e.description}
                  </div>
                )}
                {(e.skills?.length ?? 0) > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 8 }}>
                    {e.skills!.map((s) => <Chip key={s}>{s}</Chip>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Education */}
      {education.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={sectionLabel}>Education</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {education.map((e, i) => (
              <div key={i} style={{ padding: '10px 14px', background: 'var(--bg-app)', borderRadius: 8, border: '1px solid var(--border-card)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-primary)' }}>{e.school_name || '—'}</div>
                  <span style={{ fontSize: 12, color: 'var(--fg-muted)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                    {dateRange(e.starts_at, e.ends_at)}
                  </span>
                </div>
                {(e.degree_name || e.field_of_study) && (
                  <div style={{ fontSize: 12, color: 'var(--fg-secondary)', marginTop: 2 }}>
                    {[e.degree_name, e.field_of_study].filter(Boolean).join(' · ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Skills */}
      {skills.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={sectionLabel}>Skills</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {skills.map((s) => <Chip key={s} tone="accent">{s}</Chip>)}
          </div>
        </div>
      )}

      {/* Certifications */}
      {certifications.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={sectionLabel}>Certifications</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {certifications.map((c, i) => (
              <div key={i} style={{ fontSize: 13, color: 'var(--fg-secondary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Icon name="award" size={13} style={{ color: 'var(--fg-muted)', flexShrink: 0 }} />
                <span><strong style={{ color: 'var(--fg-primary)', fontWeight: 600 }}>{c.name}</strong>{c.authority ? ` — ${c.authority}` : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Languages */}
      {languages.length > 0 && (
        <div style={{ marginBottom: 4 }}>
          <div style={sectionLabel}>Languages</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {languages.map((l, i) => (
              <Chip key={i}>{l.name}{l.proficiency ? ` · ${l.proficiency}` : ''}</Chip>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
