/** Peer projects — the read-only project-switch allowlist for the dashboard. */
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { loadPeers } from '../paths.js';

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  // The base project plus every peer it may read. The client renders this as a
  // project switcher; selecting one re-issues API calls with `?project=<path>`.
  fastify.get('/api/peer-projects', async () => {
    const peers = loadPeers(paths.projectPath);
    return {
      current: { name: path.basename(paths.projectPath), path: paths.projectPath },
      peers,
    };
  });
}
