import { useQuery } from '@tanstack/react-query';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export interface GraphDeclaration {
  id: string; kind: string; name: string; file: string; line: number;
  hasSorry: boolean; sorryCount: number; signature: string;
  totalAttempts: number; latestMilestoneStatus?: string; milestoneSessions: string[]; blocker?: string;
}

export interface GraphEdge { from: string; to: string; }
export interface GraphFileGroup {
  file: string;
  declarations: string[];
  /** Per-file outcome from the latest multilane round, when applicable.
   *   cleared    — at least one lane fully cleared the file (no sorries left)
   *   progressed — at least one lane made merge-worthy partial progress
   *   attempted  — lanes ran but none was merge-worthy
   *   undefined  — file was not an objective in the latest iter
   */
  laneStatus?: 'cleared' | 'progressed' | 'attempted';
}

export interface DeclarationsResponse {
  declarations: GraphDeclaration[];
  edges: GraphEdge[];
  files: GraphFileGroup[];
}

export interface TimelinePoint {
  iteration: string; timestamp?: string; totalSorry: number;
  perFile: Record<string, number>;
  perDeclaration: Record<string, { hasSorry: boolean; sorryCount: number }>;
  changedDeclarations: string[];
}

export interface NodeMilestoneInfo {
  sessionId: string; status: string; attempts: unknown[];
  blocker?: string; nextSteps?: string; keyLemmas?: string[];
}

export interface NodeDetail {
  declaration: {
    id: string; kind: string; name: string; file: string;
    line: number; endLine: number; hasSorry: boolean; sorryCount: number;
    signature: string; body: string;
  } | null;
  milestones: NodeMilestoneInfo[];
}

// ── Prover log types ────────────────────────────────────────────────

export interface ProverLogEntry {
  ts: string;
  event: 'thinking' | 'text' | 'tool_call' | 'tool_result' | 'code_snapshot' | 'session_end';
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  step?: number;
  file?: string;
  snapshot_path?: string;
  session_id?: string;
  total_cost_usd?: number;
  duration_ms?: number;
  num_turns?: number;
  summary?: string;
}

export interface LogStats {
  thinkingCount: number;
  toolCallCount: number;
  textCount: number;
  codeSnapshotCount: number;
  totalEntries: number;
  durationMs?: number;
  totalCost?: number;
  numTurns?: number;
  sessionSummary?: string;
  startTs?: string;
  endTs?: string;
}

export interface LogResponse {
  entries: ProverLogEntry[];
  stats: LogStats;
}

// ── Hooks ───────────────────────────────────────────────────────────

export function useProofGraphDeclarations() {
  return useQuery<DeclarationsResponse>({
    queryKey: ['proofgraphDeclarations'],
    queryFn: () => fetchJson('/api/proofgraph/declarations'),
    refetchInterval: 15000,
  });
}

export function useProofGraphTimeline() {
  return useQuery<TimelinePoint[]>({
    queryKey: ['proofgraphTimeline'],
    queryFn: () => fetchJson('/api/proofgraph/timeline'),
    refetchInterval: 15000,
  });
}

export function useProofGraphSnapshot(iteration: string) {
  return useQuery<DeclarationsResponse>({
    queryKey: ['proofgraphSnapshot', iteration],
    queryFn: () => fetchJson(`/api/proofgraph/snapshot/${iteration}`),
    enabled: !!iteration,
    placeholderData: (prev) => prev,
  });
}

export function useProofGraphNodeDetail(file: string, name: string, iteration?: string) {
  const url = iteration
    ? `/api/proofgraph/node/${encodeURIComponent(file)}/${encodeURIComponent(name)}?iteration=${encodeURIComponent(iteration)}`
    : `/api/proofgraph/node/${encodeURIComponent(file)}/${encodeURIComponent(name)}`;
  return useQuery<NodeDetail>({
    queryKey: ['proofgraphNode', file, name, iteration || ''],
    queryFn: () => fetchJson(url),
    enabled: !!file && !!name,
  });
}

/** Fetch prover log entries for a file at a specific iteration */
export function useProofGraphLogs(file: string, iteration?: string) {
  const url = file && iteration
    ? `/api/proofgraph/logs/${encodeURIComponent(file)}/${encodeURIComponent(iteration)}`
    : '';
  return useQuery<LogResponse>({
    queryKey: ['proofgraphLogs', file, iteration || ''],
    queryFn: () => fetchJson(url),
    enabled: !!file && !!iteration,
  });
}