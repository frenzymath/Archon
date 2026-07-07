"""Static dashboard exporter.

The live dashboard is a React client backed by a local Fastify API.  GitHub
Pages can only serve files, so static export snapshots the API responses the
client needs and installs a tiny runtime fetch adapter that reads those JSON
files instead of calling ``/api/*``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from importlib import resources
from pathlib import Path
from typing import Any

import typer

from archon import log

from .build import build_client, check_node, install_if_needed
from .pidfile import PidFileRegistry
from .port import find_free_port
from .server import ServerProcess


MARKER = ".archon-static-dashboard"
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PAGES_WORKFLOW = "archon-dashboard-pages.yml"

# Endpoints that are *project-scoped*: the live server reads them through the
# ?project=<path> query, so for a multi-project scope they have to be snapshotted
# once per member.
PER_PROJECT_ENDPOINTS = (
    "/api/project",
    "/api/progress",
    "/api/strategy",
    "/api/tasks",
    "/api/sorry-count",
    "/api/task-results",
    "/api/summary",
    "/api/journal/sessions",
    "/api/journal/all-milestones",
    "/api/journal/status",
    "/api/to-user",
    "/api/git/log",
    "/api/git/head",
    "/api/dag",
    "/api/dag/last-modified",
    "/api/blueprint/chapters",
    "/api/multilane",
    "/api/source/files",
)

# Endpoints that describe scope-level shape (not a member) — fetched once.
SCOPE_LEVEL_ENDPOINTS = (
    "/api/scope",
    "/api/peer-projects",
)


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _api_key(endpoint: str) -> str:
    # SHA-256 hex (64 chars) — fixed width keeps filenames well under macOS's
    # 255-byte limit even for deeply nested scope member paths.
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def _quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/._-")


def _js_encode_uri_component(s: str) -> str:
    # Match JS encodeURIComponent so the URL we snapshot under is byte-identical
    # to the URL the client builds. encodeURIComponent leaves A-Z a-z 0-9 plus
    # ``-_.!~*'()`` unencoded and percent-encodes everything else (including ``/``).
    return urllib.parse.quote(s, safe="-_.!~*'()")


def _scoped(endpoint: str, project_path: str) -> str:
    """Append ``?project=<encoded path>`` to an endpoint, matching the client."""
    sep = "&" if "?" in endpoint else "?"
    return f"{endpoint}{sep}project={_js_encode_uri_component(project_path)}"


def _project_from_query(query: str) -> str | None:
    if not query:
        return None
    for part in query.split("&"):
        k, _, v = part.partition("=")
        if k == "project":
            return urllib.parse.unquote(v)
    return None


def _fetch_json(base_url: str, endpoint: str, *, timeout: float = 90.0) -> Any | None:
    url = f"{base_url}{endpoint}"
    try:
        with _NO_PROXY_OPENER.open(url, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _write_json(out_dir: Path, endpoint: str, data: Any) -> None:
    target = out_dir / "data" / "api" / f"{_api_key(endpoint)}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class StaticDashboardExporter:
    def __init__(
        self,
        project_path: Path,
        *,
        out_dir: Path,
        port: int = 8090,
        force: bool = False,
        members: list[dict] | None = None,
        scope_path: Path | None = None,
    ) -> None:
        self.project_path = project_path.resolve()
        self.out_dir = out_dir.resolve()
        self.port = port
        self.force = force
        self.endpoints: set[str] = set()
        self.saved = 0
        # When set, the exporter fans every project-scoped endpoint out across
        # all members so the project switcher works in the deployed Pages site.
        self.members = members or []
        self.scope_path = scope_path.resolve() if scope_path else None
        # save_url → fetch_url override. When fanning out per-member we want
        # the live server hit with the absolute path it can resolve, but we
        # want to *save* the snapshot under an aliased URL so the file name
        # (and any URL the client builds later) carries no local-machine paths.
        self.fetch_for: dict[str, str] = {}
        # path → alias map: replaces absolute paths leaking through response
        # JSON (e.g. /api/scope.scopePath, /api/peer-projects.current.path).
        # Member names are unique within a scope, so they're a safe stable ID.
        self.path_alias: dict[str, str] = self._build_path_alias()
        # Pre-sorted longest-first for prefix matching in _redact_str.
        self._alias_by_length: list[tuple[str, str]] = sorted(
            self.path_alias.items(), key=lambda kv: -len(kv[0])
        )

    def _build_path_alias(self) -> dict[str, str]:
        alias: dict[str, str] = {}
        host_alias = self._public_path_alias(self.project_path)
        alias[str(self.project_path)] = host_alias
        if self.scope_path is not None:
            alias[str(self.scope_path)] = self.scope_path.name
        for m in self.members:
            if not isinstance(m, dict):
                continue
            mp = m.get("path")
            if mp:
                alias[str(Path(mp).resolve())] = self._public_path_alias(Path(mp))
        return alias

    def _public_path_alias(self, path: Path) -> str:
        resolved = path.resolve()
        if self.scope_path is not None:
            try:
                return resolved.relative_to(self.scope_path).as_posix()
            except ValueError:
                pass
        return resolved.name

    def _alias_for(self, abs_path: str) -> str:
        """Return the public ID for ``abs_path`` (scope-relative, or basename)."""
        resolved = str(Path(abs_path).resolve())
        return self.path_alias.get(resolved) or Path(abs_path).name

    def _redact_str(self, s: str) -> str:
        # Match longest known prefix first so /Users/foo/AB doesn't get
        # truncated by an alias registered for /Users/foo/A.
        for abs_path, alias in self._alias_by_length:
            if s == abs_path:
                return alias
            if s.startswith(abs_path + "/") or s.startswith(abs_path + os.sep):
                return alias + s[len(abs_path):]
        return s

    def _redact(self, value: Any) -> Any:
        """Recursively replace any absolute project path (or path prefix)
        with its alias so nothing about the build machine's filesystem
        layout leaks into the snapshot."""
        if isinstance(value, str):
            return self._redact_str(value)
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact(v) for v in value]
        return value

    def run(self) -> None:
        archon_dir = self.project_path / ".archon"
        if not archon_dir.is_dir():
            log.error(f"No .archon/ directory found in {self.project_path}")
            log.step("Run: archon init first, or check the project path")
            raise typer.Exit(1)

        ui_dir = _data_path("ui")
        server_dir = ui_dir / "server"
        client_dir = ui_dir / "client"
        if not ui_dir.exists():
            log.error("UI files not found in package data — installation may be incomplete")
            raise typer.Exit(1)

        self._prepare_output_dir()

        check_node()
        install_if_needed(server_dir, "server")
        install_if_needed(client_dir, "client")

        with tempfile.TemporaryDirectory(prefix="archon-dashboard-static-") as td:
            client_build = Path(td) / "client"
            log.step("Building static dashboard client...")
            build_client(client_dir, out_dir=client_build, base="./")
            shutil.copytree(client_build, self.out_dir, dirs_exist_ok=True)

        server = self._start_api_server(server_dir, archon_dir)
        if self.members:
            log.step(f"Snapshotting {len(self.members)} member project(s) in scope")
        try:
            self._snapshot_api(f"http://127.0.0.1:{server.port}")
        finally:
            server.cleanup()

        self._write_static_config()
        self._ensure_pages_workflow()
        (self.out_dir / MARKER).write_text(
            "Generated by archon dashboard --static-build\n",
            encoding="utf-8",
        )
        log.success(f"Static dashboard written to {self.out_dir}")
        log.info(f"Snapshotted {self.saved} API endpoints")
        log.step("GitHub Pages workflow is ready. In Settings → Pages, select GitHub Actions.")

    def _prepare_output_dir(self) -> None:
        if not self.out_dir.exists():
            self.out_dir.mkdir(parents=True)
            return
        entries = list(self.out_dir.iterdir())
        if entries and not (self.out_dir / MARKER).exists() and not self.force:
            log.error(f"Output directory is not empty: {self.out_dir}")
            log.step("Pass --force to replace it, or choose a different --out directory.")
            raise typer.Exit(1)
        shutil.rmtree(self.out_dir)
        self.out_dir.mkdir(parents=True)

    def _start_api_server(self, server_dir: Path, archon_dir: Path) -> ServerProcess:
        requested = self.port
        free = find_free_port(requested) or requested
        registry = PidFileRegistry(self.out_dir / ".tmp-ui", self.project_path)
        server = ServerProcess(
            server_dir=server_dir,
            project_path=self.project_path,
            archon_dir=archon_dir,
            registry=registry,
        )
        port = server.start_with_retry(free)
        log.step(f"Snapshotting dashboard API on http://127.0.0.1:{port}")
        return server

    def _enqueue(
        self,
        queue: deque[str],
        save_url: str,
        fetch_url: str | None = None,
    ) -> None:
        if save_url in self.endpoints:
            return
        self.endpoints.add(save_url)
        if fetch_url is not None and fetch_url != save_url:
            self.fetch_for[save_url] = fetch_url
        queue.append(save_url)

    def _snapshot_api(self, base_url: str) -> None:
        queue: deque[str] = deque()
        for endpoint in PER_PROJECT_ENDPOINTS:
            self._enqueue(queue, endpoint)
        for endpoint in SCOPE_LEVEL_ENDPOINTS:
            self._enqueue(queue, endpoint)
        # Scope mode: fan out per-member so the project switcher and Meta
        # union view resolve to real snapshots instead of 404s. The *save*
        # URL uses the member alias (no local path leaks into the file
        # name); the *fetch* URL uses the absolute path the live server
        # needs to resolve the project.
        for member in self.members:
            mpath = member.get("path") if isinstance(member, dict) else None
            if not mpath:
                continue
            alias = self._alias_for(mpath)
            for endpoint in PER_PROJECT_ENDPOINTS:
                self._enqueue(
                    queue,
                    _scoped(endpoint, alias),
                    fetch_url=_scoped(endpoint, mpath),
                )

        while queue:
            save_url = queue.popleft()
            fetch_url = self.fetch_for.get(save_url, save_url)
            timeout = 300.0 if self._slow_endpoint(save_url) else 90.0
            data = self._cached_endpoint(save_url)
            if data is None:
                data = _fetch_json(base_url, fetch_url, timeout=timeout)
            if data is None:
                if self._important_endpoint(save_url):
                    log.warn(f"Static export skipped slow or unavailable endpoint: {save_url}")
                continue
            data = self._redact(data)
            _write_json(self.out_dir, save_url, data)
            self.saved += 1
            self._expand_endpoint(queue, save_url, fetch_url, data)

    def _cached_endpoint(self, save_url: str) -> Any | None:
        # The cache lookup uses the *real* (absolute) project path that the
        # server would resolve to, not the aliased ?project= that's encoded
        # in the save_url. Fall through fetch_for if an alias was set.
        fetch_url = self.fetch_for.get(save_url, save_url)
        path, _, query = fetch_url.partition("?")
        if path == "/api/dag":
            scoped = _project_from_query(query)
            project = Path(scoped) if scoped else self.project_path
            cache = project / ".leandag" / "dag.json"
            if not cache.exists():
                return None
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return {
                "nodes": raw.get("nodes") or [],
                "edges": raw.get("edges") or [],
                "meta": raw.get("meta") or {},
                "error": None,
            }
        if path == "/api/dag/last-modified":
            return {"files": {}}
        return None

    def _slow_endpoint(self, endpoint: str) -> bool:
        path = endpoint.split("?", 1)[0]
        return (
            path == "/api/dag"
            or path.startswith("/api/proofgraph/")
            or path.startswith("/api/snapshot-files/")
            or "/snapshots/" in path
        )

    def _important_endpoint(self, endpoint: str) -> bool:
        path = endpoint.split("?", 1)[0]
        return path in {
            "/api/project",
            "/api/progress",
            "/api/dag",
            "/api/blueprint/chapters",
            "/api/git/log",
            "/api/git/head",
        }

    def _expand_endpoint(
        self,
        queue: deque[str],
        save_url: str,
        fetch_url: str,
        data: Any,
    ) -> None:
        save_path, _, save_query = save_url.partition("?")
        _, _, fetch_query = fetch_url.partition("?")
        save_project = _project_from_query(save_query)
        fetch_project = _project_from_query(fetch_query)

        if save_path == "/api/journal/sessions" and isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    sid = _quote_path(item["id"])
                    for tail in ("milestones", "summary", "recommendations"):
                        sub_base = f"/api/journal/sessions/{sid}/{tail}"
                        save_sub = (
                            _scoped(sub_base, save_project) if save_project else sub_base
                        )
                        fetch_sub = (
                            _scoped(sub_base, fetch_project) if fetch_project else sub_base
                        )
                        self._enqueue(queue, save_sub, fetch_url=fetch_sub)
            return

        if save_path == "/api/source/files" and isinstance(data, dict):
            for entry in data.get("files") or []:
                if not isinstance(entry, dict):
                    continue
                fpath = entry.get("path")
                if not isinstance(fpath, str) or not fpath:
                    continue
                encoded = _js_encode_uri_component(fpath)
                sub_base = f"/api/source/file?path={encoded}"
                save_sub = (
                    _scoped(sub_base, save_project) if save_project else sub_base
                )
                fetch_sub = (
                    _scoped(sub_base, fetch_project) if fetch_project else sub_base
                )
                self._enqueue(queue, save_sub, fetch_url=fetch_sub)
            return

    def _ensure_pages_workflow(self) -> None:
        # Workflow lives in whichever repo will be deployed: the scope repo
        # (scope mode) or the project repo (single-project mode).
        repo_root = self.scope_path or self.project_path
        workflows_dir = repo_root / ".github" / "workflows"
        workflow_path = workflows_dir / PAGES_WORKFLOW
        if workflow_path.exists():
            log.info(f"GitHub Pages workflow already exists: {workflow_path}")
            return

        try:
            upload_path = self.out_dir.relative_to(repo_root).as_posix()
        except ValueError:
            log.warn(
                "Static output is outside the repository root; skipping GitHub Pages workflow generation."
            )
            return

        workflows_dir.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(_pages_workflow(upload_path), encoding="utf-8")
        log.success(f"GitHub Pages workflow written to {workflow_path}")

    def _write_static_config(self) -> None:
        # Path-shaped fields here would otherwise embed the build machine's
        # absolute paths into the deployed Pages site. Use the public alias
        # (member name / scope basename) so the snapshot is portable.
        config: dict[str, Any] = {
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "projectPath": self._alias_for(str(self.project_path)),
            "endpointCount": self.saved,
        }
        if self.scope_path is not None:
            config["scopePath"] = self._alias_for(str(self.scope_path))
            # The client uses this to know it should re-enable the project
            # switcher and ?project= URL rewriting in static mode.
            config["scopeMembers"] = [
                {
                    "name": m.get("name"),
                    "path": self._alias_for(m["path"]),
                    "has_dag": m.get("has_dag"),
                }
                for m in self.members if isinstance(m, dict) and m.get("path")
            ]
        script = "window.__ARCHON_STATIC__ = " + json.dumps(config, ensure_ascii=False) + ";\n"
        (self.out_dir / "archon-static-config.js").write_text(script, encoding="utf-8")

        index = self.out_dir / "index.html"
        html = index.read_text(encoding="utf-8")
        tag = '<script src="./archon-static-config.js"></script>'
        if tag not in html:
            html = html.replace('<script type="module"', f'{tag}\n    <script type="module"', 1)
        index.write_text(html, encoding="utf-8")


def _pages_workflow(upload_path: str) -> str:
    return f"""name: Deploy Archon dashboard

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{{{ steps.deployment.outputs.page_url }}}}

    steps:
      - name: Checkout
        uses: actions/checkout@v6
        with:
          submodules: false

      - name: Configure Pages
        uses: actions/configure-pages@v5

      - name: Upload dashboard
        uses: actions/upload-pages-artifact@v4
        with:
          path: {json.dumps(upload_path)}

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
"""
