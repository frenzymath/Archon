/**
 * Helpers for interacting with the inner archon git repo (.archon/git-dir).
 *
 * All helpers are no-ops when the inner git doesn't exist, so callers can use
 * them unconditionally on legacy projects without checking for the directory
 * first.
 */
import { spawnSync } from 'child_process';
import fs from 'fs';

export interface InnerCommit {
  sha: string;
  shortSha: string;
  subject: string;
  date: string;
}

function run(gitDir: string, projectPath: string, args: string[]): { stdout: string; ok: boolean } {
  const r = spawnSync('git', args, {
    env: { ...process.env, GIT_DIR: gitDir, GIT_WORK_TREE: projectPath },
    cwd: projectPath,
    encoding: 'utf-8',
    timeout: 8000,
    maxBuffer: 64 * 1024 * 1024,
  });
  return { stdout: r.stdout ?? '', ok: r.status === 0 };
}

/** True when the inner archon git exists. */
export function hasInnerGit(gitDir: string): boolean {
  return fs.existsSync(gitDir);
}

/**
 * Map iteration id (iter-NNN) → latest commit on that iteration.
 * "Latest" = newest in topo-order that mentions `archon[NNN/...]` in its subject.
 */
export function mapIterToCommit(gitDir: string, projectPath: string): Map<string, InnerCommit> {
  const out = new Map<string, InnerCommit>();
  if (!hasInnerGit(gitDir)) return out;
  const { stdout, ok } = run(gitDir, projectPath, [
    'log', '--all', '--topo-order',
    '--format=%H%x01%h%x01%s%x01%ai',
  ]);
  if (!ok) return out;
  for (const line of stdout.split('\n')) {
    if (!line.trim()) continue;
    const [sha, shortSha, subject, date] = line.split('\x01');
    const m = subject?.match(/archon\[(\d+)\//);
    if (!m) continue;
    const iterId = `iter-${String(parseInt(m[1], 10)).padStart(3, '0')}`;
    if (!out.has(iterId)) out.set(iterId, { sha, shortSha, subject, date });
  }
  return out;
}

export interface IterPhaseCommits {
  /** Catch-all: the most recent commit for the iteration (used for iter-group badge). */
  latest?: InnerCommit;
  plan?: InnerCommit;
  refactor?: InnerCommit;
  review?: InnerCommit;
  finalize?: InnerCommit;
  /** Prover commits keyed by fileSlug (e.g. "Foo_Bar" for "Foo/Bar.lean"). */
  prover: Record<string, InnerCommit>;
}

/**
 * Map iteration id (iter-NNN) → every phase-specific commit on that iteration.
 * The subject format is `archon[N/phase]` or `archon[N/phase/fileSlug]`. Newest
 * first in topo-order; within an iteration we keep the first (= latest) match
 * per phase/slug.
 */
export function mapIterToPhaseCommits(
  gitDir: string,
  projectPath: string,
): Map<string, IterPhaseCommits> {
  const out = new Map<string, IterPhaseCommits>();
  if (!hasInnerGit(gitDir)) return out;
  const { stdout, ok } = run(gitDir, projectPath, [
    'log', '--all', '--topo-order',
    '--format=%H%x01%h%x01%s%x01%ai',
  ]);
  if (!ok) return out;
  for (const line of stdout.split('\n')) {
    if (!line.trim()) continue;
    const [sha, shortSha, subject, date] = line.split('\x01');
    const m = subject?.match(/archon\[(\d+)\/([^/\]]+)(?:\/([^\]]+))?\]/);
    if (!m) continue;
    const iterId = `iter-${String(parseInt(m[1], 10)).padStart(3, '0')}`;
    const phase = m[2];
    const fileSlug = m[3];
    const commit: InnerCommit = { sha, shortSha, subject, date };
    let entry = out.get(iterId);
    if (!entry) { entry = { prover: {} }; out.set(iterId, entry); }
    if (!entry.latest) entry.latest = commit;
    if (phase === 'prover' && fileSlug) {
      if (!entry.prover[fileSlug]) entry.prover[fileSlug] = commit;
    } else if (phase === 'plan' && !entry.plan) {
      entry.plan = commit;
    } else if (phase === 'refactor' && !entry.refactor) {
      entry.refactor = commit;
    } else if (phase === 'review' && !entry.review) {
      entry.review = commit;
    } else if (phase === 'finalize' && !entry.finalize) {
      entry.finalize = commit;
    }
  }
  return out;
}

/**
 * List all project .lean files in the tree at a given commit.
 *
 * Filters out tooling/agent dirs that the inner git tracks but that aren't
 * project source — most importantly `.archon/logs/iter-NNN/snapshots/<slug>/`
 * which contains baseline.lean and step-*.lean snapshot files. Including
 * them would make the proof graph render every snapshot as a separate file
 * group (e.g. "baseline.lean" repeated once per iteration × prover slug).
 *
 * The exclusion list matches the on-disk walk in proofgraph.ts's
 * `/api/proofgraph/declarations` so both views agree on what counts as a
 * project file.
 */
const EXCLUDED_PATH_SEGMENTS = new Set(['_lake', '.lake', '.archon', 'node_modules', '.git', 'lake-packages']);

export function lsLeanFilesAtCommit(gitDir: string, projectPath: string, sha: string): string[] {
  if (!hasInnerGit(gitDir)) return [];
  const { stdout, ok } = run(gitDir, projectPath, ['ls-tree', '-r', '--name-only', sha]);
  if (!ok) return [];
  return stdout.split('\n').filter(f => {
    if (!f.endsWith('.lean')) return false;
    return !f.split('/').some(seg => EXCLUDED_PATH_SEGMENTS.has(seg));
  });
}

/**
 * Read file content at a given commit. Returns null when the file didn't
 * exist at that commit (git show exits non-zero).
 */
export function showFileAtCommit(
  gitDir: string,
  projectPath: string,
  sha: string,
  file: string,
): string | null {
  if (!hasInnerGit(gitDir)) return null;
  const { stdout, ok } = run(gitDir, projectPath, ['show', `${sha}:${file}`]);
  return ok ? stdout : null;
}
