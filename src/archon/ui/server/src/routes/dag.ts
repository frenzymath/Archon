/**
 * Blueprint DAG API — serves the current leandag dependency graph to the
 * dashboard's DAG page.
 *
 * The graph is computed fresh by shelling out to `archon dag-graph --json`
 * (the Archon CLI has `leandag` installed, so this avoids reimplementing the
 * blueprint/Lean parsing in Node). The command also caches the result to
 * `.leandag/dag.json`, which we fall back to reading if the CLI is missing.
 */
import { spawnSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';

interface DagNode {
  id: string; type: string; title: string; chapter: string; statement: string;
  uses: string[]; lean_name: string | null; proved: boolean; has_sorry: boolean;
  dep_count: number; rdep_count: number;
  effort_total: number | null; effort_local: number | null;
  [k: string]: unknown;
}
interface DagGraph {
  nodes: DagNode[];
  edges: { from: string; to: string }[];
  meta: Record<string, unknown>;
  error: string | null;
}

const EMPTY: DagGraph = { nodes: [], edges: [], meta: {}, error: null };

function computeViaCli(projectPath: string, commit?: string): DagGraph | null {
  const args = ['dag-graph', '--project-path', projectPath];
  // Historical builds run in-memory and never write .leandag/ — see the
  // Python build_graph_at_commit; this keeps a running loop's live graph intact.
  if (commit) args.push('--commit', commit);
  const r = spawnSync('archon', args, {
    cwd: projectPath,
    encoding: 'utf-8',
    timeout: 60000,
    maxBuffer: 64 * 1024 * 1024,
  });
  if (r.status !== 0 || !r.stdout) return null;
  try {
    // The CLI prints only the JSON to stdout (banner goes to stderr).
    return JSON.parse(r.stdout) as DagGraph;
  } catch {
    return null;
  }
}

function readCache(projectPath: string): DagGraph | null {
  const p = path.join(projectPath, '.leandag', 'dag.json');
  if (!fs.existsSync(p)) return null;
  try {
    const raw = JSON.parse(fs.readFileSync(p, 'utf-8'));
    return { nodes: raw.nodes ?? [], edges: raw.edges ?? [], meta: raw.meta ?? {}, error: null };
  } catch {
    return null;
  }
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { projectPath } = paths;

  fastify.get<{ Querystring: { commit?: string } }>('/api/dag', async (req) => {
    const commit = req.query.commit?.trim();
    if (commit) {
      // Time-travel view: build the DAG at this commit fresh, in-memory. Do NOT
      // fall back to the cached live dag.json — that would show the current
      // graph mislabelled as a historical one.
      const at = computeViaCli(projectPath, commit);
      if (at) return at;
      return { ...EMPTY, error: `Could not build the DAG at commit ${commit}.` };
    }
    // Live view: prefer a fresh compute; fall back to the cached
    // .leandag/dag.json so the page still renders if the `archon` CLI isn't
    // on PATH in this environment.
    const fresh = computeViaCli(projectPath);
    if (fresh) return fresh;
    const cached = readCache(projectPath);
    if (cached) return cached;
    return {
      ...EMPTY,
      error:
        'Could not compute the blueprint DAG. Ensure `archon` (with leandag) ' +
        'is installed and the project has a blueprint under blueprint/src/.',
    };
  });
}
