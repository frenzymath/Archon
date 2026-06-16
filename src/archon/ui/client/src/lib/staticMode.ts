declare global {
  interface Window {
    __ARCHON_STATIC__?: {
      generatedAt?: string;
      projectPath?: string;
      endpointCount?: number;
    };
  }
}

function apiKey(path: string): string {
  const bytes = new TextEncoder().encode(path);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
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
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const path = apiPath(input);
    if (!path) return orig(input, init);
    return orig(`./data/api/${apiKey(path)}.json`, init);
  }) as typeof window.fetch;
}

export {};
