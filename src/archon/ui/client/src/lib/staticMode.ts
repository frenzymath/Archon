declare global {
  interface Window {
    __ARCHON_STATIC__?: {
      generatedAt?: string;
      projectPath?: string;
      endpointCount?: number;
      /** Set when the static export was built from a scope (archon scope dashboard --static-build). */
      scopePath?: string;
      scopeMembers?: { name?: string; path?: string; has_dag?: boolean }[];
    };
  }
}

async function apiKey(path: string): Promise<string> {
  // SHA-256 hex — must match Python's hashlib.sha256(...).hexdigest() so the
  // generated JSON files line up. crypto.subtle.digest is available in every
  // modern browser under both http://localhost and the https:// Pages origin.
  const bytes = new TextEncoder().encode(path);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const view = new Uint8Array(digest);
  let hex = '';
  for (const b of view) hex += b.toString(16).padStart(2, '0');
  return hex;
}

function unscopedDefaultProjectPath(path: string): string | null {
  const cfg = window.__ARCHON_STATIC__;
  if (!cfg?.scopePath || !cfg.projectPath) return null;
  try {
    const url = new URL(path, window.location.origin);
    if (url.searchParams.get('project') !== cfg.projectPath) return null;
    url.searchParams.delete('project');
    const search = url.searchParams.toString();
    return `${url.pathname}${search ? `?${search}` : ''}`;
  } catch {
    return null;
  }
}

function apiPath(input: RequestInfo | URL): string | null {
  if (typeof input === 'string') {
    if (input.startsWith('/api/')) return input;
    try {
      const url = new URL(input, window.location.href);
      if (url.origin === window.location.origin && url.pathname.startsWith('/api/')) {
        return `${url.pathname}${url.search}`;
      }
    } catch {
      return null;
    }
    return null;
  }
  if (input instanceof URL) {
    if (input.origin === window.location.origin && input.pathname.startsWith('/api/')) {
      return `${input.pathname}${input.search}`;
    }
    return null;
  }
  if (input instanceof Request) {
    try {
      const url = new URL(input.url);
      if (url.origin === window.location.origin && url.pathname.startsWith('/api/')) {
        return `${url.pathname}${url.search}`;
      }
    } catch {
      return null;
    }
  }
  return null;
}

export function isStaticDashboard(): boolean {
  return !!window.__ARCHON_STATIC__;
}

export function installStaticFetch(): void {
  if (!isStaticDashboard()) return;
  const orig = window.fetch.bind(window);
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = apiPath(input);
    if (!path) return orig(input, init);
    const key = await apiKey(path);
    const res = await orig(`./data/api/${key}.json`, init);
    if (res.ok) return res;
    const fallback = unscopedDefaultProjectPath(path);
    if (!fallback) return res;
    const fallbackKey = await apiKey(fallback);
    return orig(`./data/api/${fallbackKey}.json`, init);
  }) as typeof window.fetch;
}

export {};
