'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Icon } from './Icon';
import {
  createPipeline, addJobToPipeline, searchJobs, createManualJob,
  type Pipeline, type JobSearchResult,
} from '@/lib/api';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (pipeline: Pipeline) => void;
}

type Step = 'company' | 'jobs';

export function CreatePipelineModal({ isOpen, onClose, onCreated }: Props) {
  const [step, setStep] = useState<Step>('company');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Company form
  const [companyName, setCompanyName] = useState('');
  const [companyDomain, setCompanyDomain] = useState('');
  const [companyIndustry, setCompanyIndustry] = useState('');
  const [companyLocation, setCompanyLocation] = useState('');

  // Created pipeline
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);

  // Job search
  const [jobQuery, setJobQuery] = useState('');
  const [jobResults, setJobResults] = useState<JobSearchResult[]>([]);
  const [jobSearching, setJobSearching] = useState(false);
  const [addedJobIds, setAddedJobIds] = useState<Set<string>>(new Set());
  const [addingJobId, setAddingJobId] = useState<string | null>(null);

  // Manual job form
  const [showManualJob, setShowManualJob] = useState(false);
  const [manualTitle, setManualTitle] = useState('');
  const [manualLocation, setManualLocation] = useState('');
  const [manualDescription, setManualDescription] = useState('');

  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!isOpen) return;
    setStep('company');
    setCompanyName('');
    setCompanyDomain('');
    setCompanyIndustry('');
    setCompanyLocation('');
    setPipeline(null);
    setJobQuery('');
    setJobResults([]);
    setAddedJobIds(new Set());
    setError(null);
    setBusy(false);
    setShowManualJob(false);
  }, [isOpen]);

  // Debounced job search
  useEffect(() => {
    if (step !== 'jobs' || jobQuery.trim().length < 2) {
      setJobResults([]);
      return;
    }
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setJobSearching(true);
      try {
        const data = await searchJobs(jobQuery.trim());
        setJobResults(data.jobs);
      } catch {
        setJobResults([]);
      } finally {
        setJobSearching(false);
      }
    }, 350);
    return () => clearTimeout(debounceRef.current);
  }, [jobQuery, step]);

  const handleCreatePipeline = async () => {
    if (!companyName.trim() || !companyDomain.trim()) {
      setError('Company name and domain are required.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await createPipeline({
        companyName: companyName.trim(),
        companyDomain: companyDomain.trim(),
        companyIndustry: companyIndustry.trim() || undefined,
        companyLocation: companyLocation.trim() || undefined,
      });
      setPipeline(p);
      setStep('jobs');
    } catch (e: any) {
      const msg = e.message || '';
      if (msg.includes('pipeline_already_exists')) {
        setError('A pipeline for this company already exists.');
      } else {
        setError(msg || 'Failed to create pipeline');
      }
    } finally {
      setBusy(false);
    }
  };

  const handleAddJob = async (jobId: string) => {
    if (!pipeline) return;
    setAddingJobId(jobId);
    setError(null);
    try {
      await addJobToPipeline(pipeline._id, jobId);
      setAddedJobIds((prev) => new Set(prev).add(jobId));
    } catch (e: any) {
      setError(e.message || 'Failed to add job');
    } finally {
      setAddingJobId(null);
    }
  };

  const handleCreateManualJob = async () => {
    if (!manualTitle.trim()) {
      setError('Job title is required.');
      return;
    }
    if (!pipeline) return;
    setBusy(true);
    setError(null);
    try {
      const job = await createManualJob({
        title: manualTitle.trim(),
        location: manualLocation.trim() || undefined,
        companyId: pipeline.companyId || undefined,
        description: manualDescription.trim() || undefined,
      });
      await addJobToPipeline(pipeline._id, job._id);
      setAddedJobIds((prev) => new Set(prev).add(job._id));
      setManualTitle('');
      setManualLocation('');
      setManualDescription('');
      setShowManualJob(false);
    } catch (e: any) {
      setError(e.message || 'Failed to create job');
    } finally {
      setBusy(false);
    }
  };

  const handleDone = () => {
    if (pipeline) onCreated(pipeline);
    onClose();
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
          width: '100%', maxWidth: 520, background: '#FFF', borderRadius: 12,
          padding: 0, boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
          maxHeight: '80vh', display: 'flex', flexDirection: 'column',
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
              width: 36, height: 36, borderRadius: 9999,
              background: '#EEF2FF', color: '#4F46E5',
            }}>
              <Icon name={step === 'company' ? 'building' : 'search'} size={18} />
            </span>
            <div>
              <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-primary)' }}>
                {step === 'company' ? 'Create Pipeline' : 'Add Jobs'}
              </div>
              <div style={{ fontSize: 12, color: 'var(--fg-muted)' }}>
                {step === 'company'
                  ? 'Enter company details'
                  : `${pipeline?.companyName} — search and add jobs`}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={busy}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: 30, height: 30, borderRadius: 6, cursor: 'pointer',
              border: '1px solid var(--border-card)', background: '#FFF',
              color: 'var(--fg-muted)',
            }}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '20px 24px', overflow: 'auto', flex: 1 }}>
          {error && (
            <div style={{
              padding: '10px 14px', marginBottom: 16, borderRadius: 8,
              background: '#FEF2F2', border: '1px solid #FECACA',
              fontSize: 13, color: '#B91C1C',
            }}>
              {error}
            </div>
          )}

          {step === 'company' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label style={labelStyle}>Company Name *</label>
                <input
                  style={inputStyle}
                  placeholder="e.g. Acme Corp"
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label style={labelStyle}>Company Domain *</label>
                <input
                  style={inputStyle}
                  placeholder="e.g. acme.com"
                  value={companyDomain}
                  onChange={(e) => setCompanyDomain(e.target.value)}
                />
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Industry</label>
                  <input
                    style={inputStyle}
                    placeholder="e.g. Software Development"
                    value={companyIndustry}
                    onChange={(e) => setCompanyIndustry(e.target.value)}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Location</label>
                  <input
                    style={inputStyle}
                    placeholder="e.g. Munich, DE"
                    value={companyLocation}
                    onChange={(e) => setCompanyLocation(e.target.value)}
                  />
                </div>
              </div>
            </div>
          )}

          {step === 'jobs' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Search existing jobs */}
              <div style={{ position: 'relative' }}>
                <Icon
                  name="search" size={14}
                  style={{ position: 'absolute', left: 12, top: 12, color: 'var(--fg-muted)' }}
                />
                <input
                  style={{ ...inputStyle, paddingLeft: 34 }}
                  placeholder="Search existing jobs by title or location..."
                  value={jobQuery}
                  onChange={(e) => setJobQuery(e.target.value)}
                  autoFocus
                />
                {jobSearching && (
                  <Icon
                    name="loader" size={14}
                    style={{ position: 'absolute', right: 12, top: 12, color: 'var(--fg-muted)' }}
                  />
                )}
              </div>

              {/* Results */}
              {jobResults.length > 0 && (
                <div style={{
                  border: '1px solid var(--border-card)', borderRadius: 8,
                  maxHeight: 220, overflow: 'auto',
                }}>
                  {jobResults.map((j) => {
                    const added = addedJobIds.has(j._id);
                    const adding = addingJobId === j._id;
                    return (
                      <div
                        key={j._id}
                        style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          padding: '10px 14px', borderBottom: '1px solid var(--border-card)',
                          gap: 10,
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--fg-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {j.title}
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--fg-muted)', marginTop: 2 }}>
                            {j.location || 'No location'}{j.boardName ? ` · ${j.boardName}` : ''}
                          </div>
                        </div>
                        <button
                          onClick={() => handleAddJob(j._id)}
                          disabled={added || adding}
                          style={{
                            height: 30, padding: '0 12px', borderRadius: 6, fontSize: 12,
                            fontWeight: 500, cursor: added || adding ? 'not-allowed' : 'pointer',
                            border: 'none', fontFamily: 'inherit',
                            background: added ? '#D1FAE5' : '#4F46E5',
                            color: added ? '#065F46' : '#FFF',
                            display: 'inline-flex', alignItems: 'center', gap: 4,
                          }}
                        >
                          {adding ? <Icon name="loader" size={12} /> :
                            added ? <><Icon name="check" size={12} /> Added</> :
                              <><Icon name="plus" size={12} /> Add</>}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {jobQuery.trim().length >= 2 && !jobSearching && jobResults.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--fg-muted)', textAlign: 'center', padding: 12 }}>
                  No existing jobs found for &quot;{jobQuery}&quot;
                </div>
              )}

              {/* Divider */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1, height: 1, background: 'var(--border-card)' }} />
                <span style={{ fontSize: 11, color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>or</span>
                <div style={{ flex: 1, height: 1, background: 'var(--border-card)' }} />
              </div>

              {/* Manual job creation */}
              {!showManualJob ? (
                <button
                  onClick={() => setShowManualJob(true)}
                  style={{
                    width: '100%', height: 40, borderRadius: 8, fontSize: 13,
                    fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit',
                    border: '1px dashed var(--border-card)', background: '#FAFAFA',
                    color: 'var(--fg-secondary)', display: 'flex', alignItems: 'center',
                    justifyContent: 'center', gap: 6,
                  }}
                >
                  <Icon name="plus" size={14} /> Create a new job manually
                </button>
              ) : (
                <div style={{
                  border: '1px solid var(--border-card)', borderRadius: 8, padding: 14,
                  background: '#FAFAFA', display: 'flex', flexDirection: 'column', gap: 10,
                }}>
                  <div>
                    <label style={labelStyle}>Job Title *</label>
                    <input
                      style={inputStyle}
                      placeholder="e.g. Senior Software Engineer"
                      value={manualTitle}
                      onChange={(e) => setManualTitle(e.target.value)}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Location</label>
                    <input
                      style={inputStyle}
                      placeholder="e.g. Berlin, Germany"
                      value={manualLocation}
                      onChange={(e) => setManualLocation(e.target.value)}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Description (optional)</label>
                    <textarea
                      style={{ ...inputStyle, height: 60, padding: '8px 12px', resize: 'vertical' }}
                      placeholder="Paste the JD or key requirements..."
                      value={manualDescription}
                      onChange={(e) => setManualDescription(e.target.value)}
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button
                      onClick={() => setShowManualJob(false)}
                      disabled={busy}
                      style={{
                        height: 32, padding: '0 14px', borderRadius: 6, fontSize: 12,
                        cursor: 'pointer', border: '1px solid var(--border-card)',
                        background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleCreateManualJob}
                      disabled={busy || !manualTitle.trim()}
                      style={{
                        height: 32, padding: '0 14px', borderRadius: 6, fontSize: 12,
                        fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer',
                        border: 'none', background: '#4F46E5', color: '#FFF', fontFamily: 'inherit',
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                      }}
                    >
                      {busy ? <Icon name="loader" size={12} /> : <Icon name="plus" size={12} />}
                      Create &amp; Add Job
                    </button>
                  </div>
                </div>
              )}

              {addedJobIds.size > 0 && (
                <div style={{
                  padding: '10px 14px', borderRadius: 8,
                  background: '#F0FDF4', border: '1px solid #BBF7D0',
                  fontSize: 13, color: '#166534',
                  display: 'flex', alignItems: 'center', gap: 6,
                }}>
                  <Icon name="check-circle" size={14} />
                  {addedJobIds.size} job{addedJobIds.size > 1 ? 's' : ''} added to pipeline
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '16px 24px', borderTop: '1px solid var(--border-card)',
          display: 'flex', justifyContent: 'flex-end', gap: 10,
        }}>
          {step === 'company' && (
            <>
              <button
                onClick={onClose}
                disabled={busy}
                style={{
                  height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13,
                  fontWeight: 500, cursor: 'pointer', border: '1px solid var(--border-card)',
                  background: '#FFF', color: 'var(--fg-primary)', fontFamily: 'inherit',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleCreatePipeline}
                disabled={busy || !companyName.trim() || !companyDomain.trim()}
                style={{
                  height: 36, padding: '0 16px', borderRadius: 6, fontSize: 13,
                  fontWeight: 600, cursor: busy ? 'not-allowed' : 'pointer',
                  border: 'none', background: '#4F46E5', color: '#FFF', fontFamily: 'inherit',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                {busy ? <Icon name="loader" size={14} /> : <Icon name="arrow-right" size={14} />}
                Create &amp; Add Jobs
              </button>
            </>
          )}
          {step === 'jobs' && (
            <button
              onClick={handleDone}
              style={{
                height: 36, padding: '0 20px', borderRadius: 6, fontSize: 13,
                fontWeight: 600, cursor: 'pointer', border: 'none',
                background: addedJobIds.size > 0 ? '#059669' : '#4F46E5',
                color: '#FFF', fontFamily: 'inherit',
                display: 'inline-flex', alignItems: 'center', gap: 6,
              }}
            >
              <Icon name={addedJobIds.size > 0 ? 'check' : 'x'} size={14} />
              {addedJobIds.size > 0 ? 'Done' : 'Skip — no jobs for now'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
