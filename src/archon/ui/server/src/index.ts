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
import { register as registerMultilane } from './routes/multilane.js';
import { register as registerDag } from './routes/dag.js';
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

  // `forceCloseConnections: true` makes `fastify.close()` destroy any
  // open keep-alive / websocket connections immediately instead of
  // waiting for them to drain. The dashboard holds long-lived websockets
  // for live log streaming — without this flag, shutdown blocks until
  // every browser tab closes, which is the "exit takes forever" symptom
  // reported by users on Ctrl+C.
  const fastify = Fastify({ logger: false, forceCloseConnections: true });
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
  registerMultilane(fastify, paths);
  registerDag(fastify, paths);

  // Bind dual-stack (IPv6 `::` with IPV6_V6ONLY=0 accepts IPv4 too on Linux/macOS).
  // Binding to `0.0.0.0` alone causes "waiting for host…" when the browser
  // resolves localhost to ::1 first. Fall back to IPv4-only if IPv6 is disabled.
  try {
    await fastify.listen({ port, host: '::' });
  } catch (e: any) {
    if (e?.code === 'EAFNOSUPPORT' || e?.code === 'EADDRNOTAVAIL') {
      await fastify.listen({ port, host: '0.0.0.0' });
    } else {
      throw e;
    }
  }
  return fastify;
}

// CLI entry point
if (import.meta.url === `file://${process.argv[1]}`) {
  const { projectPath, port } = parseArgs();
  // Prefer 127.0.0.1 in the printed URL — resolves predictably on every system,
  // whereas `localhost` may hit ::1 first on configurations with IPv6-first DNS.
  console.log(`Archon UI → http://127.0.0.1:${port}  (project: ${projectPath})`);
  createServer({ projectPath, port })
    .then(fastify => {
      // Graceful shutdown: when the parent (`archon dashboard`) sends SIGTERM
      // or the user Ctrl+Cs, close fastify so the listening socket is fully
      // released before we exit. Without this, a quick re-launch of the
      // dashboard could see EADDRINUSE on the same port.
      let shuttingDown = false;
      const shutdown = async (sig: string) => {
        if (shuttingDown) return;
        shuttingDown = true;
        console.log(`\n[archon-ui] Received ${sig}, closing server…`);
        // Hard cap on shutdown time. `forceCloseConnections: true` should
        // already make this near-instant, but if a route handler is
        // sitting in a slow `await` (e.g. an unresponsive child process)
        // we don't want the user staring at the terminal. .unref() so a
        // fast shutdown doesn't keep the event loop alive.
        const watchdog = setTimeout(() => {
          console.error('[archon-ui] Shutdown watchdog fired — forcing exit.');
          process.exit(0);
        }, 1500);
        watchdog.unref();
        try {
          await fastify.close();
        } catch (err) {
          console.error('[archon-ui] Error during shutdown:', err);
        }
        process.exit(0);
      };
      process.on('SIGTERM', () => { void shutdown('SIGTERM'); });
      process.on('SIGINT', () => { void shutdown('SIGINT'); });
    })
    .catch(err => { console.error(err); process.exit(1); });
}