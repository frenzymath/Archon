/** Logs API — tree listing, content retrieval, WebSocket streaming */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { parseJsonl, readFileOr } from '../utils.js';
import { mapIterToPhaseCommits, type InnerCommit, type IterPhaseCommits } from '../utils/innerGit.js';
import {
  ROOT_PARENT_SLUG,
  readDispatchTree,
  type DispatchNode,
} from '../utils/dispatchTree.js';
import type { ProjectPaths } from './project.js';

interface LogFileEntry {
  name: string;
  path: string;
  size: number;
  modified: string;
  role?: string;
  /** For subagent files (`<role>-<slug>.jsonl|.md`), the bare slug — used
   *  by the dashboard to render `<role> <slug>` distinctly from a phase log. */
  subagentSlug?: string;
  /** When the subagent was spawned by another subagent (hierarchical
   *  dispatch from Workstream A), the parent's slug. Absent for
   *  root-level invocations and phase logs. */
  parentSlug?: string;
  /** Commit for this specific file's phase (plan/refactor/prover/review). */
  commit?: InnerCommit;
}
interface LogGroup { id: string; files: LogFileEntry[]; meta?: Record<string, unknown> }

/** Stat a path, returning null if it doesn't exist or is unreadable.
 *  Critical for cross-machine project clones, where prover symlinks
 *  baked on the original host (absolute paths) become dangling — a
 *  raw `fs.statSync` would throw ENOENT and 500 the whole endpoint. */
function safeStat(p: string): fs.Stats | null {
  try { return fs.statSync(p); } catch { return null; }
}

/** Pattern for autonomous-loop subagent JSONL streams. Matches files
 *  produced by ``archon subagent <name> --slug <slug> ...`` — i.e.
 *  ``<name>-<slug>.jsonl`` written under ``iter-NNN/`` (root-level)
 *  or ``iter-NNN/<parent-slug>/`` (hierarchical). The regex is
 *  *role-agnostic* on purpose — anyone can drop a new subagent
 *  descriptor under ``.archon/subagents/`` and its JSONL stream
 *  surfaces here without UI changes. Phase logs (``plan.jsonl``,
 *  ``review.jsonl``, …) have no hyphen-separated slug so they don't
 *  match. Phase logs that *do* contain a hyphen (e.g.
 *  ``plan-post-refactor``) live in ``PHASE_LOG_STEMS`` below. */
const SUBAGENT_JSONL_RE = /^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(.+)\.jsonl$/;

/** Hyphenated phase-log stems that would otherwise look like
 *  ``<role>-<slug>``. Any new phase whose stem contains a hyphen must
 *  be added here so the subagent matcher skips it. Pure-word phase
 *  stems (``plan``, ``review``, ``finalize``, …) don't need listing —
 *  they have no hyphen and so don't match the regex. */
const PHASE_LOG_STEMS = new Set<string>([
  'plan-post-refactor',
  'sync-leanok',
  'marker-sync',
]);

function matchSubagentJsonl(filename: string): { name: string; slug: string } | null {
  const m = filename.match(SUBAGENT_JSONL_RE);
  if (!m) return null;
  const stem = filename.replace(/\.jsonl$/, '');
  if (PHASE_LOG_STEMS.has(stem)) return null;
  return { name: m[1], slug: m[2] };
}

/** Directories under iter-NNN/ that are NOT subagent-slug subdirectories
 *  and should be skipped when walking nested subagent JSONLs. ``provers``
 *  is the parallel-prover sub-tree handled by its own block below; the
 *  others are bookkeeping. */
const ITER_RESERVED_SUBDIRS = new Set(['provers', '.slots', '.held', 'snapshots']);

/** Pick the commit that "belongs" to a file, given its role and prover slug. */
function commitForFile(
  phaseCommits: IterPhaseCommits | undefined,
  role: string | undefined,
  fileName: string,
): InnerCommit | undefined {
  if (!phaseCommits) return undefined;
  if (!role) return phaseCommits.latest;
  // Plan-side phase logs.
  if (role === 'plan' || role === 'plan-post-refactor') return phaseCommits.plan;
  // Manual refactor artifacts (legacy named files from the
  // interactive `archon refactor` flow).
  if (role === 'refactor' || role === 'refactor-manual'
      || role === 'refactor-directive' || role === 'refactor-report') return phaseCommits.refactor;
  // Subagent reports + JSONL streams: route by name heuristic.
  // Anything mentioning "review" goes to the review commit (with plan
  // fallback) — review-* subagents typically dispatch from review.
  // Everything else goes to the plan commit, since the plan agent is
  // the usual dispatcher for in-loop subagents.
  const looksReview =
    role.startsWith('review-')
    || role.startsWith('subagent-review-')
    || role.includes('-review-');
  if (looksReview) {
    return phaseCommits.review ?? phaseCommits.plan;
  }
  if (role.startsWith('subagent-') || role.endsWith('-report')) {
    return phaseCommits.plan;
  }
  if (role === 'review') return phaseCommits.review;
  if (role === 'finalize') return phaseCommits.finalize;
  if (role === 'prover') {
    const slug = fileName.replace(/\.jsonl$/, '');
    return phaseCommits.prover[slug] ?? phaseCommits.latest;
  }
  return phaseCommits.latest;
}

function resolveLogPath(logsPath: string, logPath: string, archonPath?: string): string | null {
  const normalized = path.normalize(logPath).replace(/^(\.\.[/\\])+/, '');
  const full = path.join(logsPath, normalized);
  if (!full.startsWith(logsPath)) return null;
  // For .md files, pass through as-is; for others, default to .jsonl
  const candidate = (full.endsWith('.md') || full.endsWith('.jsonl')) ? full : full + '.jsonl';
  if (fs.existsSync(candidate)) return candidate;
  // Fallback: multilane lane logs and merge logs aren't always
  // symlinked into iter_dir/provers/. If the requested path looks like
  // ``iter-NNN/provers/<slug>__<lane>.jsonl`` (or ``__merge``), peel
  // the suffix off and try the canonical multilane location:
  //   <archonPath>/multilane/lanes/<lane>/iter-NNN/provers/<slug>.jsonl
  //   <archonPath>/multilane/runtime/iter-NNN/merges/<slug>.jsonl
  if (!archonPath) return candidate;
  const m = candidate.match(/iter-(\d+)\/provers\/(.+?)__([^/]+)\.jsonl$/);
  if (!m) return candidate;
  const [, iterNum, slug, tail] = m;
  const iterId = `iter-${iterNum}`;
  if (tail === 'merge') {
    const mergeFile = path.join(archonPath, 'multilane', 'runtime', iterId, 'merges', `${slug}.jsonl`);
    if (fs.existsSync(mergeFile)) return mergeFile;
  } else {
    const laneFile = path.join(archonPath, 'multilane', 'lanes', tail, iterId, 'provers', `${slug}.jsonl`);
    if (fs.existsSync(laneFile)) return laneFile;
  }
  return candidate;  // missing — let the caller's existsSync return the 404
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { logsPath, archonPath, projectPath } = paths;
  const gitDir = path.join(archonPath, 'git-dir');

  // Tree-structured log listing
  fastify.get('/api/logs', async () => {
    if (!fs.existsSync(logsPath)) return { flat: [], groups: [] };

    const phaseByIter = mapIterToPhaseCommits(gitDir, projectPath);
    const commitByIter = new Map<string, InnerCommit>();
    for (const [iter, ph] of phaseByIter) if (ph.latest) commitByIter.set(iter, ph.latest);

    const flat: LogFileEntry[] = fs.readdirSync(logsPath)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => {
        const stat = safeStat(path.join(logsPath, f));
        if (!stat || !stat.isFile()) return null;
        return { name: f, path: f, size: stat.size, modified: stat.mtime.toISOString() };
      })
      .filter((x): x is LogFileEntry => x !== null)
      .sort((a, b) => b.modified.localeCompare(a.modified));

    const groups: LogGroup[] = [];
    const iterDirs = fs.readdirSync(logsPath)
      .filter(d => {
        if (!d.startsWith('iter-')) return false;
        const s = safeStat(path.join(logsPath, d));
        return !!s && s.isDirectory();
      })
      .sort();

    for (const dir of iterDirs) {
      const dirPath = path.join(logsPath, dir);
      const files: LogFileEntry[] = [];
      const phaseCommits = phaseByIter.get(dir);

      // Standard JSONL logs at the iteration root.
      // `dispatch.jsonl` carries dispatch_start/dispatch_end metadata
      // for the dispatch-tree endpoint; it is NOT a renderable session
      // log (no shell/thinking/tool_call/text events). Including it
      // here surfaces an empty sidebar entry the LogViewer can't
      // render, which is exactly what we want to avoid.
      for (const f of fs.readdirSync(dirPath).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl') && f !== 'provers-combined.jsonl' && f !== 'dispatch.jsonl')) {
        const full = path.join(dirPath, f);
        const stat = safeStat(full);
        if (!stat || !stat.isFile()) continue;

        // Subagent JSONL streams emit `<name>-<slug>.jsonl`. Tag them
        // with role=`subagent-<name>` and surface the bare slug so the
        // dashboard can render them under their role with the slug
        // shown alongside, instead of one anonymous "refactor-foo" tag.
        const subagentMatch = matchSubagentJsonl(f);
        const role = subagentMatch
          ? `subagent-${subagentMatch.name}`
          : f.replace('.jsonl', '');
        const subagentSlug = subagentMatch ? subagentMatch.slug : undefined;

        files.push({
          name: f, path: `${dir}/${f}`, size: stat.size, modified: stat.mtime.toISOString(), role,
          subagentSlug,
          commit: commitForFile(phaseCommits, role, f),
        });
      }

      // Refactor artifacts (legacy archived markdown from the
      // pre-subagent refactor phase).
      for (const artifact of ['refactor-directive.md', 'refactor-report.md']) {
        const full = path.join(dirPath, artifact);
        const stat = safeStat(full);
        if (!stat || !stat.isFile()) continue;
        const role = artifact.replace('.md', '');  // "refactor-directive" | "refactor-report"
        files.push({
          name: artifact,
          path: `${dir}/${artifact}`,
          size: stat.size,
          modified: stat.mtime.toISOString(),
          role,
          commit: commitForFile(phaseCommits, role, artifact),
        });
      }

      // Subagent reports archived by the plan agent. Convention:
      // `<name>-<slug>-report.md`. Role-agnostic match so newly-added
      // subagents surface without UI changes — only legacy phase
      // artifacts (refactor-report.md, refactor-directive.md) need
      // explicit skips.
      const subagentReportRe = /^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(.+)-report\.md$/;
      for (const f of fs.readdirSync(dirPath)) {
        const m = f.match(subagentReportRe);
        if (!m) continue;
        if (f === 'refactor-report.md' || f === 'refactor-directive.md') continue;
        const full = path.join(dirPath, f);
        const stat = safeStat(full);
        if (!stat || !stat.isFile()) continue;
        const role = `${m[1]}-report`;  // e.g. "analogy-report", "blueprint-writer-report"
        files.push({
          name: f,
          path: `${dir}/${f}`,
          size: stat.size,
          modified: stat.mtime.toISOString(),
          role,
          subagentSlug: m[2],
          commit: commitForFile(phaseCommits, role, f),
        });
      }

      // Hierarchical subagent JSONL streams (Workstream A): when a
      // coordinator-style subagent dispatches children, the children's
      // logs live at `iter-NNN/<parent-slug>/<role>-<slug>.jsonl`.
      // Walk every subdir of iter-NNN that isn't reserved (provers,
      // .slots, .held, snapshots) and surface its JSONLs with the
      // `parentSlug` field set, so the dashboard can group by parent.
      for (const sub of fs.readdirSync(dirPath)) {
        if (ITER_RESERVED_SUBDIRS.has(sub)) continue;
        const subPath = path.join(dirPath, sub);
        const subStat = safeStat(subPath);
        if (!subStat || !subStat.isDirectory()) continue;
        for (const f of fs.readdirSync(subPath)) {
          if (!f.endsWith('.jsonl') || f.endsWith('.raw.jsonl')) continue;
          const subagentMatch = matchSubagentJsonl(f);
          if (!subagentMatch) continue;
          const full = path.join(subPath, f);
          const stat = safeStat(full);
          if (!stat || !stat.isFile()) continue;
          const role = `subagent-${subagentMatch.name}`;
          const subagentSlug = subagentMatch.slug;
          files.push({
            name: f,
            path: `${dir}/${sub}/${f}`,
            size: stat.size,
            modified: stat.mtime.toISOString(),
            role,
            subagentSlug,
            parentSlug: sub,
            commit: commitForFile(phaseCommits, role, f),
          });
        }
      }

      // Parallel prover JSONL logs — each gets the commit for its specific file slug.
      const proversDir = path.join(dirPath, 'provers');
      const seenProverNames = new Set<string>();
      const proversStat = safeStat(proversDir);
      if (proversStat && proversStat.isDirectory()) {
        for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl')).sort()) {
          const full = path.join(proversDir, f);
          const stat = safeStat(full);
          // Broken symlink (common after cross-machine project clones,
          // where absolute symlink targets don't resolve). Skip silently
          // so the lanes-dir fallback below can surface the real file.
          if (!stat) continue;
          files.push({
            name: f, path: `${dir}/provers/${f}`, size: stat.size, modified: stat.mtime.toISOString(), role: 'prover',
            commit: commitForFile(phaseCommits, 'prover', f),
          });
          seenProverNames.add(f);
        }
      }

      // Multilane fallback: when symlinks weren't created (older
      // archon, or the symlink call failed silently), the lane JSONLs
      // live only under .archon/multilane/lanes/<lane>/<iter>/provers/.
      // Walk that tree and surface them under the SAME logical path
      // (<iter>/provers/<slug>__<lane>.jsonl) — resolveLogPath knows
      // how to map the suffix back to the actual file.
      const lanesRoot = path.join(archonPath, 'multilane', 'lanes');
      if (fs.existsSync(lanesRoot)) {
        for (const lane of fs.readdirSync(lanesRoot)) {
          const laneProversDir = path.join(lanesRoot, lane, dir, 'provers');
          if (!fs.existsSync(laneProversDir)) continue;
          for (const f of fs.readdirSync(laneProversDir).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl')).sort()) {
            const fakeName = `${f.replace('.jsonl', '')}__${lane}.jsonl`;
            if (seenProverNames.has(fakeName)) continue;  // symlink already covered it
            try {
              const stat = fs.statSync(path.join(laneProversDir, f));
              files.push({
                name: fakeName,
                path: `${dir}/provers/${fakeName}`,
                size: stat.size,
                modified: stat.mtime.toISOString(),
                role: 'prover',
                commit: commitForFile(phaseCommits, 'prover', fakeName),
              });
            } catch { /* ignore unreadable entries */ }
          }
        }
      }
      // Same fallback for merge-agent logs.
      const mergesDir = path.join(archonPath, 'multilane', 'runtime', dir, 'merges');
      if (fs.existsSync(mergesDir)) {
        for (const f of fs.readdirSync(mergesDir).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl')).sort()) {
          const fakeName = `${f.replace('.jsonl', '')}__merge.jsonl`;
          if (seenProverNames.has(fakeName)) continue;
          try {
            const stat = fs.statSync(path.join(mergesDir, f));
            files.push({
              name: fakeName,
              path: `${dir}/provers/${fakeName}`,
              size: stat.size,
              modified: stat.mtime.toISOString(),
              role: 'prover',
              commit: commitForFile(phaseCommits, 'prover', fakeName),
            });
          } catch { /* ignore */ }
        }
      }

      let meta: Record<string, unknown> | undefined;
      const metaFile = path.join(dirPath, 'meta.json');
      try { meta = JSON.parse(fs.readFileSync(metaFile, 'utf-8')); } catch { /* skip */ }

      // Attach the inner git commit for this iteration (if present) — this is
      // the latest commit of the iter, used for the group-level badge.
      const commit = commitByIter.get(dir);
      if (commit) meta = { ...(meta ?? {}), commit };

      groups.push({ id: dir, files, meta });
    }

    // ── Relocate legacy flat refactor-{timestamp}.jsonl files into their true iter.
    // Strategy: a manual refactor commit is tagged `archon[N+1/refactor/...]` in the
    // inner git — i.e. it belongs to the NEXT iteration. Use commitByIter's subject
    // dates to find the refactor commit whose date is closest to the filename
    // timestamp, and attach the flat file to that iter. Falls back to mtime-window
    // correlation when no inner git exists.
    if (groups.length && flat.length) {
      // Use phase-specific refactor commits (not just the iter's latest, which
      // might be a review commit made after the refactor).
      const refactorCommits = Array.from(phaseByIter.entries())
        .filter(([, ph]) => !!ph.refactor)
        .map(([iterId, ph]) => ({ iterId, ts: new Date(ph.refactor!.date).getTime() }))
        .filter(x => Number.isFinite(x.ts));

      const iterWindows = groups.map(g => {
        const m = g.meta as Record<string, unknown> | undefined;
        const sAt = typeof m?.startedAt === 'string' ? new Date(m.startedAt).getTime() : 0;
        const cAt = typeof m?.completedAt === 'string' ? new Date(m.completedAt).getTime() : 0;
        return { id: g.id, startedAt: sAt, completedAt: cAt };
      });

      for (let i = flat.length - 1; i >= 0; i--) {
        const f = flat[i];
        const m = f.name.match(/^refactor-(\d+)\.jsonl$/);
        if (!m) continue;
        const ts = parseInt(m[1], 10) * 1000;
        if (!Number.isFinite(ts) || ts <= 0) continue;

        let targetIterId: string | undefined;

        // 1. Prefer matching to a real refactor commit by date proximity (≤1h window).
        if (refactorCommits.length) {
          let bestDelta = Infinity;
          for (const rc of refactorCommits) {
            const d = Math.abs(ts - rc.ts);
            if (d < bestDelta && d <= 3600 * 1000) {
              bestDelta = d;
              targetIterId = rc.iterId;
            }
          }
        }

        // 2. Otherwise, fall back to the closest iter by time windows (legacy projects
        //    without inner git, or refactors that never committed).
        if (!targetIterId) {
          let bestIdx = -1;
          let bestDelta = Infinity;
          for (let gi = 0; gi < iterWindows.length; gi++) {
            const w = iterWindows[gi];
            const upper = w.completedAt || iterWindows[gi + 1]?.startedAt || (w.startedAt + 24 * 3600 * 1000);
            const lower = w.startedAt;
            if (!lower) continue;
            if (ts >= lower - 3600 * 1000 && ts <= upper + 3600 * 1000) {
              const centre = (lower + upper) / 2;
              const d = Math.abs(ts - centre);
              if (d < bestDelta) { bestDelta = d; bestIdx = gi; }
            }
          }
          if (bestIdx < 0) {
            // Final fallback: closest iter by startedAt.
            for (let gi = 0; gi < iterWindows.length; gi++) {
              const w = iterWindows[gi];
              if (!w.startedAt) continue;
              const d = Math.abs(ts - w.startedAt);
              if (d < bestDelta) { bestDelta = d; bestIdx = gi; }
            }
          }
          if (bestIdx >= 0) targetIterId = iterWindows[bestIdx].id;
        }

        if (targetIterId) {
          const target = groups.find(g => g.id === targetIterId);
          if (target) {
            target.files.push({
              name: f.name,
              path: f.path,
              size: f.size,
              modified: f.modified,
              role: 'refactor-manual',
              commit: commitForFile(phaseByIter.get(targetIterId), 'refactor-manual', f.name),
            });
            flat.splice(i, 1);
          }
        }
      }
    }

    return { flat, groups };
  });

  // Hierarchical subagent-invocation tree for one iteration. Reads
  // ``iter-NNN/dispatch.jsonl`` (written by ``Subagent.run``) and
  // returns the forest rooted at ``_root``. Each node carries enough
  // info for the dashboard to render an expandable subagent tree:
  // role, slug, parentSlug, write-domain, status, duration, and the
  // log/report paths so the client can deep-link.
  fastify.get('/api/logs/:iter/tree', async (req, reply) => {
    const iterId = (req.params as Record<string, string>).iter;
    if (!iterId || !/^iter-\d{3,}$/.test(iterId)) {
      return reply.status(400).send({ error: 'Invalid iter id' });
    }
    const iterDir = path.join(logsPath, iterId);
    if (!fs.existsSync(iterDir)) {
      return reply.status(404).send({ error: 'Iteration not found' });
    }
    const dispatchPath = path.join(iterDir, 'dispatch.jsonl');
    const tree: DispatchNode[] = readDispatchTree(dispatchPath);

    // Stamp the log-path field on each node relative to ``logsPath`` so
    // the client can fetch the JSONL via the existing /api/logs/* route.
    // Root-level: ``iter-NNN/<role>-<slug>.jsonl``
    // Nested:     ``iter-NNN/<parent-slug>/<role>-<slug>.jsonl``
    //
    // Also normalize reportPath to be relative to ``.archon/task_results/``
    // so the client can fetch it via the new /api/task-results/* route.
    const taskResultsRoot = path.join(archonPath, 'task_results');
    function annotate(nodes: DispatchNode[]): void {
      for (const node of nodes) {
        const fname = `${node.role}-${node.slug}.jsonl`;
        const rel = node.parentSlug === ROOT_PARENT_SLUG
          ? `${iterId}/${fname}`
          : `${iterId}/${node.parentSlug}/${fname}`;
        // Override logBase (a server-local absolute path in the raw
        // record) with a UI-relative path. Keep the same field name so
        // the client doesn't need to learn a second one.
        node.logBase = rel;

        if (node.reportPath) {
          const abs = path.resolve(node.reportPath);
          if (abs.startsWith(taskResultsRoot + path.sep) || abs === taskResultsRoot) {
            node.reportPath = path.relative(taskResultsRoot, abs);
          }
          // If the report is under a different root (unexpected) we
          // leave the absolute path so the dashboard can at least
          // display it as a hint; the /api/task-results/* route will
          // refuse to serve it.
        }

        annotate(node.children);
      }
    }
    annotate(tree);

    return { iter: iterId, dispatch: tree };
  });

  // Subagent report files. ``task_results/<role>-<slug>.md`` for root
  // invocations; ``task_results/<parent-slug>/<role>-<slug>.md`` for
  // children dispatched by a coordinator (Workstream A). Paths returned
  // by /api/logs/:iter/tree are relative to this root.
  fastify.get('/api/task-results/*', async (req, reply) => {
    const subpath = (req.params as Record<string, string>)['*'];
    if (!subpath) return reply.status(400).send({ error: 'Missing path' });

    const root = path.join(archonPath, 'task_results');
    const normalized = path.normalize(subpath).replace(/^(\.\.[/\\])+/, '');
    const full = path.join(root, normalized);
    // Re-resolve to absolute to catch any remaining traversal attempts.
    const abs = path.resolve(full);
    if (!abs.startsWith(root + path.sep) && abs !== root) {
      return reply.status(400).send({ error: 'Invalid path' });
    }
    if (!fs.existsSync(abs)) return reply.status(404).send({ error: 'Not found' });

    const stat = fs.statSync(abs);
    if (!stat.isFile()) return reply.status(400).send({ error: 'Not a file' });

    if (abs.endsWith('.md')) {
      const content = readFileOr(abs, '');
      return [{
        ts: stat.mtime.toISOString(),
        event: 'text',
        content,
      }];
    }
    // Non-markdown task_results files are unusual; serve as raw text.
    const content = readFileOr(abs, '');
    return [{
      ts: stat.mtime.toISOString(),
      event: 'text',
      content,
    }];
  });

  // Wildcard log content — supports both .jsonl (parsed) and .md (raw).
  fastify.get('/api/logs/*', async (req, reply) => {
    const subpath = (req.params as Record<string, string>)['*'];
    if (!subpath) return reply.status(400).send({ error: 'Missing path' });
    const filePath = resolveLogPath(logsPath, subpath, archonPath);
    if (!filePath || !fs.existsSync(filePath)) return reply.status(404).send({ error: 'Not found' });

    if (filePath.endsWith('.md')) {
      // Serve markdown as a single synthetic "text" log entry so the existing
      // client log-viewer can render it without a separate code path.
      const content = readFileOr(filePath, '');
      const stat = fs.statSync(filePath);
      return [{
        ts: stat.mtime.toISOString(),
        event: 'text',
        content,
      }];
    }

    return parseJsonl(filePath);
  });

  // WebSocket streaming (JSONL only; .md files are static artifacts).
  fastify.get('/api/log-stream/*', { websocket: true }, (socket, req) => {
    const subpath = (req.params as Record<string, string>)['*'] || '';
    const filePath = resolveLogPath(logsPath, subpath, archonPath);
    if (!filePath || !fs.existsSync(filePath) || !filePath.endsWith('.jsonl')) {
      socket.send(JSON.stringify({ type: 'error', message: 'Not found or not streamable' }));
      socket.close();
      return;
    }

    let lastSize = fs.statSync(filePath).size;
    socket.send(JSON.stringify({ type: 'ready', size: lastSize }));

    const watcher = fs.watch(filePath, () => {
      try {
        const newSize = fs.statSync(filePath).size;
        if (newSize <= lastSize) return;
        const stream = fs.createReadStream(filePath, { start: lastSize, end: newSize - 1, encoding: 'utf-8' });
        let buffer = '';
        stream.on('data', (chunk) => {
          buffer += chunk;
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.trim()) try { socket.send(line); } catch { /* ignore */ }
          }
        });
        stream.on('end', () => {
          if (buffer.trim()) try { socket.send(buffer); } catch { /* ignore */ }
        });
        lastSize = newSize;
      } catch { /* ignore stat errors during write */ }
    });

    socket.on('close', () => watcher.close());
  });
}