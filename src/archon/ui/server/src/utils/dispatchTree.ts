/**
 * dispatch.jsonl reader + tree builder.
 *
 * Workstream A's Subagent base appends one `dispatch_start` and (later)
 * one `dispatch_end` row per subagent invocation to
 * `.archon/logs/iter-NNN/dispatch.jsonl`. This module collapses those
 * rows into a per-invocation record and arranges them into the
 * subagent-invocation forest rooted at the synthetic `_root` parent
 * (which represents the plan agent's direct dispatches).
 *
 * Keep this module self-contained: no fastify, no path knowledge of
 * task_results / logs trees — the routes layer reads the raw file and
 * hands it to `buildDispatchTree`.
 */

import fs from 'fs';

export const ROOT_PARENT_SLUG = '_root';

/** One row as it appears in dispatch.jsonl. */
export interface DispatchRow {
  ts?: string;
  event?: 'dispatch_start' | 'dispatch_end' | string;
  role?: string;
  slug?: string;
  parent_slug?: string;
  write_domain?: string[];
  log_base?: string;
  pid?: number;
  ok?: boolean;
  duration_s?: number;
  report_path?: string | null;
  [key: string]: unknown;
}

/** One collapsed subagent invocation, after merging start + end rows. */
export interface DispatchInvocation {
  role: string;
  slug: string;
  parentSlug: string;
  writeDomain: string[];
  /** Set when dispatch_end has been observed; otherwise the invocation is still running. */
  ok: boolean | null;
  startedAt: string | null;
  endedAt: string | null;
  durationS: number | null;
  reportPath: string | null;
  logBase: string | null;
  pid: number | null;
}

/** Tree node: an invocation plus its child invocations. */
export interface DispatchNode extends DispatchInvocation {
  children: DispatchNode[];
}

/** Parse a dispatch.jsonl file. Returns [] on missing file or malformed lines. */
export function readDispatchJsonl(filePath: string): DispatchRow[] {
  if (!fs.existsSync(filePath)) return [];
  let raw: string;
  try {
    raw = fs.readFileSync(filePath, 'utf-8');
  } catch {
    return [];
  }
  const rows: DispatchRow[] = [];
  for (const line of raw.split('\n')) {
    const s = line.trim();
    if (!s) continue;
    try {
      rows.push(JSON.parse(s) as DispatchRow);
    } catch {
      // Malformed line — skip silently. dispatch.jsonl is best-effort
      // anyway (Subagent.run swallows OSErrors during writes).
    }
  }
  return rows;
}

/**
 * Collapse start/end rows for the same (parent_slug, slug, role) triple
 * into a single invocation record.
 *
 * Order matters only for the (rare) case where the same slug is reused
 * within one iteration; we key on the most recent start that hasn't yet
 * been matched by an end. In practice slugs are unique per parent per
 * iteration (by convention enforced in the prompts), so this is a
 * defensive resolution.
 */
export function collapseInvocations(rows: DispatchRow[]): DispatchInvocation[] {
  const open: DispatchInvocation[] = [];
  const all: DispatchInvocation[] = [];

  for (const row of rows) {
    const role = String(row.role || '');
    const slug = String(row.slug || '');
    const parentSlug = String(row.parent_slug || ROOT_PARENT_SLUG);

    if (row.event === 'dispatch_start') {
      const inv: DispatchInvocation = {
        role,
        slug,
        parentSlug,
        writeDomain: Array.isArray(row.write_domain) ? [...row.write_domain] : [],
        ok: null,
        startedAt: typeof row.ts === 'string' ? row.ts : null,
        endedAt: null,
        durationS: null,
        reportPath: null,
        logBase: typeof row.log_base === 'string' ? row.log_base : null,
        pid: typeof row.pid === 'number' ? row.pid : null,
      };
      open.push(inv);
      all.push(inv);
      continue;
    }

    if (row.event === 'dispatch_end') {
      // Find the most-recent open invocation matching this (parent, slug, role).
      let idx = -1;
      for (let i = open.length - 1; i >= 0; i--) {
        const inv = open[i];
        if (inv.role === role && inv.slug === slug && inv.parentSlug === parentSlug) {
          idx = i;
          break;
        }
      }
      if (idx < 0) {
        // End row with no matching start — emit a synthetic record so
        // the dashboard can still surface it.
        const inv: DispatchInvocation = {
          role,
          slug,
          parentSlug,
          writeDomain: Array.isArray(row.write_domain) ? [...row.write_domain] : [],
          ok: typeof row.ok === 'boolean' ? row.ok : null,
          startedAt: null,
          endedAt: typeof row.ts === 'string' ? row.ts : null,
          durationS: typeof row.duration_s === 'number' ? row.duration_s : null,
          reportPath: typeof row.report_path === 'string' ? row.report_path : null,
          logBase: null,
          pid: null,
        };
        all.push(inv);
        continue;
      }
      const inv = open[idx];
      open.splice(idx, 1);
      inv.ok = typeof row.ok === 'boolean' ? row.ok : null;
      inv.endedAt = typeof row.ts === 'string' ? row.ts : inv.endedAt;
      inv.durationS = typeof row.duration_s === 'number' ? row.duration_s : null;
      inv.reportPath = typeof row.report_path === 'string' ? row.report_path : null;
    }
  }
  return all;
}

/**
 * Arrange collapsed invocations into the subagent-invocation forest.
 *
 * The top-level returned array contains every invocation whose
 * `parentSlug === ROOT_PARENT_SLUG`. Each node's `children` list
 * contains invocations whose `parentSlug` equals that node's `slug`.
 *
 * Orphans (invocations whose parent is neither `_root` nor any
 * recorded slug) are surfaced at the top level with a synthetic
 * `parentSlug` marker so they remain visible — this can happen when
 * dispatch.jsonl is truncated mid-iteration.
 */
export function buildDispatchTree(invocations: DispatchInvocation[]): DispatchNode[] {
  // Index by slug for parent lookup. If two siblings under different
  // parents share a slug, the index points to the last one — which is
  // good enough because slug uniqueness is per-parent (enforced by the
  // CLI's hierarchical task_results layout).
  const bySlug = new Map<string, DispatchNode>();
  const nodes: DispatchNode[] = invocations.map((inv) => {
    const node: DispatchNode = { ...inv, children: [] };
    bySlug.set(inv.slug, node);
    return node;
  });

  const roots: DispatchNode[] = [];
  for (const node of nodes) {
    if (node.parentSlug === ROOT_PARENT_SLUG) {
      roots.push(node);
      continue;
    }
    const parent = bySlug.get(node.parentSlug);
    if (parent) {
      parent.children.push(node);
    } else {
      // Orphan — surface at the root so the dashboard can show it.
      roots.push(node);
    }
  }
  return roots;
}

/** Convenience: read the file and return the tree in one call. */
export function readDispatchTree(filePath: string): DispatchNode[] {
  return buildDispatchTree(collapseInvocations(readDispatchJsonl(filePath)));
}
