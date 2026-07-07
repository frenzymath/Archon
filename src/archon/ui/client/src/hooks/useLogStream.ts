/**
 * useLogStream — manages log entry loading + real-time streaming for a selected file.
 *
 * Strategy:
 *   1. Immediately fetch full log via REST
 *   2. Open WebSocket for incremental updates
 *   3. If WS fails or disconnects, fall back to REST polling (3s)
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import type { LogEntry } from '../types';
import { getProjectScope } from '../lib/projectScope';
import { isStaticDashboard } from '../lib/staticMode';
import { wsUrl } from '../utils/constants';

const POLL_INTERVAL = 3000;

interface UseLogStreamResult {
  entries: LogEntry[];
  streaming: boolean;
}

export function useLogStream(selectedFile: string): UseLogStreamResult {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [streaming, setStreaming] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const entriesRef = useRef<LogEntry[]>([]);

  // Keep ref in sync for use in callbacks
  const updateEntries = useCallback((newEntries: LogEntry[]) => {
    entriesRef.current = newEntries;
    setEntries(newEntries);
  }, []);

  const appendEntries = useCallback((incoming: LogEntry[]) => {
    const updated = [...entriesRef.current, ...incoming];
    entriesRef.current = updated;
    setEntries([...updated]);
  }, []);

  useEffect(() => {
    if (!selectedFile) {
      updateEntries([]);
      setStreaming(false);
      return;
    }

    updateEntries([]);
    setStreaming(false);

    let wsConnected = false;
    let wsReady = false;
    let cancelled = false;

    // --- REST fetch (initial + poll fallback) ---
    function fetchLog() {
      return fetch(`/api/logs/${selectedFile}`)
        .then(r => r.json())
        .then((logs: LogEntry[]) => {
          if (cancelled) return;
          updateEntries(logs);
          return logs.length;
        })
        .catch(() => {
          return -1;
        });
    }

    function startPolling() {
      if (isStaticDashboard()) return;
      if (pollRef.current) return;
      pollRef.current = setInterval(async () => {
        if (cancelled) return;
        const count = await fetchLog();
        if (count !== undefined) {
        }
      }, POLL_INTERVAL);
    }

    if (isStaticDashboard()) {
      fetchLog();
      return () => {
        cancelled = true;
      };
    }

    // --- WebSocket ---
    // Scope the stream to the selected peer project. wsUrl() applies the
    // reverse-proxy base so streaming works behind a path-prefix proxy.
    const scope = getProjectScope();
    const scopeQ = scope ? `?project=${encodeURIComponent(scope)}` : '';
    const ws = new WebSocket(wsUrl(`/api/log-stream/${selectedFile}${scopeQ}`));
    wsRef.current = ws;

    ws.onopen = () => {
      if (cancelled) return;
      wsConnected = true;
    };

    ws.onmessage = (ev) => {
      if (cancelled) return;
      try {
        const raw = JSON.parse(ev.data);

        // Server sends {type: 'ready'} to signal watcher is active
        if (raw.type === 'ready') {
          wsReady = true;
          setStreaming(true);
          // Load initial entries now that WS is watching
          fetchLog();
          return;
        }

        if (raw.type === 'error') {
          return;
        }

        // Incremental entry
        if (raw.ts) {
          appendEntries([raw as LogEntry]);
        }
      } catch { /* skip non-JSON */ }
    };

    ws.onclose = () => {
      if (cancelled) return;
      setStreaming(false);
      if (!wsConnected || !wsReady) startPolling();
    };

    ws.onerror = () => {
      if (cancelled) return;
      setStreaming(false);
      startPolling();
    };

    // Also fetch immediately via REST (don't wait for WS handshake)
    fetchLog();

    return () => {
      cancelled = true;
      ws.close();
      wsRef.current = null;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [selectedFile, updateEntries, appendEntries]);

  return { entries, streaming };
}
