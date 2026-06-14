import { useQuery } from '@tanstack/react-query';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export interface GitCommit {
  sha: string;
  shortSha: string;
  subject: string;
  date: string;
  parents: string[];
  refs: string[];
  branch?: string;
  iteration?: string;
  phase?: string;
  fileSlug?: string;
}

export interface GitLogResponse {
  commits: GitCommit[];
}

export interface PhaseLogResponse {
  entries: unknown[];
}

export interface BlueprintResponse {
  tex: string | null;
  /**
   * KaTeX-compatible macro map, parsed from blueprint/src/macros/*.tex —
   * keys are the full command (e.g. "\\R") and values are the expansion
   * (e.g. "\\mathbb{R}"). Empty object when the project has no macros/ dir.
   */
  macros?: Record<string, string>;
}

export function useGitLog() {
  return useQuery<GitLogResponse>({
    queryKey: ['gitLog'],
    queryFn: () => fetchJson('/api/git/log'),
    refetchInterval: 10000,
  });
}

export interface GitHeadResponse {
  commit: null | {
    sha: string;
    shortSha: string;
    subject: string;
    date: string;
    branch: string;
    iteration?: string;
    phase?: string;
  };
}

/** HEAD commit of the inner archon git (null when no inner git exists). */
export function useGitHead() {
  return useQuery<GitHeadResponse>({
    queryKey: ['gitHead'],
    queryFn: () => fetchJson('/api/git/head'),
    refetchInterval: 10000,
  });
}

export function usePhaseLogs(iteration: string | undefined, phase: string | undefined) {
  return useQuery<PhaseLogResponse>({
    queryKey: ['phaseLogs', iteration, phase],
    queryFn: () => fetchJson(`/api/git/phase-logs/${iteration}/${phase}`),
    enabled: !!iteration && !!phase && phase !== 'prover',
  });
}

export function useBlueprint(file: string, name: string) {
  return useQuery<BlueprintResponse>({
    queryKey: ['blueprint', file, name],
    queryFn: () => fetchJson(`/api/blueprint?file=${encodeURIComponent(file)}&name=${encodeURIComponent(name)}`),
    enabled: !!file && !!name,
  });
}

export interface BlueprintChapter { slug: string; title: string; tex: string; }
export interface BlueprintChaptersResponse {
  chapters: BlueprintChapter[];
  macros?: Record<string, string>;
  docTitle?: string | null;
  docAuthor?: string | null;
  hasBlueprint: boolean;
  commit: string | null;
  error: string | null;
}

/**
 * The whole blueprint as ordered chapters (raw tex, comments preserved), or —
 * when `commit` is given — as it was at that inner-git commit (read via
 * `git show` server-side, never cached).
 */
export function useBlueprintChapters(commit?: string | null) {
  return useQuery<BlueprintChaptersResponse>({
    queryKey: ['blueprintChapters', commit ?? null],
    queryFn: () => fetchJson(
      commit
        ? `/api/blueprint/chapters?commit=${encodeURIComponent(commit)}`
        : '/api/blueprint/chapters',
    ),
    staleTime: commit ? Infinity : 30_000,
  });
}
