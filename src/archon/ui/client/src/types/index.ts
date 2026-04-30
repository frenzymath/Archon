// --- Log types ---
export interface LogEntry {
  ts: string;
  event: 'shell' | 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'session_end' | 'code_snapshot';
  level?: 'info' | 'warn' | 'error';
  message?: string;
  content?: string;
  tool?: string;
  input?: Record<string, unknown>;
  // code_snapshot fields
  step?: number;
  file?: string;
  snapshot_path?: string;
  old_string?: string;
  new_string?: string;
  // session_end fields
  total_cost_usd?: number;
  duration_ms?: number;
  num_turns?: number;
  input_tokens?: number;
  output_tokens?: number;
  model_usage?: Record<string, { inputTokens: number; outputTokens: number; costUSD: number }>;
  summary?: string;
}

export interface LogFile {
  name: string;
  path: string;
  size: number;
  modified: string;
  role?: string;
  /** Commit associated with this specific file/phase. */
  commit?: { sha: string; shortSha: string; subject: string; date: string };
}

export interface LogGroup {
  id: string;
  files: LogFile[];
  meta?: {
    iteration?: number;
    stage?: string;
    mode?: string;
    startedAt?: string;
    completedAt?: string;
    wallTimeSecs?: number;
    plan?: { status: string; durationSecs?: number };
    refactor?: { status: string; durationSecs?: number };
    prover?: { status: string; durationSecs?: number };
    review?: { status: string; durationSecs?: number };
    provers?: Record<string, { file: string; status: string }>;
    commit?: { sha: string; shortSha: string; subject: string; date: string };
  };
}

export interface LogsResponse {
  flat: LogFile[];
  groups: LogGroup[];
}

// --- Progress types ---
export interface ProgressData {
  stage: string;
  objectives: string[];
  checklist: { label: string; done: boolean }[];
}

// --- Task types ---
export interface Task {
  id: string;
  theorem: string;
  file: string;
  status: 'pending' | 'in-progress' | 'done';
  proofSketch?: string;
}

// --- Session summary types ---
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

// --- Sorry count ---
export interface SorryCount {
  total: number;
  files: { file: string; count: number; lines: number[] }[];
}

// --- Journal types (aligned with cctest-dashboard Milestone/Attempt schema) ---
export interface Attempt {
  attempt: number;
  strategy: string;
  code_tried?: string;
  lean_error?: string;
  goal_before?: string;
  goal_after?: string;
  result: string;
  duration_min?: number;
  insight?: string;
}

export interface Milestone {
  timestamp: string;
  status: 'solved' | 'partial' | 'blocked' | 'failed_retry' | 'not_started';
  target: { file: string; theorem: string };
  session: { id: string; model: string };
  findings: { blocker?: string; key_lemmas_used?: string[] };
  attempts: Attempt[];
  next_steps?: string;
}

// --- Iteration types ---
export interface ProverMeta {
  file: string;
  status: 'running' | 'done' | 'error';
}

export interface IterationMeta {
  id: string;
  iteration?: number;
  stage?: string;
  mode?: 'parallel' | 'serial';
  startedAt?: string;
  completedAt?: string;
  wallTimeSecs?: number;
  plan?: { status: string; durationSecs?: number };
  refactor?: { status: string; durationSecs?: number };
  prover?: { status: string; durationSecs?: number };
  review?: { status: string; durationSecs?: number };
  provers?: Record<string, ProverMeta>;
  proverFiles?: { slug: string; size: number }[];
}

// --- Snapshot types ---
export interface SnapshotProverSummary {
  slug: string;
  file?: string;
  stepCount: number;
  hasBaseline: boolean;
}

export interface SnapshotFileInfo {
  name: string;
  size: number;
  modified: string;
}

export interface SnapshotFileContent {
  name: string;
  content: string;
}

export interface DiffResult {
  step: number;
  fromFile: string;
  toFile: string;
  diff: string;
  addedLines: number;
  removedLines: number;
}

// --- Cross-iteration file snapshot types ---
export interface FileSnapshotSummary {
  slug: string;
  file?: string;
  iterations: { id: string; stepCount: number; hasBaseline: boolean }[];
  totalSteps: number;
}

export interface TimelineEntry {
  iteration: string;
  step: number;           // 0 = baseline
  file: string;           // baseline.lean / step-001.lean
  ts?: string;            // timestamp from code_snapshot event
  proverLog?: string;     // slug for log cross-reference
  sourceFile?: string;    // actual edited file recorded by code_snapshot event
  synthetic?: boolean;    // content came from git / empty placeholder, not a real prover snapshot
  diff?: string;
  addedLines?: number;
  removedLines?: number;
}