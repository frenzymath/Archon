"""Initialize a new Archon project.

This command does all deterministic setup in Python (lake, git, mathlib,
blueprint, workspace skeletons) and *then* invokes Claude Code for the
semantic pass only: reorganize loose reference files, fill in README.md
and references/summary.md prose, and propose initial objectives.

This split matters: anything that can fail deterministically should fail
the same way every time and be cheap to re-run. Claude should only be
spending tokens on decisions that actually need judgment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from importlib import resources
from pathlib import Path

import typer

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling import protect
from archon.commands.tooling.blueprint import Blueprint
from archon.commands.tooling.git import Git
from archon.commands.tooling.inner_git import InnerGit
from archon.commands.tooling.lake import Lake
from archon.commands.tooling.project import (
    BootstrapOptions,
    BootstrapReport,
    ProjectBootstrap,
    ProjectLayout,
    WorkspaceTemplates,
)
from archon.commands.tooling.version import ProjectVersion, warn_if_mismatch

# ── helpers ───────────────────────────────────────────────────────────


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _fail_permission(path: Path, err: Exception | None) -> None:
    """Stop init with a clear message when the project dir isn't writable."""
    log.error(f"Cannot write to project directory: {path}")
    if err is not None:
        log.step(f"  {err}")
    try:
        stat = path.stat() if path.exists() else path.parent.stat()
        try:
            import pwd
            owner = pwd.getpwuid(stat.st_uid).pw_name
        except Exception:
            owner = str(stat.st_uid)
        log.step(f"  Owner: {owner}   Mode: {oct(stat.st_mode & 0o777)}")
    except Exception:
        pass
    import getpass
    user = getpass.getuser()
    log.step("This usually means the directory was created by a different user")
    log.step("(for example, cloned with sudo) or has restrictive permissions.")
    log.step("")
    log.step("Fix it with one of the following, then re-run 'archon init':")
    log.step(f"  sudo chown -R {user}:{user} {path}")
    log.step(f"  chmod u+w {path}")
    raise typer.Exit(1)


def _parse_stage(progress_md: Path) -> str:
    if not progress_md.exists():
        return "init"
    lines = progress_md.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Current Stage"):
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return "init"


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _copy_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and overwrite:
        log.warn(f"Overwriting existing file: {dst.name}")
    if overwrite or not dst.exists():
        shutil.copy2(src, dst)


def _find_global_mcp_lean_lsp() -> list[str]:
    settings = Path.home() / ".claude" / "settings.json"
    data = _read_json(settings)
    return [
        k for k in data.get("mcpServers", {})
        if "lean" in k.lower() and "lsp" in k.lower() and "archon" not in k.lower()
    ]


def _find_global_lean4_plugins() -> list[str]:
    settings = Path.home() / ".claude" / "settings.json"
    data = _read_json(settings)
    return [
        k for k in data.get("enabledPlugins", {})
        if ("lean4" in k.lower() or "lean4-skills" in k.lower()) and "archon" not in k.lower()
    ]


# ── re-init detection ─────────────────────────────────────────────────


def _detect_existing_archon(state_dir: Path) -> dict:
    info = {
        "exists": state_dir.is_dir(),
        "has_progress": False,
        "has_prompts": False,
        "prompts_are_symlinks": False,
        "stage": "init",
        "version": "unknown",
    }
    if not info["exists"]:
        return info
    progress = state_dir / "PROGRESS.md"
    info["has_progress"] = progress.exists()
    if info["has_progress"]:
        info["stage"] = _parse_stage(progress)

    prompts_dir = state_dir / "prompts"
    if prompts_dir.is_dir():
        info["has_prompts"] = True
        md_files = list(prompts_dir.glob("*.md"))
        if md_files and any(f.is_symlink() for f in md_files):
            info["prompts_are_symlinks"] = True
            info["version"] = "legacy-symlink"
        elif md_files:
            info["version"] = "current-copy"
    return info


def _prompt_reinit_mode(info: dict) -> str:
    log.warn("This project has already been initialized with Archon.")
    log.key_value({
        "Detected layout": info["version"],
        "Current stage": info["stage"],
        "Prompts are symlinks": "yes" if info["prompts_are_symlinks"] else "no",
    })

    if info["prompts_are_symlinks"]:
        log.step(
            "Detected the legacy symlink-based layout. The new CLI copies prompts "
            "into .archon/prompts/ instead of symlinking. Re-initializing directly "
            "would break the old symlinks."
        )

    typer.echo("")
    typer.echo("How would you like to proceed?")
    typer.echo("  [k] keep       — do nothing, use the existing setup as-is")
    typer.echo("  [m] merge      — compare each file and let Claude help reconcile (recommended)")
    typer.echo("  [o] overwrite  — replace all Archon files with the bundled versions")
    typer.echo("  [a] abort      — cancel")
    typer.echo("")

    while True:
        choice = typer.prompt("Choice [k/m/o/a]", default="m").strip().lower()
        if choice in ("k", "keep"):
            return "keep"
        if choice in ("m", "merge"):
            return "merge"
        if choice in ("o", "overwrite"):
            if typer.confirm("This will overwrite local changes to .archon/prompts/ and .archon/CLAUDE.md. Continue?"):
                return "overwrite"
        if choice in ("a", "abort"):
            return "abort"


# ── merge helpers (unchanged in spirit from previous version) ─────────


def _stage_bundled_prompts(state_dir: Path) -> Path:
    staging = state_dir / ".archon-incoming"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    prompts_src = _data_path("prompts")
    if prompts_src.exists():
        prompts_stage = staging / "prompts"
        prompts_stage.mkdir(exist_ok=True)
        for f in sorted(prompts_src.glob("*.md")):
            shutil.copy2(f, prompts_stage / f.name)

    template_dir = _data_path("archon-template")
    if template_dir.exists():
        for name in ("CLAUDE.md",):
            src = template_dir / name
            if src.exists():
                shutil.copy2(src, staging / name)
    return staging


def _merge_prompts_with_claude(
    project_path: Path, state_dir: Path, staging: Path, *, model: str = DEFAULT_MODEL,
) -> None:
    log.phase(0, "Reconciling local vs. bundled Archon files")
    log.step("Launching Claude Code to walk you through the differences file by file.")

    if not _has("claude"):
        log.warn("Claude Code is not installed — falling back to a text-only diff summary.")
        _print_diff_summary(state_dir, staging)
        return

    prompt = textwrap.dedent(f"""\
        You are helping the user reconcile their existing Archon setup with the newer bundled
        versions. This is a merge session, NOT a normal init.

        Paths:
          - Existing (local, user-edited): {state_dir}
          - Incoming (bundled, new version): {staging}

        For every file under {staging} (prompts/*.md and CLAUDE.md), compare against the
        corresponding file under {state_dir}. For each file that differs, show a concise
        summary of what changed, then ask the user to choose:
          [L] keep local   [N] take new   [M] merge manually

        Rules:
          - Never edit .lean files.
          - Never touch {state_dir}/PROGRESS.md, task_pending.md, task_done.md,
            USER_HINTS.md, or REFACTOR_DIRECTIVE.md.
          - Only reconcile {state_dir}/prompts/*.md and {state_dir}/CLAUDE.md.
          - When done, delete {staging} and report: "Merged N files, kept M files."
        """)

    ClaudeAgent(model=model, role="init-merge").run_interactive(prompt, cwd=project_path)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def _print_diff_summary(state_dir: Path, staging: Path) -> None:
    log.step("Files that differ between your local setup and the bundled version:")
    differs = 0
    for incoming in staging.rglob("*"):
        if not incoming.is_file():
            continue
        rel = incoming.relative_to(staging)
        local = state_dir / rel
        if not local.exists():
            log.warn(f"  + {rel} (new in bundled version)")
            differs += 1
        elif local.read_bytes() != incoming.read_bytes():
            log.warn(f"  ~ {rel} (differs)")
            differs += 1
    if differs == 0:
        log.success("No differences — your local setup matches the bundled version.")


# ── Archon state directory setup ──────────────────────────────────────


def _step_state_dir(project_path: Path, state_dir: Path, fresh: bool) -> None:
    log.phase(1, "Setting up .archon/ state directory")

    for subdir in (
        "task_results", "logs", "prompts",
        "proof-journal/sessions", "proof-journal/current_session",
    ):
        try:
            (state_dir / subdir).mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            _fail_permission(state_dir / subdir, e)
    log.step("Created directory tree")

    template_dir = _data_path("archon-template")
    copied = 0
    preserved = 0

    user_state_files = (
        "PROGRESS.md",
        "STRATEGY.md",
        "USER_HINTS.md",
        "task_pending.md",
        "task_done.md",
        "REFACTOR_DIRECTIVE.md",
    )
    for name in user_state_files:
        src = template_dir / name
        dst = state_dir / name
        if not src.exists():
            log.warn(f"Template not found: {name}")
            continue
        if dst.exists():
            preserved += 1
            continue
        _copy_file(src, dst)
        copied += 1

    claude_src = template_dir / "CLAUDE.md"
    if claude_src.exists():
        if (state_dir / "CLAUDE.md").exists() and not fresh:
            log.warn("CLAUDE.md will be overwritten with the latest bundled version.")
        _copy_file(claude_src, state_dir / "CLAUDE.md", overwrite=True)
    else:
        log.warn("Template not found: CLAUDE.md")

    # MULTILANE.md — reference doc for setting up additional providers.
    # Always overwrite with the bundled version: it's a reference, not a
    # user-edited file.
    multilane_doc_src = template_dir / "MULTILANE.md"
    if multilane_doc_src.exists():
        _copy_file(multilane_doc_src, state_dir / "MULTILANE.md", overwrite=True)

    log.step(f"Copied {copied} new template file(s)" +
             (f", preserved {preserved} existing" if not fresh else ""))
    log.success("State directory ready")


def _step_copy_prompts(state_dir: Path, fresh: bool) -> None:
    log.phase(2, "Copying prompts")

    prompts_src = _data_path("prompts")
    prompts_dst = state_dir / "prompts"
    prompts_dst.mkdir(parents=True, exist_ok=True)

    if not prompts_src.exists():
        log.error(f"Prompts directory not found at {prompts_src}")
        return

    new = 0
    preserved = 0
    for f in sorted(prompts_src.glob("*.md")):
        dst = prompts_dst / f.name
        if fresh:
            _copy_file(f, dst, overwrite=True)
            new += 1
            continue
        if dst.exists():
            if dst.is_symlink():
                dst.unlink()
                _copy_file(f, dst, overwrite=True)
                new += 1
            else:
                preserved += 1
            continue
        _copy_file(f, dst)
        new += 1

    if fresh:
        log.success(f"Copied {new} prompt(s)")
    else:
        log.success(f"Added {new} new prompt(s), preserved {preserved} existing")


# ── deterministic bootstrap step ──────────────────────────────────────


def _step_bootstrap(project_path: Path) -> BootstrapReport:
    """Run the deterministic lake/git/mathlib/blueprint bootstrap.

    This replaces what used to be Claude's job in init.md steps 1–2.
    Safe to call on an already-initialized project — every sub-step is
    idempotent, and the bootstrap only commits if it actually did work.
    """
    log.phase(3, "Bootstrapping Lean project (lake, git, mathlib, blueprint)")

    # Basic capability checks — warn but don't block if blueprint/lake are missing.
    if not Lake.available():
        log.error("`lake` is not on PATH. Run: archon setup")
        raise typer.Exit(1)
    if not Git.available():
        log.error("`git` is not on PATH. Run: archon setup")
        raise typer.Exit(1)
    if not Blueprint.available():
        log.warn("`leanblueprint` is not on PATH — will skip blueprint scaffold (run: archon setup)")

    options = BootstrapOptions(
        init_lake=True,
        add_mathlib=True,
        init_blueprint=Blueprint.available(),
        fetch_mathlib_cache=True,
        do_initial_build=True,
        project_title=project_path.name,
    )

    bootstrap = ProjectBootstrap(project_path, options)
    report = bootstrap.run()

    for a in report.actions:
        log.step(a)
    for w in report.warnings:
        log.warn(w)
    for s in report.skipped:
        log.info(f"Skipped: {s}")

    # Write README / references summary skeletons.
    templates = WorkspaceTemplates(project_path)
    if templates.ensure_readme():
        log.step("Wrote README.md skeleton")
    if templates.ensure_references_summary():
        log.step("Wrote references/summary.md skeleton")

    if report.stage_report:
        sr = report.stage_report
        log.info(
            f"Stage detection: {sr.stage} "
            f"({sr.lean_file_count} Lean files, {sr.decl_count} declarations, {sr.sorry_count} sorries)"
        )

    log.success("Bootstrap complete")
    return report


# ── Claude / MCP / plugin registration ────────────────────────────────


def _step_lean_lsp_mcp(project_path: Path) -> None:
    log.phase(4, "Installing lean-lsp MCP server (project scope)")

    lean_lsp_dir = _data_path("tools/lean-lsp-mcp")

    existing = _run(["claude", "mcp", "list"], cwd=project_path)
    already_registered = "archon-lean-lsp" in (existing.stdout or "")

    if already_registered:
        log.step("Found existing archon-lean-lsp. Removing to refresh paths...")
        _run(["claude", "mcp", "remove", "archon-lean-lsp", "-s", "project"], cwd=project_path)

    for name in _find_global_mcp_lean_lsp():
        log.warn(f"Found conflicting MCP server '{name}' in global config")
        _run(["claude", "mcp", "remove", name, "-s", "project"], cwd=project_path)
        log.success(f"Disabled '{name}' for this project")

    r = _run(
        ["claude", "mcp", "add", "archon-lean-lsp", "-s", "project", "--",
         "uv", "run", "--directory", str(lean_lsp_dir), "lean-lsp-mcp"],
        cwd=project_path,
    )
    output = r.stdout + r.stderr
    if "already exists" in output.lower():
        log.success("archon-lean-lsp already configured")
    elif r.returncode == 0:
        log.success("archon-lean-lsp added")
    else:
        log.error(f"Failed to add archon-lean-lsp: {output.strip()}")


def _step_skills(project_path: Path) -> None:
    log.phase(5, "Installing Archon skills")

    home = Path.home()
    skills_dir = _data_path("skills")
    plugin_json_path = skills_dir / "lean4" / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        log.error("Archon lean4 skills not found in package data")
        raise typer.Exit(1)

    (project_path / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (project_path / ".claude" / "rules").mkdir(parents=True, exist_ok=True)

    log.step("Registering archon-local marketplace")
    market_needs_update = True
    r = _run(["claude", "plugin", "marketplace", "list"])
    if "archon-local" in (r.stdout or ""):
        known_path = home / ".claude" / "plugins" / "known_marketplaces.json"
        data = _read_json(known_path)
        current = data.get("archon-local", {}).get("source", {}).get("path", "")
        if current == str(skills_dir):
            log.success("archon-local marketplace already up to date")
            market_needs_update = False
        else:
            log.warn(f"archon-local points to a stale path: {current}")
            _run(["claude", "plugin", "marketplace", "remove", "archon-local"])

    if market_needs_update:
        r = _run(["claude", "plugin", "marketplace", "add", str(skills_dir)])
        output = r.stdout + r.stderr
        if r.returncode == 0 or "already" in output.lower():
            log.success("Registered archon-local marketplace")
        else:
            log.error(f"Failed to register marketplace: {output.strip()}")
            raise typer.Exit(1)

    log.step("Installing lean4 plugin (project scope)")
    installed_json = home / ".claude" / "plugins" / "installed_plugins.json"
    installed_data = _read_json(installed_json)
    installed_here = any(
        entry.get("projectPath") == str(project_path)
        for entry in installed_data.get("plugins", {}).get("lean4@archon-local", [])
    )
    if not installed_here:
        r = _run(
            ["claude", "plugin", "install", "lean4@archon-local", "--scope", "project"],
            cwd=project_path,
        )
        output = r.stdout + r.stderr
        if "success" in output.lower() or r.returncode == 0:
            log.success("lean4@archon-local installed")
        else:
            log.error(f"Failed to install lean4@archon-local: {output.strip()}")
            raise typer.Exit(1)
    else:
        log.success("lean4@archon-local already installed for this project")

    tools_dir = project_path / ".claude" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    agent_src = _data_path("tools/informal_agent.py")
    agent_dst = tools_dir / "archon-informal-agent.py"
    if agent_src.exists():
        _copy_file(agent_src, agent_dst, overwrite=True)
        log.success("Informal agent copied to .claude/tools/")


def _step_disable_conflicting_plugins(project_path: Path) -> None:
    log.phase(6, "Checking for conflicting global lean4-skills")
    conflicting = _find_global_lean4_plugins()
    if not conflicting:
        log.success("No conflicting global lean4-skills detected")
        return
    log.warn(f"Found {len(conflicting)} conflicting plugin(s) in global config")
    for name in conflicting:
        r = _run(
            ["claude", "plugin", "disable", name, "--scope", "project"],
            cwd=project_path,
        )
        if r.returncode == 0:
            log.success(f"Disabled '{name}' for this project")
        else:
            log.warn(f"Could not auto-disable '{name}'")


# ── Claude semantic pass ──────────────────────────────────────────────


def _step_claude_semantic_pass(
    project_path: Path,
    state_dir: Path,
    report: BootstrapReport,
    *,
    model: str = DEFAULT_MODEL,
) -> None:
    """Launch Claude for the narrow semantic tasks that remain after bootstrap.

    Claude's job is: verify the bootstrap, reorganize loose reference files,
    fill in the README / summary.md prose, walk the user through the initial
    archon-protected.yaml, and propose initial objectives. Everything
    deterministic has already happened.
    """
    stage = _parse_stage(state_dir / "PROGRESS.md")
    project_name = project_path.name

    if stage != "init":
        log.success(f"Init already complete — current stage: {stage}")
        log.step(f"Next: archon loop {project_path}")
        return

    # Re-inspect to pick up what the bootstrap just did.
    layout = ProjectLayout.inspect(project_path)

    log.header(f"Initializing project: {project_name}")
    log.step("Handing off to Claude for the semantic pass.")

    # Structured report for Claude: exactly what the bootstrap did/didn't do,
    # so Claude doesn't re-check everything itself.
    bootstrap_summary = {
        "actions": report.actions,
        "warnings": report.warnings,
        "skipped": report.skipped,
        "layout": {
            "has_lakefile": layout.has_lakefile,
            "lakefile_kind": layout.lakefile_kind,
            "has_mathlib": layout.has_mathlib,
            "has_blueprint": layout.has_blueprint,
            "has_references_dir": layout.has_references_dir,
            "has_readme": layout.has_readme,
            "lean_file_count": len(layout.lean_files),
            "loose_references": [str(p.relative_to(project_path)) for p in layout.loose_references],
        },
        "stage": report.stage_report.stage if report.stage_report else "autoformalize",
        "sorry_count": report.stage_report.sorry_count if report.stage_report else 0,
        "decl_count": report.stage_report.decl_count if report.stage_report else 0,
    }

    prompt = textwrap.dedent(f"""\
        You are in the init stage for project '{project_name}' at {project_path}. \
        Read {state_dir}/CLAUDE.md, then read {state_dir}/prompts/init.md and follow \
        its instructions. Project state files are in {state_dir}/. Write PROGRESS.md \
        and other state files there, not in the project directory.

        IMPORTANT: After checking the project state, do NOT write initial objectives \
        on your own. Instead, propose what you think the objectives should be, then \
        ask the user to confirm or adjust before writing them to PROGRESS.md. Wait \
        for the user's reply.

        When the user has confirmed and you have finished the init steps, run \
        /archon-lean4:doctor to verify the full setup before exiting.

        Remark: A bootstrap process has already run to install lake, mathlib, and \
        other deterministic setup, including creating an empty \
        {protect.PROTECTED_FILENAME} at the project root. Here is its report:
        {json.dumps(bootstrap_summary, indent=2)}
        """)

    ClaudeAgent(model=model, role="init").run_interactive(prompt, cwd=project_path)

    new_stage = _parse_stage(state_dir / "PROGRESS.md")
    if new_stage == "init":
        log.warn("Stage is still 'init' — setup may not be complete")
        log.step(f"Re-run: archon init {project_path}")
    else:
        log.success(f"Init complete — stage is now: {new_stage}")
        log.step(f"Next: archon loop {project_path}")


# ── protected-declarations summary ────────────────────────────────────


def _step_report_protected(project_path: Path) -> None:
    """Log the contents of archon-protected.yaml and remind the user to edit it.

    Always the last phase of init, regardless of whether this was a fresh
    init or a merge-based re-init. The user should leave the CLI knowing
    exactly which declarations agents will refuse to touch — or that the
    list is empty and they should go fill it in.
    """
    log.phase(7, "Protected declarations")
    ps = protect.load(project_path)

    if not ps.entries:
        log.info(
            f"{protect.PROTECTED_FILENAME} is empty — no declarations are "
            "currently protected from agent edits."
        )
        log.step(
            f"List declarations you want Archon to treat as read-only in "
            f"{protect.PROTECTED_FILENAME} at the project root. "
            "Agents will refuse to modify their signatures."
        )
        return

    total = ps.total_count()
    log.info(f"{total} protected declaration(s) across {len(ps.entries)} file(s):")
    shown = 0
    done = False
    for file, names in ps.entries.items():
        if done:
            break
        log.step(f"  [bold]{file}[/bold]")
        for n in names:
            log.step(f"    - {n}")
            shown += 1
            if shown >= 20:
                done = True
                break
    if total > shown:
        log.step(f"  ... and {total - shown} more")
    log.step(
        f"Edit {protect.PROTECTED_FILENAME} to add or remove entries. "
        "It is committed to git so the whole team shares it."
    )


# ── inner git + version stamp ─────────────────────────────────────────


def _step_inner_git(project_path: Path) -> None:
    """Initialize the inner `.archon/.git` repo and capture the current state.

    Archon keeps its own versioning here — every agent phase commits. The
    outer (mathematician's) git repo is untouched after init.
    """
    log.phase(8, "Inner git (.archon/.git)")

    if not InnerGit.available():
        log.warn("`git` not on PATH — skipping inner-git setup. Run: archon setup")
        return

    inner = InnerGit(project_path)
    created = inner.init()
    if created:
        log.step("Initialized inner git at .archon/git-dir")
    else:
        log.step("Inner git already present at .archon/git-dir")

    # First commit captures the state at init time so subsequent phase
    # commits have a baseline to diff against.
    made_initial = inner.ensure_initial_commit("archon[000/init]: initial state")
    if made_initial:
        log.success(f"Inner git first commit: {inner.head_sha() or '?'}")
    else:
        log.info(f"Inner git HEAD: {inner.head_sha() or '?'}")


def _step_env_and_config(project_path: Path) -> None:
    """Create .archon/.env and .archon/config.json with sensible defaults.

    Both files are idempotent: existing user content is left alone. The
    .env is gitignored by both outer (.archon/ rule) and inner git
    (default-excludes rule, see InnerGit). config.json is tracked by
    inner git so it's versioned with the project.
    """
    from archon.commands.tooling import env_loader, project_config

    log.phase(9, "Project config (.archon/config.json) and .env")

    env_written = env_loader.write_env_template(project_path)
    if env_written:
        log.step("Wrote .archon/.env (alternative-provider keys, gitignored)")
    else:
        log.step(".archon/.env already exists — preserved")

    cfg_written = project_config.write_default_config(project_path)
    if cfg_written:
        log.step("Wrote .archon/config.json with default loop + multilane settings")
    else:
        log.step(".archon/config.json already exists — preserved")
    log.success("Project config ready")


def _step_version_stamp(project_path: Path) -> None:
    """Stamp the current CLI version into .archon/VERSION."""
    log.phase(9, "Version stamp")
    pv = ProjectVersion(project_path)
    previous = pv.read()
    written = pv.write()
    if previous is None:
        log.success(f"Stamped project version: {written}")
    elif previous != written:
        log.success(f"Updated project version: {previous} → {written}")
    else:
        log.step(f"Project version unchanged: {written}")


# ── main command ──────────────────────────────────────────────────────


def init(
    project_path: str = typer.Argument(
        None,
        help="Path to Lean project (directory containing lakefile.lean/toml). "
        "If omitted, prompts for a name and creates the project.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Skip the re-init prompt and overwrite existing Archon files.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Claude model alias (e.g. 'opus', 'sonnet') or full id used for "
             "the interactive init pass.",
    ),
) -> None:
    """Initialize a new Archon project.

    Runs the deterministic bootstrap (lake init, Mathlib, blueprint, workspace
    skeletons) in Python, then hands off to Claude Code for the semantic pass
    only: reorganizing reference files, writing README/summary.md prose, and
    proposing initial objectives.

    [bold]Examples:[/bold]
      [cyan]archon init .[/cyan]
      [cyan]archon init /path/to/lean-project[/cyan]
    """
    log.header("archon init")

    if project_path is None:
        log.info("No project path specified")
        log.step("Enter a name to create a new project, or Ctrl-C and re-run.")
        name = typer.prompt("  Project name")
        if not name:
            log.error("No project name entered")
            raise typer.Exit(1)
        resolved = Path.cwd() / name
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            _fail_permission(resolved, e)
        log.success(f"Created project at {resolved}")
    else:
        resolved = Path(project_path).resolve()
        if not resolved.exists():
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                _fail_permission(resolved, e)
            log.success(f"Created directory {resolved}")

    # Fail fast if we can't write inside the project dir — otherwise the first
    # attempt to create .archon/ bombs out mid-phase with a raw Python traceback.
    if not os.access(resolved, os.W_OK):
        _fail_permission(resolved, None)

    state_dir = resolved / ".archon"
    log.key_value({
        "Project": str(resolved),
        "State dir": str(state_dir),
    })

    warn_if_mismatch(resolved)

    if not _has("claude"):
        log.error("Claude Code is not installed. Run: archon setup")
        raise typer.Exit(1)

    # ── Re-init detection ────────────────────────────────────────
    info = _detect_existing_archon(state_dir)
    fresh = True
    mode = "fresh"
    if info["exists"] and info["has_progress"]:
        if force:
            log.warn("--force passed: overwriting existing Archon setup")
            mode = "overwrite"
        else:
            mode = _prompt_reinit_mode(info)

        if mode == "abort":
            log.info("Aborted by user — no changes made.")
            raise typer.Exit(0)

        if mode == "keep":
            log.info("Keeping existing setup. Verifying MCP / plugin registration only.")
            _step_lean_lsp_mcp(resolved)
            _step_skills(resolved)
            _step_disable_conflicting_plugins(resolved)
            _step_report_protected(resolved)
            _step_env_and_config(resolved)
            _step_inner_git(resolved)
            _step_version_stamp(resolved)
            log.success("Verification complete.")
            return

        if mode == "merge":
            staging = _stage_bundled_prompts(state_dir)
            _merge_prompts_with_claude(resolved, state_dir, staging, model=model)
            fresh = False

    # ── Deterministic setup ──────────────────────────────────────
    _step_state_dir(resolved, state_dir, fresh=fresh)
    _step_copy_prompts(state_dir, fresh=fresh)
    report = _step_bootstrap(resolved)

    _step_lean_lsp_mcp(resolved)
    _step_skills(resolved)
    _step_disable_conflicting_plugins(resolved)

    # ── Semantic pass (Claude) ───────────────────────────────────
    if fresh:
        _step_claude_semantic_pass(resolved, state_dir, report, model=model)
    else:
        log.success("Merge-based re-init complete.")
        log.step(f"Next: archon loop {resolved}")

    # Always show the protected-declarations summary.
    _step_report_protected(resolved)

    # Project config + .env first (so the inner-git commit below
    # captures them too), then inner-git setup, then version stamp.
    _step_env_and_config(resolved)
    _step_inner_git(resolved)
    _step_version_stamp(resolved)