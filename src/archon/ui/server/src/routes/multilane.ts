/** Multilane API — experimental dashboard surface for shared-plan multi-lane runs */
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { readMultiLaneSummary } from '../utils/multilane.js';

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  fastify.get('/api/multilane', async (req) => {
    return readMultiLaneSummary(req.paths.archonPath);
  });
}
