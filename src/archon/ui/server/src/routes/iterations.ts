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
  const { logsPath } = paths;

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
    const proverFiles: { slug: string; size: number }[] = [];
    if (fs.existsSync(proversDir)) {
      for (const f of fs.readdirSync(proversDir).filter(f => f.endsWith('.jsonl'))) {
        const stat = fs.statSync(path.join(proversDir, f));
        proverFiles.push({ slug: f.replace('.jsonl', ''), size: stat.size });
      }
    }

    const refactor = refactorFlags(iterPath);
    const taskResultsArchive = listTaskResultsArchive(iterPath);

    return {
      id: iterDir,
      ...(meta || {}),
      proverFiles,
      hasRefactorDirective: refactor.hasDirective,
      hasRefactorReport: refactor.hasReport,
      taskResultsArchive,
    };
  });

  fastify.get<{ Params: { id: string; file: string } }>('/api/iterations/:id/provers/:file', async (req, reply) => {
    const { id, file } = req.params;
    if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });
    const filePath = path.join(logsPath, id, 'provers', file.endsWith('.jsonl') ? file : `${file}.jsonl`);
    if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'Not found' });
    return parseJsonl(filePath);
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