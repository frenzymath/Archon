/**
 * Project scope — which project the dashboard is currently viewing.
 *
 * The server is launched bound to one project but can serve any *peer* it
 * declares (`.archon/peers.yaml`) when a request carries `?project=<path>`.
 * The selected peer is remembered in localStorage; a one-time `fetch` wrapper
 * (installed at app start) appends `?project=` to every `/api/*` call, so all
 * existing hooks switch with no per-call changes. Selecting "this project"
 * clears the scope.
 *
 * Static mode (GitHub Pages export) participates too when the snapshot was
 * generated for a *scope*: `__ARCHON_STATIC__.scopePath` is set, and per-member
 * API responses are pre-snapshotted under URLs that carry `?project=<path>`.
 * The fetch wrapper here appends the scope, and the static-fetch wrapper in
 * `staticMode.ts` then resolves the resulting URL to the matching JSON file.
 */
const KEY = 'archon.projectScope';

function staticDashboard(): boolean {
  return !!window.__ARCHON_STATIC__;
}

/** True when the deployed static dashboard was built from a scope. */
export function isStaticScope(): boolean {
  return !!window.__ARCHON_STATIC__?.scopePath;
}

/** Scope switching is wired whenever the runtime can serve per-member data:
 *  live server (not static), or a static export that was built for a scope. */
function scopeSwitchingEnabled(): boolean {
  return !staticDashboard() || isStaticScope();
}

export function getProjectScope(): string | null {
  if (!scopeSwitchingEnabled()) return null;
  try {
    const stored = localStorage.getItem(KEY);
    if (stored) {
      if (!isStaticScope()) return stored;
      const members = window.__ARCHON_STATIC__?.scopeMembers ?? [];
      if (members.some((m) => m.path === stored)) return stored;
    }
  } catch {
    /* fall through to the static default */
  }
  // Static scope exports snapshot member APIs under ?project=<public alias>.
  // Treat the exported host project as the selected project from first load so
  // Overview/DAG/Blueprint hit the same scoped URLs as the project switcher.
  if (isStaticScope()) return window.__ARCHON_STATIC__?.projectPath || null;
  return null;
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
  if (!scopeSwitchingEnabled()) return;
  const orig = window.fetch.bind(window);
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (
      typeof input === 'string' &&
      input.startsWith('/api/') &&
      !input.startsWith('/api/peer-projects') &&
      !input.startsWith('/api/scope')
    ) {
      return orig(withProjectScope(input), init);
    }
    return orig(input, init);
  }) as typeof window.fetch;
}
