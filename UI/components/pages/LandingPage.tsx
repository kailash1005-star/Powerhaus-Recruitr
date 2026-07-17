'use client';

import Link from 'next/link';
import { useUser } from '@auth0/nextjs-auth0';
import { useState, type ReactNode } from 'react';
import { Icon } from '../Icon';
import { Cta, Logo, Pill, Section } from '../marketing/shared';

/* ── Recruitr landing page ────────────────────────────────────────────────────
   Layout rhythm carried over from the Kadai marketing page, re-skinned in the
   Recruitr language: near-black actions, navy (--primary) brand bands, borders
   instead of shadows, Inter. Copy is Germany-first, drawn from
   Recruitr_Client_OnePager.md and Recruitr_Sales_OnePager.md — every figure on
   the page is attributed to its published source. */

const NAV_LINKS = [
  { href: '#what', label: 'What it does' },
  { href: '#how', label: 'How it works' },
  { href: '#trust', label: 'Trust' },
  { href: '#faq', label: 'FAQ' },
];

/** The landing page stays public and is not redirected away from — a signed-in
 *  user may legitimately want to read it. But offering them "Sign in" when they
 *  already are is just a dead end, so the CTA becomes a way back into the app. */
function useSignedIn() {
  const { user, isLoading } = useUser();
  return { signedIn: Boolean(user), isLoading };
}

function Nav() {
  const [open, setOpen] = useState(false);
  const { signedIn } = useSignedIn();

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 40,
        background: 'rgba(255,255,255,0.85)',
        backdropFilter: 'blur(8px)',
        borderBottom: '1px solid var(--border-default)',
      }}
    >
      <div
        className="mk-container"
        style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
      >
        <Link href="/" style={{ textDecoration: 'none' }}>
          <Logo />
        </Link>

        <nav className="mk-nav-links">
          {NAV_LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              style={{ fontSize: 14, color: 'var(--fg-muted)', textDecoration: 'none' }}
            >
              {l.label}
            </a>
          ))}
        </nav>

        <div className="mk-nav-cta">
          {signedIn ? (
            <Cta href="/runs" size="sm">
              Go to app <Icon name="arrow-right" size={14} />
            </Cta>
          ) : (
            <>
              <Cta href="/login" tone="outline" size="sm">
                Sign in
              </Cta>
              <Cta href="#pilot" size="sm">
                Book a pilot <Icon name="arrow-right" size={14} />
              </Cta>
            </>
          )}
        </div>

        <button
          className="mk-nav-toggle"
          onClick={() => setOpen((o) => !o)}
          aria-label="Menu"
          aria-expanded={open}
          style={{
            background: 'transparent',
            border: 0,
            padding: 8,
            cursor: 'pointer',
            color: 'var(--fg-primary)',
          }}
        >
          <Icon name={open ? 'x' : 'menu'} size={20} />
        </button>
      </div>

      {open && (
        <div
          style={{
            borderTop: '1px solid var(--border-default)',
            background: 'var(--bg-app)',
            padding: '12px 24px 16px',
          }}
        >
          {NAV_LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              style={{
                display: 'block',
                padding: '10px 0',
                fontSize: 14,
                color: 'var(--fg-secondary)',
                textDecoration: 'none',
              }}
            >
              {l.label}
            </a>
          ))}
          <div style={{ display: 'flex', gap: 8, paddingTop: 8 }}>
            {signedIn ? (
              <Cta href="/runs" size="sm">
                Go to app
              </Cta>
            ) : (
              <>
                <Cta href="/login" tone="outline" size="sm">
                  Sign in
                </Cta>
                <Cta href="#pilot" size="sm">
                  Book a pilot
                </Cta>
              </>
            )}
          </div>
        </div>
      )}
    </header>
  );
}

function Hero() {
  return (
    <section style={{ position: 'relative', overflow: 'hidden' }}>
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: -1,
          background: 'linear-gradient(to bottom, var(--band), var(--bg-app))',
        }}
      />
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: -280,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: -1,
          width: 760,
          height: 760,
          borderRadius: 9999,
          filter: 'blur(64px)',
          opacity: 0.35,
          background: 'radial-gradient(circle, rgba(30,58,138,0.14), transparent 60%)',
        }}
      />
      <div className="mk-container" style={{ paddingTop: 88, paddingBottom: 72 }}>
        <div className="mk-hero-grid">
          <div>
            <Pill>Built for staffing teams hiring in Germany</Pill>
            <h1 className="mk-h1" style={{ marginTop: 20 }}>
              We streamline your
              <br />
              hiring lifecycle.
            </h1>
            <p className="mk-lead" style={{ marginTop: 18, maxWidth: 560 }}>
              Recruitr is a team of AI agents that sources, re-mines and ranks candidates — then drafts
              the outreach. It runs on the ATS and the database you already own, so nobody switches
              systems. Your recruiters just stop losing the day to admin.
            </p>
            <div style={{ marginTop: 28, display: 'flex', flexWrap: 'wrap', gap: 12 }}>
              <Cta href="#pilot">
                Book a 30-day pilot <Icon name="arrow-right" size={16} />
              </Cta>
              <Cta href="#how" tone="outline">
                See how it works
              </Cta>
            </div>
            <p style={{ marginTop: 16, fontSize: 12, color: 'var(--fg-subtle)' }}>
              No system switch · One desk · GDPR &amp; EU AI Act ready
            </p>
          </div>
          <ShortlistMock />
        </div>
      </div>
      <SectorStrip />
    </section>
  );
}

const MOCK_MATCHES = [
  { initials: 'AS', name: 'Anna Schmidt', role: 'Senior Payroll Specialist · München', score: 94, why: 'SAP HCM + 8 yrs DACH payroll' },
  { initials: 'TB', name: 'Tomas Bauer', role: 'Product Lead · Berlin', score: 88, why: 'Titled "Lead", scoped as PM' },
  { initials: 'MK', name: 'Mira Kowalski', role: 'Lohnbuchhalterin · Remote', score: 81, why: 'From your database — 2023 finalist' },
];

/** A still of the product's real output: a ranked shortlist, each row with its reason. */
function ShortlistMock() {
  return (
    <div style={{ position: 'relative', maxWidth: 420, margin: '0 auto', width: '100%' }}>
      <div
        style={{
          border: '1px solid var(--border-card)',
          borderRadius: 12,
          background: 'var(--bg-app)',
          overflow: 'hidden',
          boxShadow: 'var(--shadow-drawer)',
        }}
      >
        <div
          style={{
            padding: '12px 16px',
            borderBottom: '1px solid var(--border-default)',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: 'var(--bg-muted)',
          }}
        >
          <Icon name="sparkles" size={15} style={{ color: 'var(--primary)' }} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Shortlist · Payroll Specialist (m/w/d)</span>
        </div>

        <div>
          {MOCK_MATCHES.map((m) => (
            <div
              key={m.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: '14px 16px',
                borderBottom: '1px solid var(--border-default)',
              }}
            >
              <span
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 9999,
                  background: 'var(--bg-chip)',
                  color: 'var(--fg-secondary)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 11,
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {m.initials}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{m.name}</div>
                <div style={{ fontSize: 11, color: 'var(--fg-subtle)' }}>{m.role}</div>
                <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 3 }}>
                  <Icon name="check" size={11} style={{ color: 'var(--status-success)', marginRight: 4 }} />
                  {m.why}
                </div>
              </div>
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13,
                  fontWeight: 500,
                  color: 'var(--primary)',
                  flexShrink: 0,
                }}
              >
                {m.score}
              </span>
            </div>
          ))}
        </div>

        <div style={{ padding: '10px 16px', fontSize: 11, color: 'var(--fg-subtle)' }}>
          Ranked by meaning, not keywords · every score has a reason
        </div>
      </div>

      <FloatChip icon="database" tone="var(--primary)" value="1 of 3" label="from your own bench" style={{ left: -16, top: 64 }} />
      <FloatChip icon="mail" tone="var(--status-success)" value="Drafts ready" label="personalized" style={{ right: -12, bottom: 48 }} />
    </div>
  );
}

function FloatChip({
  icon,
  tone,
  value,
  label,
  style,
}: {
  icon: string;
  tone: string;
  value: string;
  label: string;
  style: React.CSSProperties;
}) {
  return (
    <div
      style={{
        position: 'absolute',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: 'var(--bg-app)',
        border: '1px solid var(--border-card)',
        borderRadius: 10,
        boxShadow: 'var(--shadow-popover)',
        padding: '8px 10px',
        ...style,
      }}
    >
      <Icon name={icon} size={14} style={{ color: tone }} />
      <div style={{ lineHeight: 1.25 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{value}</div>
        <div style={{ fontSize: 10, color: 'var(--fg-muted)' }}>{label}</div>
      </div>
    </div>
  );
}

const SECTORS = [
  'IT & Software', 'Engineering', 'Healthcare', 'Logistics', 'Finance & Accounting',
  'Payroll', 'Manufacturing', 'Life Sciences', 'Construction', 'Automotive',
  'Public Sector', 'Energy', 'Retail', 'Skilled Trades', 'Professional Services',
];

function SectorStrip() {
  const row = [...SECTORS, ...SECTORS];
  return (
    <div style={{ borderTop: '1px solid var(--border-default)', borderBottom: '1px solid var(--border-default)', padding: '24px 0', background: 'rgba(255,255,255,0.6)' }}>
      <p
        style={{
          textAlign: 'center',
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: 'var(--tracking-widest)',
          color: 'var(--fg-subtle)',
          margin: '0 0 16px',
        }}
      >
        Built for the desks agencies actually run
      </p>
      <div className="mk-marquee-mask">
        <div className="mk-marquee-track">
          {row.map((s, i) => (
            <span
              key={i}
              style={{
                flexShrink: 0,
                whiteSpace: 'nowrap',
                margin: '0 20px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 10,
                fontSize: 14,
                fontWeight: 500,
                color: 'var(--fg-muted)',
              }}
            >
              <span style={{ width: 5, height: 5, borderRadius: 9999, background: 'var(--border-strong)', flexShrink: 0 }} />
              {s}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

const WHAT_CARDS = [
  {
    icon: 'search',
    title: 'It brings you clients',
    body: 'Recruitr scans job boards for companies hiring in your target industries, qualifies them automatically — right industry, right size, other agencies screened out — and hands you the decision-makers to pitch.',
  },
  {
    icon: 'database',
    title: 'It fills roles from your own bench first',
    body: 'Paste a role and Recruitr ranks your existing CV database by meaning, not keywords. The strong people your current search buries come back up — with a score, the reasons, and the gaps.',
  },
  {
    icon: 'users',
    title: 'It sources new talent when you need it',
    body: 'No match in your pool? Recruitr searches the public market via LinkedIn and Apollo, ranks what it finds, and drafts warm, personalized outreach to candidates and clients alike.',
  },
];

function WhatIs() {
  return (
    <Section
      id="what"
      eyebrow="What is Recruitr"
      title="Two sides of your desk. One platform."
      subtitle="Most tools help you find candidates or clients. Recruitr works both — on the data and contacts you already have."
    >
      <div className="mk-grid-3">
        {WHAT_CARDS.map((c) => (
          <div key={c.title} className="mk-card">
            <span
              style={{
                width: 38,
                height: 38,
                borderRadius: 8,
                background: 'var(--bg-chip)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Icon name={c.icon} size={18} style={{ color: 'var(--fg-primary)' }} />
            </span>
            <h3 className="mk-h3" style={{ marginTop: 16 }}>{c.title}</h3>
            <p className="mk-body" style={{ marginTop: 8 }}>{c.body}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

const AGENTS = [
  { icon: 'file-text', title: 'JD Parser', status: 'Live', body: 'Reads the role and extracts skills, seniority, experience and location — one canonical parse that drives both the search and the scoring.' },
  { icon: 'radar', title: 'Candidate Search', status: 'Live', body: 'Sources and scores active candidates across the public market, retrying and broadening on its own when a search comes back thin.' },
  { icon: 'sparkles', title: 'Match & Rank', status: 'Shipping', body: 'Ranks every candidate with a plain-language reason for the decision — a defensible score per person, not an opaque keyword hit.' },
  { icon: 'mail', title: 'Personalize → Outreach', status: 'Shipping', body: 'Drafts tailored messages to candidates and hiring contacts, sends them, and tracks the replies back into the pipeline.' },
];

function Agents() {
  return (
    <Section
      eyebrow="The agentic lifecycle"
      title="A team of agents, one per stage of the funnel."
      subtitle="Each one does the admin a recruiter would otherwise do by hand — and shows its work at every step."
    >
      <div className="mk-grid-2">
        {AGENTS.map((a) => (
          <div key={a.title} className="mk-card" style={{ display: 'flex', gap: 16 }}>
            <span
              style={{
                width: 38,
                height: 38,
                flexShrink: 0,
                borderRadius: 8,
                background: 'var(--status-info-bg)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Icon name={a.icon} size={18} style={{ color: 'var(--primary)' }} />
            </span>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h3 className="mk-h3" style={{ fontSize: 15 }}>{a.title}</h3>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.04em',
                    padding: '2px 6px',
                    borderRadius: 4,
                    background: a.status === 'Live' ? 'var(--status-success-bg)' : 'var(--bg-chip)',
                    color: a.status === 'Live' ? 'var(--status-success)' : 'var(--fg-muted)',
                  }}
                >
                  {a.status}
                </span>
              </div>
              <p className="mk-body" style={{ marginTop: 6 }}>{a.body}</p>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

const STEPS = [
  { n: '1', title: 'Keep your stack', body: 'Recruitr is vendor-neutral and plugs into the ATS/CRM you already own. No migration, no rip-and-replace, no new system for your recruiters to live in.' },
  { n: '2', title: 'Drop in a role', body: 'Paste the job description. The agents parse it, re-mine your database for past-best matches, and search the public market for the rest.' },
  { n: '3', title: 'Work the shortlist', body: 'You get a ranked shortlist with evidence per candidate and outreach drafted and ready. A human always decides who moves.' },
];

function How() {
  return (
    <section id="how" style={{ background: 'var(--primary)', color: 'var(--primary-fg)' }}>
      <div className="mk-container mk-section">
        <div style={{ maxWidth: 660 }}>
          <p className="mk-eyebrow" style={{ color: 'rgba(255,255,255,0.6)' }}>How it works</p>
          <h2 className="mk-h2">From job description to shortlist in three steps.</h2>
        </div>
        <div className="mk-grid-3" style={{ marginTop: 40 }}>
          {STEPS.map((s) => (
            <div
              key={s.n}
              style={{
                borderRadius: 12,
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'rgba(255,255,255,0.03)',
                padding: 24,
              }}
            >
              <span
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 8,
                  background: '#FFFFFF',
                  color: 'var(--primary)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontWeight: 700,
                }}
              >
                {s.n}
              </span>
              <h3 className="mk-h3" style={{ marginTop: 16 }}>{s.title}</h3>
              <p style={{ marginTop: 8, fontSize: 14, lineHeight: 1.5, color: 'rgba(255,255,255,0.7)' }}>{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* Published figures only — each card names its source. Nothing here is a
   Recruitr performance claim; they describe the market the product sells into. */
const MARKET_STATS = [
  { value: '628,000', label: 'roles sit unfilled in Germany, with the average vacancy open ~299 days.', source: 'German Federal Employment Agency, 2025' },
  { value: '88%', label: 'of executives admit qualified people are screened out because a CV misses a job’s exact wording.', source: 'Harvard Business School & Accenture, “Hidden Workers”, 2021' },
  { value: '15–25%', label: 'of margin leaks away through disconnected, manual tooling before an invoice is sent.', source: 'Staffing Industry Analysts' },
];

function Market() {
  return (
    <Section
      eyebrow="The market you’re selling into"
      title="The work is there. Winning it and filling it fast is the hard part."
      subtitle="Germany faces a shortfall of up to 7 million workers by 2035 (German Economic Institute, IW). Demand is not the constraint — manual, keyword-driven tooling is."
    >
      <div className="mk-grid-3">
        {MARKET_STATS.map((s) => (
          <div key={s.value} className="mk-card">
            <p style={{ fontSize: 36, fontWeight: 600, letterSpacing: '-0.02em', margin: 0 }}>{s.value}</p>
            <p className="mk-body" style={{ marginTop: 10, color: 'var(--fg-secondary)' }}>{s.label}</p>
            <p style={{ marginTop: 14, fontSize: 11, color: 'var(--fg-subtle)' }}>{s.source}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

const TRUST = [
  { icon: 'shield-check', title: 'GDPR — your data stays yours', body: 'Fully logged, and never used to train models. Your CVs, your contacts, your contracts — export them whenever you want.' },
  { icon: 'scale', title: 'EU AI Act ready', body: 'Hiring counts as high-risk under the Act. Recruitr is built for it: protected attributes are removed before scoring, and decisions are bias-controlled.' },
  { icon: 'user-check', title: 'Human-in-the-loop by design', body: 'Every ranking carries a plain-language “why,” and a person always makes the call — defensible to your works council, your DPO, and the regulator.' },
];

function Trust() {
  return (
    <Section id="trust" eyebrow="Built for trust" title="Defensible to your works council, your DPO, and the regulator.">
      <div className="mk-grid-3">
        {TRUST.map((t) => (
          <div key={t.title} className="mk-card">
            <Icon name={t.icon} size={20} style={{ color: 'var(--primary)' }} />
            <h3 className="mk-h3" style={{ marginTop: 12 }}>{t.title}</h3>
            <p className="mk-body" style={{ marginTop: 8 }}>{t.body}</p>
          </div>
        ))}
      </div>
    </Section>
  );
}

const FAQS = [
  { q: 'Do we have to replace our ATS?', a: 'No. Recruitr is vendor-neutral and runs on the stack you already own — your data, your contracts, your workflow stay exactly where they are. That is the whole point: no migration, and nothing for your recruiters to re-learn.' },
  { q: 'How is this different from the keyword search we have today?', a: 'Recruitr matches on meaning, not wording. A “Product Lead” never surfaces in a Boolean search for “Product Manager” — semantic matching finds them anyway, which is exactly how the strong candidates your ATS buries come back up.' },
  { q: 'Can we see why a candidate was ranked where they were?', a: 'Yes — every score comes with a plain-language reason and the gaps, per candidate. That is what makes a shortlist defensible to your client, and to a regulator.' },
  { q: 'What about our existing database?', a: 'It is the first place Recruitr looks. Roughly three quarters of the candidates already in an agency database are never looked at again; re-mining them is the cheapest placement you will make.' },
  { q: 'Is this compliant for hiring in the EU?', a: 'It is built for it. GDPR-aligned, fully logged, no training on your data, protected attributes stripped before scoring, and a human decision at every gate — the requirements the EU AI Act places on high-risk hiring tools.' },
  { q: 'How do we start?', a: 'A 30-day pilot on one desk. No system switch, and we measure the lift together. If the numbers are not there, you have lost nothing.' },
];

function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <Section id="faq" eyebrow="Questions" title="Everything you might be wondering.">
      <div style={{ maxWidth: 760, borderTop: '1px solid var(--border-default)' }}>
        {FAQS.map((f, i) => (
          <div key={f.q} style={{ borderBottom: '1px solid var(--border-default)' }}>
            <button
              onClick={() => setOpen(open === i ? null : i)}
              aria-expanded={open === i}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 16,
                padding: '16px 0',
                textAlign: 'left',
                background: 'transparent',
                border: 0,
                cursor: 'pointer',
                font: 'inherit',
                color: 'var(--fg-primary)',
              }}
            >
              <span style={{ fontSize: 15, fontWeight: 500 }}>{f.q}</span>
              <Icon name={open === i ? 'minus' : 'plus'} size={18} style={{ color: 'var(--fg-muted)' }} />
            </button>
            {open === i && (
              <p className="mk-body" style={{ paddingBottom: 16, maxWidth: 660 }}>{f.a}</p>
            )}
          </div>
        ))}
      </div>
    </Section>
  );
}

function FinalCta() {
  return (
    <section id="pilot" className="mk-container" style={{ paddingBottom: 96 }}>
      <div
        style={{
          position: 'relative',
          overflow: 'hidden',
          borderRadius: 16,
          background: 'var(--primary)',
          color: 'var(--primary-fg)',
          padding: '72px 32px',
          textAlign: 'center',
        }}
      >
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: -220,
            left: '50%',
            transform: 'translateX(-50%)',
            width: 560,
            height: 560,
            borderRadius: 9999,
            filter: 'blur(64px)',
            opacity: 0.25,
            background: 'radial-gradient(circle, rgba(255,255,255,0.5), transparent 60%)',
            pointerEvents: 'none',
          }}
        />
        <h2 className="mk-h2" style={{ position: 'relative', maxWidth: 660, margin: '0 auto' }}>
          See it on your own roles.
        </h2>
        <p
          style={{
            position: 'relative',
            marginTop: 14,
            fontSize: 16,
            lineHeight: 1.5,
            color: 'rgba(255,255,255,0.72)',
            maxWidth: 580,
            marginLeft: 'auto',
            marginRight: 'auto',
          }}
        >
          Give us a handful of your live roles and your candidate CVs. In one session we will show you
          the shortlists, the reasons, and the client leads — on your data. Then run a 30-day pilot on
          one desk, and we measure the lift together.
        </p>
        <div style={{ position: 'relative', marginTop: 28, display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 12 }}>
          <Cta href="mailto:hello@recruitr.io?subject=Recruitr%20pilot" tone="onDark">
            Book a pilot session <Icon name="arrow-right" size={16} />
          </Cta>
          <Cta href="/login" tone="onDarkOutline">
            Sign in
          </Cta>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer style={{ borderTop: '1px solid var(--border-default)' }}>
      <div
        className="mk-container"
        style={{
          padding: '32px 24px',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
        }}
      >
        <Logo />
        <p style={{ fontSize: 12, color: 'var(--fg-muted)', margin: 0 }}>
          Recruitr — the agentic hiring lifecycle, on the stack you already own.
        </p>
        <div style={{ display: 'flex', gap: 20 }}>
          {['#what', '#how', '#trust'].map((h, i) => (
            <a key={h} href={h} style={{ fontSize: 12, color: 'var(--fg-muted)', textDecoration: 'none' }}>
              {['What it does', 'How it works', 'Trust'][i]}
            </a>
          ))}
          <Link href="/login" style={{ fontSize: 12, color: 'var(--fg-muted)', textDecoration: 'none' }}>
            Sign in
          </Link>
        </div>
      </div>
    </footer>
  );
}

export function LandingPage() {
  return (
    <div style={{ background: 'var(--bg-app)', color: 'var(--fg-primary)' }}>
      <Nav />
      <main>
        <Hero />
        <WhatIs />
        <Agents />
        <How />
        <Market />
        <Trust />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}
