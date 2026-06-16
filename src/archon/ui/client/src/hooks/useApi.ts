import { useQuery } from '@tanstack/react-query';
import type {
  ProgressData, Task, LogEntry, AggregatedStats,
  LogsResponse, SorryCount, Milestone, IterationMeta,
} from '../types';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

// --- Core ---
export interface ScopeData {
  inScope: boolean;
  scopePath?: string;
  readme?: string;
  roadmap?: string;
  members?: { name: string; path: string; has_dag: boolean }[];
}

export function useScope() {
  return useQuery<ScopeData>({
    queryKey: ['scope'], queryFn: () => fetchJson('/api/scope'), staleTime: 30000,
  });
}

export function useProject() {
  return useQuery<{ name: string; path: string; archonPath: string }>({
    queryKey: ['project'], queryFn: () => fetchJson('/api/project'), staleTime: Infinity,
  });
}

export function useProgress() {
  return useQuery<ProgressData>({ queryKey: ['progress'], queryFn: () => fetchJson('/api/progress'), refetchInterval: 10000 });
}

export function useStrategy() {
  return useQuery<{ content: string }>({
    queryKey: ['strategy'],
    queryFn: () => fetchJson('/api/strategy'),
    refetchInterval: 30000,
  });
}

export function useTasks() {
  return useQuery<Task[]>({ queryKey: ['tasks'], queryFn: () => fetchJson('/api/tasks'), refetchInterval: 10000 });
}

export function useSummary() {
  return useQuery<AggregatedStats>({ queryKey: ['summary'], queryFn: () => fetchJson('/api/summary'), refetchInterval: 10000 });
}

export function useSorryCount() {
  return useQuery<SorryCount>({ queryKey: ['sorryCount'], queryFn: () => fetchJson('/api/sorry-count'), refetchInterval: 10000 });
}

// --- Logs ---
export function useLogs() {
  return useQuery<LogsResponse>({ queryKey: ['logs'], queryFn: () => fetchJson('/api/logs'), refetchInterval: 3000, refetchIntervalInBackground: true });
}

export function useLogContent(filename: string) {
  return useQuery<LogEntry[]>({
    queryKey: ['logContent', filename],
    queryFn: () => fetchJson(`/api/logs/${filename}`),
    enabled: !!filename,
    refetchInterval: false,
  });
}

// --- Journal ---
export interface JournalSession { id: string; hasMilestones: boolean; hasSummary: boolean; hasRecommendations: boolean }

export function useJournalSessions() {
  return useQuery<JournalSession[]>({ queryKey: ['journalSessions'], queryFn: () => fetchJson('/api/journal/sessions'), refetchInterval: 10000 });
}

export function useJournalMilestones(sessionId: string) {
  return useQuery<Milestone[]>({
    queryKey: ['journalMilestones', sessionId],
    queryFn: () => fetchJson(`/api/journal/sessions/${sessionId}/milestones`),
    enabled: !!sessionId,
  });
}

export function useJournalSummary(sessionId: string) {
  return useQuery<{ content: string }>({
    queryKey: ['journalSummary', sessionId],
    queryFn: () => fetchJson(`/api/journal/sessions/${sessionId}/summary`),
    enabled: !!sessionId,
  });
}

export function useJournalRecommendations(sessionId: string) {
  return useQuery<{ content: string }>({
    queryKey: ['journalRecs', sessionId],
    queryFn: () => fetchJson(`/api/journal/sessions/${sessionId}/recommendations`),
    enabled: !!sessionId,
  });
}

export function useJournalAllMilestones() {
  return useQuery<{ sessionId: string; milestones: Milestone[] }[]>({
    queryKey: ['journalAllMilestones'],
    queryFn: () => fetchJson('/api/journal/all-milestones'),
    refetchInterval: 10000,
  });
}

export function useProjectStatus() {
  return useQuery<{ content: string }>({ queryKey: ['projectStatus'], queryFn: () => fetchJson('/api/journal/status'), refetchInterval: 30000 });
}

// --- Iterations ---
export function useIterations() {
  return useQuery<IterationMeta[]>({ queryKey: ['iterations'], queryFn: () => fetchJson('/api/iterations'), refetchInterval: 5000 });
}

export function useIteration(id: string) {
  return useQuery<IterationMeta>({
    queryKey: ['iteration', id],
    queryFn: () => fetchJson(`/api/iterations/${id}`),
    enabled: !!id,
    refetchInterval: 5000,
  });
}

export function useIterationProver(iterationId: string, file: string) {
  return useQuery<LogEntry[]>({
    queryKey: ['iterationProver', iterationId, file],
    queryFn: () => fetchJson(`/api/iterations/${iterationId}/provers/${file}`),
    enabled: !!iterationId && !!file,
  });
}

export function useToUserAlert() {
  return useQuery({
    queryKey: ['toUserAlert'],
    queryFn: async () => {
      const res = await fetch('/api/to-user');
      if (!res.ok) return null;
      const data = await res.json();
      return data.content as string | null;
    },
    refetchInterval: 5000, 
  });
}