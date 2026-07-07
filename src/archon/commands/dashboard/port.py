"""TCP port probing for the dashboard server.

`port_in_use` mirrors the Node server's bind sequence so the Python
side predicts what the server will actually see. `wait_for_http`
identifies *our* server (by reading `/api/project` and matching the
`archonPath`) so an unrelated process on the same port can't trick us.
"""

from __future__ import annotations

import errno
import json
import socket
import time
import urllib.error
import urllib.request


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def port_in_use(port: int) -> bool:
    """Return True if the dashboard server can NOT bind to `port`.

    Mirrors the server's bind sequence in ``server/src/index.ts``: try
    IPv6 dual-stack ``::`` first, fall back to IPv4 ``0.0.0.0`` only on
    EAFNOSUPPORT/EADDRNOTAVAIL. SO_REUSEADDR matches libuv's defaults
    so we predict what the Node bind will actually see.

    A bind-test is the only reliable check for "is this port usable by
    the server". Connect-based probes give false "free" results for
    ports bound to a specific interface or stuck in TIME_WAIT without
    SO_REUSEADDR, and they can block on filtered ports — turning
    `find_free_port` into a multi-second stall. ``bind`` returns
    immediately in every case.
    """
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass  # platform may not allow toggling V6ONLY at runtime
            s.bind(("::", port))
            return False
        finally:
            s.close()
    except OSError as e:
        # IPv6 disabled on this host — fall through to v4-only test, the
        # same fallback path the server uses. Any other error
        # (EADDRINUSE, EACCES, ...) means the server can't bind here either.
        if e.errno not in (
            errno.EAFNOSUPPORT,
            errno.EADDRNOTAVAIL,
            errno.EPROTONOSUPPORT,
        ):
            return True

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return False
        finally:
            s.close()
    except OSError:
        return True


def find_free_port(start: int, attempts: int = 10) -> int | None:
    """Find a free port starting from `start + 1`."""
    for p in range(start + 1, start + 1 + attempts):
        if not port_in_use(p):
            return p
    return None


def wait_for_http(port: int, expected_archon_path: str, timeout: float = 4.0) -> bool:
    """Return True once OUR archon UI is answering HTTP on `port`.

    Critical: it's not enough to see *any* 200 response — anything else
    on port 8080 (a corporate proxy, a leftover dev server, an OpenAPI
    explorer that blanket-200s on `/api/*`) would falsely confirm our
    dashboard is live, the URL gets printed, and the user opens a
    broken page. Instead we hit ``/api/project`` and require the
    response body to parse as JSON containing ``archonPath`` matching
    the project we spawned the server for. Only our server can answer
    that correctly, so this rules out false positives end-to-end.
    """
    url = f"http://127.0.0.1:{port}/api/project"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _NO_PROXY_OPENER.open(url, timeout=0.5) as resp:
                if resp.status == 200:
                    try:
                        data = json.loads(resp.read().decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        data = None
                    if (isinstance(data, dict)
                            and data.get("archonPath") == expected_archon_path):
                        return True
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            ConnectionError,
            OSError,
            TimeoutError,
        ):
            pass
        time.sleep(0.15)
    return False
