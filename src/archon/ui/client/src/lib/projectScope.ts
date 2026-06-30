/**
 * Project scope — which project the dashboard is currently viewing.
 *
 * The server is launched bound to one project but can serve any *peer* it
 * declares (`.archon/peers.yaml`) when a request carries `?project=<path>`.
 * The selected peer is remembered in localStorage; a one-time `fetch` wrapper
 * (installed at app start) appends `?project=` to every `/api/*` call, so all
 * existing hooks switch with no per-call changes. Selecting "this project"
 * clears the scope.
 */
import { apiUrl } from '../utils/constants';

const KEY = 'archon.projectScope';

export function getProjectScope(): string | null {
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
}

export function setProjectScope(path: string | null): void {
  try {
    if (path) localStorage.setItem(KEY, path);
    else localStorage.removeItem(KEY);
  } catch {
    /* ignore (private mode etc.) */
  }
}

/** Append `?project=<scope>` to an /api URL when a peer project is selected.
 *  A URL that already carries an explicit `project=` (e.g. the Meta view
 *  fetching a specific project's DAG) is left untouched. */
export function withProjectScope(url: string): string {
  if (/[?&]project=/.test(url)) return url;
  const scope = getProjectScope();
  if (!scope) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}project=${encodeURIComponent(scope)}`;
}

/**
 * Wrap `window.fetch` once so same-origin `/api/*` requests are scoped to the
 * selected peer. `/api/peer-projects` is left unscoped — the switcher always
 * lists the base project's peers regardless of which project is being viewed.
 */
export function installFetchScope(): void {
  const orig = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input === 'string' && input.startsWith('/api/')) {
      // /api/peer-projects is never scoped (the switcher always lists the base
      // project's peers); everything else is scoped to the selected peer.
      const scoped = input.startsWith('/api/peer-projects') ? input : withProjectScope(input);
      // Prefix the reverse-proxy base so /api/* works under a path prefix.
      return orig(apiUrl(scoped), init);
    }
    return orig(input, init);
  }) as typeof window.fetch;
}
