/** Summary API — aggregated cost/token/session stats across all logs */
import fs from 'fs';
import type { FastifyInstance } from 'fastify';
import type { LogEntry, AggregatedStats, SessionSummary } from '../types.js';
import { parseJsonl, primaryModelId } from '../utils.js';
import type { ProjectPaths } from './project.js';

function calculateStats(logs: LogEntry[]): AggregatedStats {
  const sessions: SessionSummary[] = [];
  let totalCost = 0, totalDuration = 0, totalTokensIn = 0, totalTokensOut = 0;

  for (const entry of logs) {
    if (entry.event !== 'session_end') continue;
    const cost = entry.total_cost_usd || 0;
    const duration = entry.duration_ms || 0;
    const tokIn = entry.input_tokens || 0;
    const tokOut = entry.output_tokens || 0;
    const model = primaryModelId(entry.model_usage);
    totalCost += cost;
    totalDuration += duration;
    totalTokensIn += tokIn;
    totalTokensOut += tokOut;
    sessions.push({
      cost, duration, tokensIn: tokIn, tokensOut: tokOut,
      model, turns: entry.num_turns || 0, timestamp: entry.ts,
      summary: entry.summary,
    });
  }
  return { totalCost, totalDuration, totalTokensIn, totalTokensOut, sessionCount: sessions.length, sessions };
}

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  fastify.get('/api/summary', async (req) => {
    const { logsPath } = req.paths;
    if (!fs.existsSync(logsPath)) return { totalCost: 0, totalDuration: 0, totalTokensIn: 0, totalTokensOut: 0, sessionCount: 0, sessions: [] };
    let allLogs: LogEntry[] = [];
    function walkJsonl(dir: string) {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = `${dir}/${entry.name}`;
        if (entry.isDirectory()) walkJsonl(full);
        else if (entry.isFile() && entry.name.endsWith('.jsonl')) allLogs = allLogs.concat(parseJsonl(full));
      }
    }
    walkJsonl(logsPath);
    return calculateStats(allLogs);
  });
}

