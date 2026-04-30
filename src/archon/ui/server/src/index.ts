/**
 * Archon UI Server — entry point
 *
 * Composes route modules and starts Fastify.
 * Each route module is self-contained under ./routes/.
 */
import Fastify from 'fastify';
import cors from '@fastify/cors';
import staticFiles from '@fastify/static';
import websocket from '@fastify/websocket';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// Route modules
import { register as registerProject } from './routes/project.js';
import { register as registerLogs } from './routes/logs.js';
import { register as registerIterations } from './routes/iterations.js';
import { register as registerJournal } from './routes/journal.js';
import { register as registerSummary } from './routes/summary.js';
import { register as registerSnapshots } from './routes/snapshots.js';
import { register as registerProofGraph } from './routes/proofgraph.js';
import { register as registerGit } from './routes/git.js';
import type { ProjectPaths } from './routes/project.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function parseArgs(): { projectPath: string; port: number } {
  const args = process.argv.slice(2);
  let projectPath = process.cwd();
  let port = 8080;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--project' && i + 1 < args.length) projectPath = args[++i];
    else if (args[i] === '--port' && i + 1 < args.length) port = parseInt(args[++i], 10);
  }
  return { projectPath, port };
}

export async function createServer(options: { projectPath: string; port: number }) {
  const { projectPath, port } = options;

  const paths: ProjectPaths = {
    projectPath,
    archonPath: path.join(projectPath, '.archon'),
    logsPath: path.join(projectPath, '.archon', 'logs'),
  };

  const fastify = Fastify({ logger: false });
  await fastify.register(cors);
  await fastify.register(websocket);

  // Serve built client (SPA)
  const clientBuildPath = path.join(__dirname, '../../client/dist');
  if (fs.existsSync(clientBuildPath)) {
    await fastify.register(staticFiles, { root: clientBuildPath, prefix: '/' });
    fastify.setNotFoundHandler((req, reply) => {
      if (req.url.startsWith('/api/')) return reply.status(404).send({ error: 'Not found' });
      return reply.sendFile('index.html');
    });
  }

  // Register route modules
  registerProject(fastify, paths);
  registerLogs(fastify, paths);
  registerIterations(fastify, paths);
  registerJournal(fastify, paths);
  registerSummary(fastify, paths);
  registerSnapshots(fastify, paths);
  registerProofGraph(fastify, paths);
  registerGit(fastify, paths);

  await fastify.listen({ port, host: '0.0.0.0' });
  return fastify;
}

// CLI entry point
if (import.meta.url === `file://${process.argv[1]}`) {
  const { projectPath, port } = parseArgs();
  console.log(`Archon UI → http://localhost:${port}  (project: ${projectPath})`);
  createServer({ projectPath, port }).catch(err => { console.error(err); process.exit(1); });
}