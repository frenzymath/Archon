import fs from 'fs';
import type { LogEntry } from './types.js';

/** Read a file or return fallback on error */
export function readFileOr(filePath: string, fallback: string): string {
  try { return fs.readFileSync(filePath, 'utf-8'); } catch { return fallback; }
}

/** Parse a .jsonl file into typed entries */
export function parseJsonl(filePath: string): LogEntry[] {
  const content = readFileOr(filePath, '');
  if (!content) return [];
  return content.split('\n').filter(Boolean).map(line => {
    try { return JSON.parse(line) as LogEntry; } catch { return null; }
  }).filter((e): e is LogEntry => e !== null);
}

/** Read just the first JSONL line's ``ts`` field — used to anchor a
 *  log file in chronological order without parsing the whole stream.
 *  Reads a small head buffer so the cost stays O(1) per file even for
 *  long-running JSONLs.  Returns ``null`` on any read/parse failure;
 *  callers fall back to mtime in that case. */
export function readFirstJsonlTs(filePath: string): string | null {
  try {
    const fd = fs.openSync(filePath, 'r');
    try {
      const buf = Buffer.alloc(2048);
      const n = fs.readSync(fd, buf, 0, 2048, 0);
      if (n <= 0) return null;
      const text = buf.subarray(0, n).toString('utf8');
      const eol = text.indexOf('\n');
      const line = eol >= 0 ? text.slice(0, eol) : text;
      const obj = JSON.parse(line);
      return typeof obj?.ts === 'string' ? obj.ts : null;
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}
