/** Journal API — proof-journal sessions, milestones, summaries */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { readFileOr } from '../utils.js';
import type { ProjectPaths } from './project.js';

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  // Per-request paths (base project or an allowed peer via `?project=`).
  const sessionsRoot = (archonPath: string) => path.join(archonPath, 'proof-journal', 'sessions');

  function listSessions(archonPath: string): string[] {
    const root = sessionsRoot(archonPath);
    if (!fs.existsSync(root)) return [];
    return fs.readdirSync(root)
      .filter(d => d.startsWith('session_') && fs.statSync(path.join(root, d)).isDirectory())
      .sort((a, b) => {
        const na = parseInt(a.replace('session_', ''), 10) || 0;
        const nb = parseInt(b.replace('session_', ''), 10) || 0;
        return na - nb;
      });
  }

  function sessionDir(archonPath: string, id: string): string {
    return path.join(sessionsRoot(archonPath), id);
  }

  // List all sessions with a summary of what files exist in each
  fastify.get('/api/journal/sessions', async (req) => {
    const { archonPath } = req.paths;
    return listSessions(archonPath).map(id => {
      const dir = sessionDir(archonPath, id);
      const hasMilestones = fs.existsSync(path.join(dir, 'milestones.jsonl'));
      const hasSummary = fs.existsSync(path.join(dir, 'summary.md'));
      const hasRecommendations = fs.existsSync(path.join(dir, 'recommendations.md'));
      return { id, hasMilestones, hasSummary, hasRecommendations };
    });
  });

  // Milestones — return empty array if file missing
  fastify.get<{ Params: { id: string } }>('/api/journal/sessions/:id/milestones', async (req) => {
    const filePath = path.join(sessionDir(req.paths.archonPath, req.params.id), 'milestones.jsonl');
    const content = readFileOr(filePath, '');
    if (!content) return [];
    return content.split('\n').filter(Boolean).map(line => {
      try { return JSON.parse(line); } catch { return null; }
    }).filter(Boolean);
  });

  // Summary — return empty content if file missing
  fastify.get<{ Params: { id: string } }>('/api/journal/sessions/:id/summary', async (req) => {
    const filePath = path.join(sessionDir(req.paths.archonPath, req.params.id), 'summary.md');
    return { content: readFileOr(filePath, '') };
  });

  // Recommendations — return empty content if file missing
  fastify.get<{ Params: { id: string } }>('/api/journal/sessions/:id/recommendations', async (req) => {
    const filePath = path.join(sessionDir(req.paths.archonPath, req.params.id), 'recommendations.md');
    return { content: readFileOr(filePath, '') };
  });

  // All milestones across all sessions (for cross-session aggregation)
  fastify.get('/api/journal/all-milestones', async (req) => {
    const { archonPath } = req.paths;
    const result: { sessionId: string; milestones: unknown[] }[] = [];
    for (const id of listSessions(archonPath)) {
      const filePath = path.join(sessionDir(archonPath, id), 'milestones.jsonl');
      const content = readFileOr(filePath, '');
      if (!content) continue;
      const milestones = content.split('\n').filter(Boolean).map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);
      if (milestones.length > 0) result.push({ sessionId: id, milestones });
    }
    return result;
  });

  // Project status
  fastify.get('/api/journal/status', async (req) => {
    const filePath = path.join(req.paths.archonPath, 'PROJECT_STATUS.md');
    return { content: readFileOr(filePath, '') };
  });
}
