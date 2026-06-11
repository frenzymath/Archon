/**
 * Per-request project paths + peer allowlist.
 *
 * The dashboard is launched bound to one project, but the user can switch to a
 * *peer* project (declared in that project's `.archon/peers.yaml`) via a
 * `?project=<path>` query param. This module resolves the allowed peer set
 * (single source of truth: the Python `archon peers --json`) and builds the
 * per-request `ProjectPaths`. Switching is read-only and restricted to the
 * allowlist, so a request can never point the server at an arbitrary directory.
 */
import path from 'path';
import { spawnSync } from 'child_process';
import type { ProjectPaths } from './routes/project.js';

/** The archon CLI to shell out to. The Python launcher passes an absolute path
 *  via ARCHON_BIN (archon may not be on the spawned server's PATH); bare name
 *  is the dev fallback (e.g. `tsx watch` started outside the launcher). */
export const ARCHON_BIN = process.env.ARCHON_BIN || 'archon';

// Make `request.paths` available to every route (set by the onRequest hook).
declare module 'fastify' {
  interface FastifyRequest {
    paths: ProjectPaths;
  }
}

export function makePaths(projectPath: string): ProjectPaths {
  const abs = path.resolve(projectPath);
  return {
    projectPath: abs,
    archonPath: path.join(abs, '.archon'),
    logsPath: path.join(abs, '.archon', 'logs'),
  };
}

export interface PeerEntry {
  name: string;
  path: string;
  has_dag: boolean;
}

/**
 * Resolve the peer projects for `projectPath` by shelling out to the Python
 * CLI, which owns `.archon/peers.yaml` glob resolution. The banner goes to
 * stderr, so stdout is clean JSON (same contract `dag-graph` relies on).
 */
export function loadPeers(projectPath: string): PeerEntry[] {
  try {
    const r = spawnSync(ARCHON_BIN, ['peers', '--project-path', projectPath, '--json'], {
      encoding: 'utf-8',
      maxBuffer: 8 * 1024 * 1024,
    });
    if (r.status !== 0 || !r.stdout) return [];
    const parsed = JSON.parse(r.stdout);
    return Array.isArray(parsed?.peers) ? (parsed.peers as PeerEntry[]) : [];
  } catch {
    return [];
  }
}

/** The set of project roots (the base project + its peers) a request may target. */
export function allowedRoots(projectPath: string, peers: PeerEntry[]): Set<string> {
  const roots = new Set<string>([path.resolve(projectPath)]);
  for (const p of peers) roots.add(path.resolve(p.path));
  return roots;
}
