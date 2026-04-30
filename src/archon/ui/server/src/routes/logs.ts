/** Logs API — tree listing, content retrieval, WebSocket streaming */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { parseJsonl, readFileOr } from '../utils.js';
import { mapIterToPhaseCommits, type InnerCommit, type IterPhaseCommits } from '../utils/innerGit.js';
import type { ProjectPaths } from './project.js';

interface LogFileEntry {
  name: string;
  path: string;
  size: number;
  modified: string;
  role?: string;
  /** Commit for this specific file's phase (plan/refactor/prover/review). */
  commit?: InnerCommit;
}
interface LogGroup { id: string; files: LogFileEntry[]; meta?: Record<string, unknown> }

/** Pick the commit that "belongs" to a file, given its role and prover slug. */
function commitForFile(
  phaseCommits: IterPhaseCommits | undefined,
  role: string | undefined,
  fileName: string,
): InnerCommit | undefined {
  if (!phaseCommits) return undefined;
  if (!role) return phaseCommits.latest;
  // Plan and refactor logs cover both their own jsonl and the archived .md.
  if (role === 'plan' || role === 'plan-post-refactor') return phaseCommits.plan;
  if (role === 'refactor' || role === 'refactor-manual'
      || role === 'refactor-directive' || role === 'refactor-report') return phaseCommits.refactor;
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
      .filter(f => f.endsWith('.jsonl') && fs.statSync(path.join(logsPath, f)).isFile())
      .map(f => {
        const stat = fs.statSync(path.join(logsPath, f));
        return { name: f, path: f, size: stat.size, modified: stat.mtime.toISOString() };
      })
      .sort((a, b) => b.modified.localeCompare(a.modified));

    const groups: LogGroup[] = [];
    const iterDirs = fs.readdirSync(logsPath)
      .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
      .sort();

    for (const dir of iterDirs) {
      const dirPath = path.join(logsPath, dir);
      const files: LogFileEntry[] = [];
      const phaseCommits = phaseByIter.get(dir);

      // Standard JSONL logs at the iteration root.
      for (const f of fs.readdirSync(dirPath).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl') && f !== 'provers-combined.jsonl')) {
        const full = path.join(dirPath, f);
        if (!fs.statSync(full).isFile()) continue;
        const role = f.replace('.jsonl', '');
        const stat = fs.statSync(full);
        files.push({
          name: f, path: `${dir}/${f}`, size: stat.size, modified: stat.mtime.toISOString(), role,
          commit: commitForFile(phaseCommits, role, f),
        });
      }

      // Refactor artifacts (archived markdown).
      for (const artifact of ['refactor-directive.md', 'refactor-report.md']) {
        const full = path.join(dirPath, artifact);
        if (!fs.existsSync(full) || !fs.statSync(full).isFile()) continue;
        const stat = fs.statSync(full);
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

      // Parallel prover JSONL logs — each gets the commit for its specific file slug.
      const proversDir = path.join(dirPath, 'provers');
      const seenProverNames = new Set<string>();
      if (fs.existsSync(proversDir) && fs.statSync(proversDir).isDirectory()) {
        for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl')).sort()) {
          const full = path.join(proversDir, f);
          const stat = fs.statSync(full);
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