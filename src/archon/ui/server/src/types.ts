export interface LogEntry {
  ts: string;
  event: 'shell' | 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'session_end' | 'code_snapshot';
  level?: 'info' | 'warn' | 'error';
  message?: string;
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  // session_end fields (actual JSONL format)
  total_cost_usd?: number;
  duration_ms?: number;
  num_turns?: number;
  input_tokens?: number;
  output_tokens?: number;
  model_usage?: Record<string, { inputTokens: number; outputTokens: number; costUSD: number }>;
  summary?: string;
  session_id?: string;
}

export interface ProgressData {
  stage: string;
  objectives: string[];
  checklist: { label: string; done: boolean }[];
}

export interface Task {
  id: string;
  theorem: string;
  file: string;
  status: 'pending' | 'in-progress' | 'done';
  proofSketch?: string;
}

/** Per-phase status as stored in meta.json */
export interface PhaseStatus {
  status: string;
  durationSecs?: number;
}

/** Full iteration meta.json shape */
export interface IterationMeta {
  iteration?: number;
  stage?: string;
  mode?: string;
  startedAt?: string;
  completedAt?: string;
  wallTimeSecs?: number;
  plan?: PhaseStatus;
  refactor?: PhaseStatus;
  prover?: PhaseStatus;
  review?: PhaseStatus;
  provers?: Record<string, { file: string; status: string }>;
  sorry_count?: number;
}

export interface SessionSummary {
  cost: number;
  duration: number;
  tokensIn: number;
  tokensOut: number;
  model: string;
  turns: number;
  timestamp: string;
  summary?: string;
}

export interface AggregatedStats {
  totalCost: number;
  totalDuration: number;
  totalTokensIn: number;
  totalTokensOut: number;
  sessionCount: number;
  sessions: SessionSummary[];
}