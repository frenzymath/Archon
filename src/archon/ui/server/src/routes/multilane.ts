/** Multilane API — experimental dashboard surface for shared-plan multi-lane runs */
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { readMultiLaneSummary } from '../utils/multilane.js';

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { archonPath } = paths;

  fastify.get('/api/multilane', async () => {
    return readMultiLaneSummary(archonPath);
  });
}
