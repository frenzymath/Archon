/** Summary API — aggregated cost/token/session stats across all logs */
import fs from 'fs';
import type { FastifyInstance } from 'fastify';
import type { LogEntry, AggregatedStats, SessionSummary } from '../types.js';
import { parseJsonl, primaryModelId } from '../utils.js';
import type { ProjectPaths } from './project.js';

function estimateCostUSD(model: string, inputTokens: number, outputTokens: number, cacheReadTokens: number = 0): number {
  const m = model.toLowerCase();
  if (m.includes('sonnet')) {
    return (inputTokens * 3.0 + outputTokens * 15.0 + cacheReadTokens * 0.30) / 1000000;
  } else if (m.includes('haiku')) {
    return (inputTokens * 0.8 + outputTokens * 4.0 + cacheReadTokens * 0.08) / 1000000;
  } else if (m.includes('opus')) {
    return (inputTokens * 15.0 + outputTokens * 75.0) / 1000000;
  } else if (m.includes('flash')) {
    return (inputTokens * 0.075 + outputTokens * 0.30) / 1000000;
  } else if (m.includes('pro')) {
    return (inputTokens * 1.25 + outputTokens * 5.0) / 1000000;
  } else if (m.includes('gpt-4o-mini')) {
    return (inputTokens * 0.15 + outputTokens * 0.60) / 1000000;
  } else if (m.includes('gpt-4o')) {
    return (inputTokens * 2.50 + outputTokens * 10.00) / 1000000;
  } else if (m.includes('o1-mini') || m.includes('o3-mini')) {
    return (inputTokens * 1.10 + outputTokens * 4.40) / 1000000;
  } else if (m.includes('o1-preview') || m.includes('o1')) {
    return (inputTokens * 15.00 + outputTokens * 60.00) / 1000000;
  } else if (m.includes('deepseek-chat') || m.includes('deepseek-v3') || m.includes('deepseek')) {
    return (inputTokens * 0.14 + outputTokens * 0.28) / 1000000;
  } else if (m.includes('deepseek-reasoner') || m.includes('deepseek-r1')) {
    return (inputTokens * 0.55 + outputTokens * 2.19) / 1000000;
  }
  return (inputTokens * 2.50 + outputTokens * 10.00) / 1000000;
}

function calculateStats(fileLogs: { name: string; logs: LogEntry[] }[]): AggregatedStats {
  const sessions: SessionSummary[] = [];
  let totalCost = 0, totalDuration = 0, totalTokensIn = 0, totalTokensOut = 0;

  for (const { name, logs } of fileLogs) {
    for (const entry of logs) {
      if (entry.event !== 'session_end') continue;
      const tokIn = entry.input_tokens || 0;
      const tokOut = entry.output_tokens || 0;
      const model = primaryModelId(entry.model_usage);
      const entryCost = entry.total_cost_usd || 0;
      const cost = entryCost || estimateCostUSD(model, tokIn, tokOut, entry.cache_read_input_tokens || 0);
      const duration = entry.duration_ms || 0;

      totalCost += cost;
      totalDuration += duration;
      totalTokensIn += tokIn;
      totalTokensOut += tokOut;
      sessions.push({
        operation: name,
        cost,
        duration,
        tokensIn: tokIn,
        tokensOut: tokOut,
        model,
        turns: entry.num_turns || 0,
        timestamp: entry.ts,
        summary: entry.summary,
      });
    }
  }

  // Sort sessions by timestamp ascending so they are in chronological order
  sessions.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

  return { totalCost, totalDuration, totalTokensIn, totalTokensOut, sessionCount: sessions.length, sessions };
}

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  fastify.get('/api/summary', async (req) => {
    const { logsPath } = req.paths;
    if (!fs.existsSync(logsPath)) return { totalCost: 0, totalDuration: 0, totalTokensIn: 0, totalTokensOut: 0, sessionCount: 0, sessions: [] };
    
    const fileLogs: { name: string; logs: LogEntry[] }[] = [];
    function walkJsonl(dir: string) {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = `${dir}/${entry.name}`;
        if (entry.isDirectory()) {
          walkJsonl(full);
        } else if (entry.isFile() && entry.name.endsWith('.jsonl')) {
          if (entry.name === 'provers-combined.jsonl') continue;
          
          let name = full.slice(logsPath.length);
          if (name.startsWith('/')) name = name.slice(1);
          // Strip iter-XXX/
          name = name.replace(/^iter-\d+\//, '');
          // Strip .jsonl
          if (name.endsWith('.jsonl')) name = name.slice(0, -6);
          
          fileLogs.push({ name, logs: parseJsonl(full) });
        }
      }
    }
    walkJsonl(logsPath);
    return calculateStats(fileLogs);
  });
}

