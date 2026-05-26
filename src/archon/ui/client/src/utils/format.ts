/** Shared formatting utilities */

export function fmtDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${s % 60}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

export function fmtTokens(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

export function fmtTime(ts: string): string {
  try {
    // Treat bare ISO timestamps (no Z or offset) as UTC to match shell-side `date -u`
    const normalized = /\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(ts) ? ts + 'Z' : ts;
    return new Date(normalized).toLocaleTimeString('en-GB', { hour12: false });
  } catch { return ts; }
}

export function truncate(s: string, max: number): { text: string; truncated: boolean } {
  if (s.length <= max) return { text: s, truncated: false };
  return { text: s.slice(0, max), truncated: true };
}

/** Hard-truncate to `max` chars + `…`. Use for displays where CSS
 *  ellipsis can't be relied on (narrow sidebars, flex containers
 *  whose width chain doesn't propagate, etc.). */
export function truncateSubject(s: string | undefined | null, max: number): string {
  if (!s) return '';
  if (s.length <= max) return s;
  return s.slice(0, max - 1) + '…';
}

/** Pick the dominant model from a `model_usage` map. Claude Code emits a
 *  fixed-size haiku sidecar call on every session (~$0.02) regardless of
 *  `--model`; picking by `Object.keys()[0]` mislabels every opus/sonnet
 *  session as haiku (alphabetical order). Rank by cost, then output
 *  tokens, then name as final tiebreak. Strip the `claude-` prefix and
 *  any `-YYYYMMDD` date suffix for display. */
export function primaryModel(
  modelUsage: Record<string, { inputTokens?: number; outputTokens?: number; costUSD?: number }> | undefined,
): string {
  if (!modelUsage) return '';
  const entries = Object.entries(modelUsage);
  if (!entries.length) return '';
  entries.sort(([an, a], [bn, b]) => {
    const ac = a.costUSD ?? 0, bc = b.costUSD ?? 0;
    if (bc !== ac) return bc - ac;
    const ao = a.outputTokens ?? 0, bo = b.outputTokens ?? 0;
    if (bo !== ao) return bo - ao;
    return an.localeCompare(bn);
  });
  return entries[0][0].replace(/^claude-/, '').replace(/-\d{8}$/, '');
}
