/**
 * Source — current state of the project's Lean files on disk.
 *
 * Two endpoints back the Code view:
 *   - GET /api/source/files          → list of relative .lean paths + sizes
 *   - GET /api/source/file?path=…    → the file's text content
 *
 * Both are scoped via the same ?project=… mechanism the rest of the API uses
 * and stay strictly within the project root (no `..` traversal).
 */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';

const MAX_FILE_BYTES = 2 * 1024 * 1024;
const SKIP_DIRS = new Set([
  '.lake', '_lake', '.archon', '.git', 'node_modules', '.leandag', 'lake-packages',
]);

function walkLeanFiles(root: string): { path: string; size: number }[] {
  const out: { path: string; size: number }[] = [];
  const stack: string[] = [root];
  while (stack.length > 0) {
    const dir = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) stack.push(abs);
      } else if (entry.isFile() && entry.name.endsWith('.lean')) {
        try {
          const st = fs.statSync(abs);
          out.push({ path: path.relative(root, abs), size: st.size });
        } catch {
          /* ignore */
        }
      }
    }
  }
  out.sort((a, b) => a.path.localeCompare(b.path));
  return out;
}

/** Reject anything that resolves outside `root`. Returns the absolute path or null. */
function safeJoin(root: string, rel: string): string | null {
  if (!rel) return null;
  const resolved = path.resolve(root, rel);
  const normRoot = path.resolve(root) + path.sep;
  if (resolved !== path.resolve(root) && !resolved.startsWith(normRoot)) return null;
  return resolved;
}

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  fastify.get('/api/source/files', async (req) => {
    const { projectPath } = req.paths;
    return { files: walkLeanFiles(projectPath) };
  });

  fastify.get('/api/source/file', async (req, reply) => {
    const { projectPath } = req.paths;
    const rel = (req.query as { path?: string } | undefined)?.path;
    if (!rel) return reply.status(400).send({ error: 'path query parameter required' });
    if (!rel.endsWith('.lean')) {
      return reply.status(400).send({ error: 'only .lean files are served' });
    }
    const abs = safeJoin(projectPath, rel);
    if (!abs) return reply.status(400).send({ error: 'invalid path' });
    try {
      const st = fs.statSync(abs);
      if (!st.isFile()) return reply.status(404).send({ error: 'not a file' });
      if (st.size > MAX_FILE_BYTES) {
        return reply.status(413).send({ error: 'file too large', size: st.size });
      }
      const content = fs.readFileSync(abs, 'utf8');
      return { path: rel, size: st.size, content };
    } catch {
      return reply.status(404).send({ error: 'not found' });
    }
  });
}
