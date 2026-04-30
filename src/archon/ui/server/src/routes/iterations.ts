/** Iterations API — meta.json based iteration summaries */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { parseJsonl, readFileOr } from '../utils.js';
import type { ProjectPaths } from './project.js';

function listIterDirs(logsPath: string): string[] {
  if (!fs.existsSync(logsPath)) return [];
  return fs.readdirSync(logsPath)
    .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
    .sort();
}

function readMeta(logsPath: string, iterDir: string): Record<string, unknown> | null {
  const metaFile = path.join(logsPath, iterDir, 'meta.json');
  try { return JSON.parse(fs.readFileSync(metaFile, 'utf-8')); } catch { return null; }
}

/** Flags describing which refactor artifacts are present in an iteration. */
function refactorFlags(iterPath: string): { hasDirective: boolean; hasReport: boolean } {
  return {
    hasDirective: fs.existsSync(path.join(iterPath, 'refactor-directive.md')),
    hasReport: fs.existsSync(path.join(iterPath, 'refactor-report.md')),
  };
}

/** Walk multilane/lanes/<lane>/<iter>/provers/ and return file entries
 *  whose slugs are tagged with the lane name, e.g. "Foo__kimi". Used so
 *  in-progress multilane runs surface their per-lane prover JSONLs even
 *  when the runtime symlinks haven't been created yet (or the run was
 *  started with an older archon that didn't symlink at all). The archonPath
 *  argument is the ".archon/" root, not the logs directory. */
function listMultilaneLaneLogs(
  archonPath: string,
  iterDir: string,
): { slug: string; size: number; path: string }[] {
  const lanesRoot = path.join(archonPath, 'multilane', 'lanes');
  if (!fs.existsSync(lanesRoot)) return [];
  const out: { slug: string; size: number; path: string }[] = [];
  for (const lane of fs.readdirSync(lanesRoot)) {
    const proversDir = path.join(lanesRoot, lane, iterDir, 'provers');
    if (!fs.existsSync(proversDir)) continue;
    for (const f of fs.readdirSync(proversDir)) {
      if (!f.endsWith('.jsonl') || f.endsWith('.raw.jsonl')) continue;
      const full = path.join(proversDir, f);
      try {
        const stat = fs.statSync(full);
        if (!stat.isFile()) continue;
        // Slug shape matches what the standard prover-log enumeration
        // expects, prefixed with __lane so the dashboard can group them.
        out.push({ slug: `${f.replace('.jsonl', '')}__${lane}`, size: stat.size, path: full });
      } catch { /* ignore */ }
    }
  }
  return out;
}

/** Walk multilane/runtime/<iter>/merges/ for the per-file merge-agent
 *  JSONLs so the dashboard can show what the merger picked. */
function listMultilaneMergeLogs(
  archonPath: string,
  iterDir: string,
): { slug: string; size: number; path: string }[] {
  const mergesDir = path.join(archonPath, 'multilane', 'runtime', iterDir, 'merges');
  if (!fs.existsSync(mergesDir)) return [];
  const out: { slug: string; size: number; path: string }[] = [];
  for (const f of fs.readdirSync(mergesDir)) {
    if (!f.endsWith('.jsonl') || f.endsWith('.raw.jsonl')) continue;
    const full = path.join(mergesDir, f);
    try {
      const stat = fs.statSync(full);
      if (!stat.isFile()) continue;
      out.push({ slug: `${f.replace('.jsonl', '')}__merge`, size: stat.size, path: full });
    } catch { /* ignore */ }
  }
  return out;
}

/** List task_results-archive entries for an iteration. */
function listTaskResultsArchive(iterPath: string): { name: string; size: number }[] {
  const archiveDir = path.join(iterPath, 'task_results-archive');
  if (!fs.existsSync(archiveDir) || !fs.statSync(archiveDir).isDirectory()) return [];
  const files: { name: string; size: number }[] = [];
  for (const f of fs.readdirSync(archiveDir).sort()) {
    if (!f.endsWith('.md')) continue;
    const full = path.join(archiveDir, f);
    if (!fs.statSync(full).isFile()) continue;
    files.push({ name: f, size: fs.statSync(full).size });
  }
  return files;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { logsPath, archonPath } = paths;
  // We deduplicate when symlinks happen to share the same slug as the
  // multilane fall-back enumeration — the symlink (if it exists) wins.
  function mergeProverFiles(
    primary: { slug: string; size: number; path?: string }[],
    extra: { slug: string; size: number; path: string }[],
  ): { slug: string; size: number; path?: string }[] {
    const seen = new Set(primary.map(p => p.slug));
    return [...primary, ...extra.filter(e => !seen.has(e.slug))];
  }

  fastify.get('/api/iterations', async () => {
    return listIterDirs(logsPath).map(d => {
      const meta = readMeta(logsPath, d);
      const iterPath = path.join(logsPath, d);
      const refactor = refactorFlags(iterPath);
      const base: Record<string, unknown> = { id: d };
      if (meta) Object.assign(base, meta);
      // Always include these flags so the UI can render refactor indicators
      // without needing to request each iteration individually.
      base.hasRefactorDirective = refactor.hasDirective;
      base.hasRefactorReport = refactor.hasReport;
      return base;
    });
  });

  fastify.get<{ Params: { id: string } }>('/api/iterations/:id', async (req, reply) => {
    const iterDir = req.params.id;
    if (!iterDir.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const meta = readMeta(logsPath, iterDir);
    const iterPath = path.join(logsPath, iterDir);
    if (!fs.existsSync(iterPath)) return reply.status(404).send({ error: 'Not found' });

    const proversDir = path.join(iterPath, 'provers');
    const proverFiles: { slug: string; size: number; path?: string }[] = [];
    if (fs.existsSync(proversDir)) {
      for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl') && !f.endsWith('.raw.jsonl'))) {
        const stat = fs.statSync(path.join(proversDir, f));
        proverFiles.push({ slug: f.replace('.jsonl', ''), size: stat.size });
      }
    }
    // Multilane fallback: surface lane-side prover JSONLs and the
    // merger's JSONLs even when no symlinks have been created yet (or
    // the run was started with an older archon).
    const laneLogs = listMultilaneLaneLogs(archonPath, iterDir);
    const mergeLogs = listMultilaneMergeLogs(archonPath, iterDir);
    const merged = mergeProverFiles(mergeProverFiles(proverFiles, laneLogs), mergeLogs);

    const refactor = refactorFlags(iterPath);
    const taskResultsArchive = listTaskResultsArchive(iterPath);

    return {
      id: iterDir,
      ...(meta || {}),
      proverFiles: merged.map(({ slug, size }) => ({ slug, size })),
      hasRefactorDirective: refactor.hasDirective,
      hasRefactorReport: refactor.hasReport,
      taskResultsArchive,
    };
  });

  fastify.get<{ Params: { id: string; file: string } }>('/api/iterations/:id/provers/:file', async (req, reply) => {
    const { id, file } = req.params;
    if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const slug = file.endsWith('.jsonl') ? file.replace(/\.jsonl$/, '') : file;
    // Try the standard logs path first (handles non-multilane runs and
    // multilane runs where symlinks worked); fall back to walking
    // multilane/lanes/ and multilane/runtime/.../merges/ when the slug
    // ends in __<lane> or __merge so old runs without symlinks still
    // serve their logs.
    const standard = path.join(logsPath, id, 'provers', `${slug}.jsonl`);
    if (fs.existsSync(standard)) return parseJsonl(standard);
    const mergeMatch = slug.match(/^(.+)__merge$/);
    if (mergeMatch) {
      const mergeFile = path.join(archonPath, 'multilane', 'runtime', id, 'merges', `${mergeMatch[1]}.jsonl`);
      if (fs.existsSync(mergeFile)) return parseJsonl(mergeFile);
    }
    const laneMatch = slug.match(/^(.+)__(.+)$/);
    if (laneMatch) {
      const [, base, lane] = laneMatch;
      const laneFile = path.join(archonPath, 'multilane', 'lanes', lane, id, 'provers', `${base}.jsonl`);
      if (fs.existsSync(laneFile)) return parseJsonl(laneFile);
    }
    return reply.status(404).send({ error: 'Not found' });
  });

  // ── Refactor artifacts ──────────────────────────────────────────────

  fastify.get<{ Params: { id: string } }>('/api/iterations/:id/refactor-directive', async (req, reply) => {
    const { id } = req.params;
    if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const filePath = path.join(logsPath, id, 'refactor-directive.md');
    if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'No refactor directive for this iteration' });
    return { content: readFileOr(filePath, '') };
  });

  fastify.get<{ Params: { id: string } }>('/api/iterations/:id/refactor-report', async (req, reply) => {
    const { id } = req.params;
    if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const filePath = path.join(logsPath, id, 'refactor-report.md');
    if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'No refactor report for this iteration' });
    return { content: readFileOr(filePath, '') };
  });

  // ── Task-results archive ────────────────────────────────────────────

  fastify.get<{ Params: { id: string; file: string } }>(
    '/api/iterations/:id/task-results-archive/:file',
    async (req, reply) => {
      const { id, file } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
      // Sanitize: no path traversal
      const safeFile = path.basename(file);
      if (!safeFile.endsWith('.md')) return reply.status(400).send({ error: 'Only .md files supported' });
      const filePath = path.join(logsPath, id, 'task_results-archive', safeFile);
      if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'Not found' });
      return { name: safeFile, content: readFileOr(filePath, '') };
    },
  );
}