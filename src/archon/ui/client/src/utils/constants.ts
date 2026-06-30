/** Derive the base URL so fetch() works behind reverse proxies with a path prefix. */
function getBaseUrl(): string {
  // import.meta.env.BASE_URL is './' when vite base is './'
  const base = import.meta.env.BASE_URL ?? '/';
  if (base === './' || base === '.') {
    // Resolve relative to current page location
    let loc = window.location.pathname;

    // Detect common reverse-proxy patterns like /proxy/<port>/
    // In these setups the proxy forwards the full path to the backend,
    // but the backend routes are registered at the root, so we need to
    // use the proxy prefix as our base so the browser sends the right
    // path back through the proxy.
    const proxyMatch = loc.match(/^(.*\/proxy\/\d+\/)/);
    if (proxyMatch) {
      return proxyMatch[1];
    }

    // Strip filename (e.g. index.html) to get directory
    if (loc.includes('.') && !loc.endsWith('/')) {
      loc = loc.substring(0, loc.lastIndexOf('/') + 1);
    }
    // Ensure trailing slash
    return loc.endsWith('/') ? loc : loc + '/';
  }
  return base.endsWith('/') ? base : base + '/';
}

export const BASE_URL = getBaseUrl();

/** Build an API URL that works behind reverse proxies. Input: '/api/foo' → '<base>api/foo' */
export function apiUrl(path: string): string {
  // Strip leading slash from path since BASE_URL already ends with /
  const rel = path.startsWith('/') ? path.slice(1) : path;
  return BASE_URL + rel;
}

/** Build a WebSocket URL that works behind reverse proxies. */
export function wsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const rel = path.startsWith('/') ? path.slice(1) : path;
  return `${proto}//${location.host}${BASE_URL}${rel}`;
}

/** Shared constants */
export const STATUS_COLORS: Record<string, string> = {
  solved: 'var(--green)',
  partial: 'var(--orange)',
  blocked: 'var(--red)',
  failed_retry: 'var(--red)',
  not_started: 'var(--text-muted)',
  pending: 'var(--orange)',
  'in-progress': 'var(--blue)',
  done: 'var(--green)',
};
