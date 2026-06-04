import { useQuery } from '@tanstack/react-query';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

/** A blueprint DAG node (mirrors leandag's GraphNode.to_dict()). */
export interface DagNode {
  id: string;
  type: string;
  title: string;
  chapter: string;
  statement: string;
  uses: string[];
  lean_name: string | null;
  proved: boolean;
  mathlib_ok?: boolean;
  has_sorry: boolean;
  dep_count: number;
  rdep_count: number;
  descendant_count?: number;
  effort_total: number | null;
  effort_local: number | null;
  /** Source files (leandag GraphNode.tex_file / lean_file). */
  tex_file?: string | null;
  lean_file?: string | null;
  proof_tex?: string;
  lean_source?: string;
  proof_size_tex?: number | null;
  proof_size_tex_total?: number | null;
  proof_size_lean?: number | null;
  proof_size_lean_total?: number | null;
}

export interface DagGraphResponse {
  nodes: DagNode[];
  edges: { from: string; to: string }[];
  meta: {
    num_nodes?: number;
    num_edges?: number;
    has_blueprint?: boolean;
    entry?: string | null;
    total_lean_decls?: number;
    total_blueprint_decls?: number;
    axioms?: string[];
    leaves?: string[];
    duplicate_ids?: string[];
    /** Blueprint \newcommand/\def macros, for KaTeX rendering. */
    macros?: Record<string, string>;
    /** Set when this is a historical (time-travel) build at an inner-git commit. */
    commit?: string;
  };
  error: string | null;
}

/**
 * Blueprint DAG for the current working tree, or — when `commit` is given — as
 * it was at that inner-git commit (built in-memory server-side, never cached).
 */
export function useDag(commit?: string | null) {
  return useQuery({
    queryKey: ['dag', commit ?? null],
    queryFn: () => fetchJson<DagGraphResponse>(
      commit ? `/api/dag?commit=${encodeURIComponent(commit)}` : '/api/dag',
    ),
    // Historical builds are immutable; the live one we refresh on the usual cadence.
    staleTime: commit ? Infinity : 30_000,
  });
}

/** Last archon commit that touched each project file (inner git). */
export interface FileMod {
  sha: string;
  date: string;
  subject: string;
  iteration?: string;
  phase?: string;
}

export function useDagLastModified() {
  return useQuery({
    queryKey: ['dagLastModified'],
    queryFn: () => fetchJson<{ files: Record<string, FileMod> }>('/api/dag/last-modified'),
    staleTime: 30_000,
  });
}
