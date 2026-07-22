'use client';

import { useState, useEffect } from 'react';
import { Icon } from './Icon';
import { createPipeline, addJobToPipeline, createManualJob, type Pipeline } from '@/lib/api';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  /** Called on success. `jobId` set = a role was created too, so the caller can
   *  jump STRAIGHT into that job's AI-prefilled candidate search. */
  onCreated: (pipeline: Pipeline, jobId?: string) => void;
}

/**
 * ONE consolidated screen: company + the role, one submit.
 *
 * This replaced a two-step wizard (Create Pipeline → separate Add Jobs form
 * with a job-search list) that made the recruiter re-enter details on every
 * pipeline. The job title/location/JD collected here are the SAME inputs the
 * AI filter-suggestion step reads, so after this one form the recruiter lands
 * directly on the prefilled search — review, edit, run.
 *
 * Failure model: the three backend calls (create pipeline → create job → add
 * job) run as a chain. If the pipeline is created but the job step fails, the
 * pipeline is KEPT and the next submit resumes from the job step instead of
 * erroring with "pipeline already exists" — no orphaned state, no dead end.
 */
export function CreatePipelineModal({ isOpen, onClose, onCreated }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Company
  const [companyName, setCompanyName] = useState('');
  const [companyDomain, setCompanyDomain] = useState('');
  const [companyIndustry, setCompanyIndustry] = useState('');
  const [companyLocation, setCompanyLocation] = useState('');

  // Role
  const [jobTitle, setJobTitle] = useState('');
  const [jobLocation, setJobLocation] = useState('');
  const [jobDescription, setJobDescription] = useState('');

  // Resume point when the pipeline was created but the job step failed.
  const [createdPipeline, setCreatedPipeline] = useState<Pipeline | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setCompanyName(''); setCompanyDomain(''); setCompanyIndustry(''); setCompanyLocation('');
    setJobTitle(''); setJobLocation(''); setJobDescription('');
    setCreatedPipeline(null);
    setError(null);
    setBusy(false);
  }, [isOpen]);

  const handleCreate = async () => {
    if (!jobTitle.trim()) {
      setError('Role title is required — it drives the candidate search.');
      return;
    }
    // Company is optional, but a lone name or domain is almost certainly a
    // typo'd submission rather than a deliberate "I only know one of these" —
    // ask for both or neither, same rule the backend enforces.
    if (!createdPipeline && Boolean(companyName.trim()) !== Boolean(companyDomain.trim())) {
      setError('Provide both company name and domain, or leave both blank.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      // Step 1 — pipeline (skipped when resuming after a job-step failure).
      let pipeline = createdPipeline;
      if (!pipeline) {
        try {
          pipeline = await createPipeline({
            companyName: companyName.trim() || undefined,
            companyDomain: companyDomain.trim() || undefined,
            companyIndustry: companyIndustry.trim() || undefined,
            companyLocation: companyLocation.trim() || undefined,
          });
          setCreatedPipeline(pipeline);
        } catch (e: any) {
          const msg = e.message || '';
          setError(msg.includes('pipeline_already_exists')
            ? 'A pipeline for this company already exists — open it from the list instead.'
            : msg || 'Failed to create pipeline');
          return;
        }
      }

      // Step 2 — the role, attached to the pipeline. The JD pasted here is what
      // the AI reads to prefill the search filters and the match requirements.
      const job = await createManualJob({
        title: jobTitle.trim(),
        location: jobLocation.trim() || undefined,
        companyId: pipeline.companyId || undefined,
        description: jobDescription.trim() || undefined,
      });
      await addJobToPipeline(pipeline._id, job._id);

      onCreated(pipeline, job._id);
      onClose();
    } catch (e: any) {
      // Pipeline exists at this point — the next click resumes from the job step.
      setError(`${e.message || 'Failed to create the role'} — the pipeline was created; press the button again to retry the role.`);
    } finally {
      setBusy(false);
    }
  };

  if (!isOpen) return null;

  const inputStyle: React.CSSProperties = {
    width: '100%', height: 38, padding: '0 12px', borderRadius: 8,
    border: '1px solid var(--border-card)', fontSize: 13, fontFamily: 'inherit',
    background: '#FFF', color: 'var(--fg-primary)', outline: 'none',
  };
  const labelStyle: React.CSSProperties = {
    fontSize: 12, fontWeight: 600, color: 'var(--fg-secondary)',
    marginBottom: 4, display: 'block',
  };
  const sectionTitle: React.CSSProperties = {
    fontSize: 11, fontWeight: 700, color: 'var(--fg-muted)',
    textTransform: 'uppercase', letterSpacing: '0.06em',
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 70, background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: '100%', maxWidth: 560, background: '#FFF', borderRadius: 12,
          boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
          maxHeight: '86vh', display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{
          padding: '20px 24px 16px', borderBottom: '1px solid var(--border-card)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 36, height: 36, borderRadius: 9999, background: '#EEF2FF', color: '#4F46E5',
            }}>
              <Icon name="building" size={18} />
            </span>
            <div>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)' }}>
                New candidate pipeline
              </div>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                Start from the role — the AI prefills the search from what you enter here.
                Company details are optional and can be added later.
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 30, height: 30, borderRadius: 6, cursor: 'pointer',
              border: '1px solid var(--border-card)', background: '#FFF', color: 'var(--fg-muted)',
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '18px 24px', overflow: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {error && (
            <div style={{
              padding: '10px 14px', borderRadius: 8, background: '#FEF2F2',
              border: '1px solid #FECACA', fontSize: 13, color: '#B91C1C', lineHeight: 1.5,
            }}>
              {error}
            </div>
          )}

          {/* Role */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <span style={sectionTitle}>The role you&apos;re hiring for</span>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1.4 }}>
                <label style={labelStyle}>Job Title *</label>
                <input
                  style={inputStyle} placeholder="e.g. Senior SAP FI Consultant" autoFocus
                  value={jobTitle} disabled={busy}
                  onChange={(e) => setJobTitle(e.target.value)}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Job Location</label>
                <input
                  style={inputStyle} placeholder="e.g. Berlin, Germany"
                  value={jobLocation} disabled={busy}
                  onChange={(e) => setJobLocation(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label style={labelStyle}>Job description</label>
              <textarea
                style={{ ...inputStyle, height: 96, padding: '8px 12px', resize: 'vertical' }}
                placeholder="Paste the JD or the key requirements — the AI reads this to propose the search filters and the must-have skills. More detail here = sharper candidates."
                value={jobDescription} disabled={busy}
                onChange={(e) => setJobDescription(e.target.value)}
              />
            </div>
          </div>

          {/* Company — optional. A pipeline started directly from a role doesn't
             need one yet; it fills in automatically if this role is later mapped
             from a lead/campaign. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, opacity: createdPipeline ? 0.55 : 1 }}>
            <span style={sectionTitle}>The company <span style={{ textTransform: 'none', fontWeight: 500, letterSpacing: 0 }}>(optional)</span></span>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Company Name</label>
                <input
                  style={inputStyle} placeholder="e.g. Acme Corp"
                  value={companyName} disabled={!!createdPipeline || busy}
                  onChange={(e) => setCompanyName(e.target.value)}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Company Domain</label>
                <input
                  style={inputStyle} placeholder="e.g. acme.com"
                  value={companyDomain} disabled={!!createdPipeline || busy}
                  onChange={(e) => setCompanyDomain(e.target.value)}
                />
              </div>
            </div>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Industry</label>
                <input
                  style={inputStyle} placeholder="e.g. Software Development"
                  value={companyIndustry} disabled={!!createdPipeline || busy}
                  onChange={(e) => setCompanyIndustry(e.target.value)}
                />
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>Location</label>
                <input
                  style={inputStyle} placeholder="e.g. Munich, DE"
                  value={companyLocation} disabled={!!createdPipeline || busy}
                  onChange={(e) => setCompanyLocation(e.target.value)}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          padding: '16px 24px', borderTop: '1px solid var(--border-card)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
            Next: review the AI-suggested search filters, then run.
          </span>
          <div style={{ display: 'flex', gap: 10 }}>
            <button
              onClick={onClose}
              disabled={busy}
              style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 500,
                cursor: 'pointer', border: '1px solid var(--border-card)',
                background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={busy || !jobTitle.trim()}
              style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                cursor: busy ? 'not-allowed' : 'pointer', border: 'none',
                background: '#4F46E5', color: '#FFF', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              {busy ? <Icon name="loader" size={14} /> : <Icon name="arrow-right" size={14} />}
              {createdPipeline ? 'Retry — create the role' : 'Create & find candidates'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
