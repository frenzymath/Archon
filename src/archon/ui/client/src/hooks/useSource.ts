/**
 * Source — current state of the project's Lean files on disk.
 *
 * Backs the Code view, which lets users browse the working-tree source
 * (no playback/history). In static mode the per-file content is pre-snapshotted
 * during `--static-build` so the same hooks work without a live server.
 */
import { useQuery } from '@tanstack/react-query';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

export interface SourceFileSummary {
  path: string;
  size: number;
}

export function useSourceFiles() {
  return useQuery<{ files: SourceFileSummary[] }>({
    queryKey: ['sourceFiles'],
    queryFn: () => fetchJson('/api/source/files'),
    staleTime: 30_000,
  });
}

export function useSourceFile(filePath: string | null) {
  return useQuery<{ path: string; size: number; content: string }>({
    queryKey: ['sourceFile', filePath],
    queryFn: () => fetchJson(`/api/source/file?path=${encodeURIComponent(filePath ?? '')}`),
    enabled: !!filePath,
    staleTime: 30_000,
  });
}
