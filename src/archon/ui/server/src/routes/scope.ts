/** Scope information — returns scope status and files when running in scope mode */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { loadPeers } from '../paths.js';

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  fastify.get('/api/scope', async () => {
    const scopePath = process.env.ARCHON_SCOPE_PATH;
    if (!scopePath) {
      return { inScope: false };
    }

    let readme = '';
    const readmePaths = [
      path.join(scopePath, 'README.md'),
      path.join(scopePath, 'readme.md'),
      path.join(scopePath, 'README.MD')
    ];
    for (const rp of readmePaths) {
      if (fs.existsSync(rp)) {
        try {
          readme = fs.readFileSync(rp, 'utf8');
          break;
        } catch {
          // ignore
        }
      }
    }

    let roadmap = '';
    // roadmap.md lives at the scope root (next to README.md). For backward
    // compatibility, fall back to the old .archon-scope/ location.
    const roadmapCandidates = [
      path.join(scopePath, 'roadmap.md'),
      path.join(scopePath, '.archon-scope', 'roadmap.md'),
    ];
    for (const rp of roadmapCandidates) {
      if (fs.existsSync(rp)) {
        try {
          roadmap = fs.readFileSync(rp, 'utf8');
          break;
        } catch {
          // ignore
        }
      }
    }

    const peers = loadPeers(paths.projectPath);
    const hostName = path.basename(paths.projectPath);
    const hostHasDag = fs.existsSync(path.join(paths.projectPath, '.leandag', 'dag.json'));

    const members = [
      { name: hostName, path: paths.projectPath, has_dag: hostHasDag },
      ...peers
    ];

    return {
      inScope: true,
      scopePath,
      readme,
      roadmap,
      members,
    };
  });
}
