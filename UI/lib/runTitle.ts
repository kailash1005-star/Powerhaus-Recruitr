import type { Run } from './api';

type RunLike = Pick<Run, 'title' | 'runConfig'>;

/** The role/job name a run was launched for, derived from its search titles. */
export function runRoleTitle(run: RunLike): string {
  const titles = run.runConfig?.searchTitles?.filter(Boolean) ?? [];
  if (titles.length > 0) {
    return titles.length > 1 ? `${titles[0]} +${titles.length - 1} more` : titles[0];
  }
  const t = (run.title || '').trim();
  return t || 'Untitled Run';
}

/**
 * What to show as a run's name. We prefer the role it was launched for; if the
 * user has manually renamed the run (i.e. it no longer carries the auto
 * "Run (LinkedIn) — …" title), we respect their custom name instead.
 */
export function runDisplayName(run: RunLike): string {
  const t = (run.title || '').trim();
  const isAuto = /^run\s*\(/i.test(t);
  if (t && !isAuto) return t;
  return runRoleTitle(run);
}
