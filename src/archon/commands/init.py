"""Initialize a new Archon project."""

import json
import shutil
import subprocess
import textwrap
from importlib import resources
from pathlib import Path

import typer

from archon import log


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


def _parse_stage(progress_md: Path) -> str:
    """Extract the current stage from PROGRESS.md."""
    if not progress_md.exists():
        return "init"
    lines = progress_md.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Current Stage"):
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return "init"


def _data_path(sub_path: str = "") -> Path:
    """Resolve a path inside the bundled archon/.archon-src/."""
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _copy_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    """Copy a single file, skipping if dst exists (unless overwrite)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
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


def _update_gitignore(project_path: Path, entry: str) -> None:
    """Add entry to .gitignore if this is a git repo."""
    if not (project_path / ".git").is_dir():
        return
    gitignore = project_path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(f"{entry}\n", encoding="utf-8")
        log.success(f"Created .gitignore with {entry}")
        return
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    if entry not in [line.strip() for line in lines]:
        with gitignore.open("a", encoding="utf-8") as f:
            f.write(f"\n# Archon state directory\n{entry}\n")
        log.success(f"Added {entry} to .gitignore")


# ── re-init detection ─────────────────────────────────────────────────


def _detect_existing_archon(state_dir: Path) -> dict:
    """Detect whether the project has already been initialized (by any Archon version)."""
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
    """Ask the user how to handle an already-initialized project."""
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
    typer.echo("  [m] merge      — compare each file and let Claude help you reconcile differences (recommended)")
    typer.echo("  [o] overwrite  — replace all Archon files with the current bundled versions (your local edits will be lost)")
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


# ── merge helpers ─────────────────────────────────────────────────────


def _stage_bundled_prompts(state_dir: Path) -> Path:
    """Copy bundled prompts to a staging dir so Claude can diff them against existing local copies."""
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


def _merge_prompts_with_claude(project_path: Path, state_dir: Path, staging: Path) -> None:
    """Launch Claude Code in a focused merge session."""
    log.phase(0, "Reconciling local vs. bundled Archon files")
    log.step("Launching Claude Code to walk you through the differences file by file.")
    log.step("For each differing file you can choose: keep local, take new, or merge manually.")

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
        corresponding file under {state_dir}. For each file where they differ:

          1. Show the user a concise summary of what changed (do NOT dump the whole file).
          2. Ask the user to choose ONE of:
               [L] keep local (do nothing)
               [N] take new (copy incoming over local)
               [M] merge manually (show a suggested merged version, then let them edit)
          3. Apply the choice:
               - For [N]: copy {staging}/<file> to {state_dir}/<file>
               - For [M]: write a proposed merge to {state_dir}/<file>, then stop so the
                 user can review it in their editor.
               - For [L]: do nothing.

        Rules:
          - Never edit .lean files.
          - Never touch {state_dir}/PROGRESS.md, task_pending.md, task_done.md, or USER_HINTS.md.
          - Only reconcile {state_dir}/prompts/*.md and {state_dir}/CLAUDE.md.
          - When done, delete {staging} and report a one-line summary: "Merged N files, kept M files."
        """)

    subprocess.run(
        ["claude", "--dangerously-skip-permissions",
         "--permission-mode", "bypassPermissions", prompt],
        cwd=project_path,
    )

    # Clean up staging if Claude didn't
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def _print_diff_summary(state_dir: Path, staging: Path) -> None:
    """Fallback: just list which files differ without interactive merge."""
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
    else:
        log.step(
            f"{differs} file(s) differ. Inspect {staging} manually, or install Claude Code "
            "and re-run to merge interactively."
        )


# ── steps ─────────────────────────────────────────────────────────────


def _step1_state_dir(
    project_path: Path,
    state_dir: Path,
    fresh: bool,
    refresh_claude_md: bool = True,
) -> None:
    """Create .archon/ state directory and populate with template files.

    Args:
      fresh: If False (re-init), preserve existing PROGRESS.md / task_*.md / USER_HINTS.md.
      refresh_claude_md: If True, overwrite CLAUDE.md with the bundled template.
        Set to False after a merge run so Claude's merged CLAUDE.md is preserved.
    """
    log.phase(1, "Setting up .archon/ state directory")

    for subdir in (
        "task_results",
        "logs",
        "prompts",
        "proof-journal/sessions",
        "proof-journal/current_session",
    ):
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)
    log.step("Created directory tree")

    template_dir = _data_path("archon-template")
    copied = 0
    preserved = 0
    # On re-init, never overwrite user state — only add missing ones.
    for name in ("PROGRESS.md", "USER_HINTS.md", "task_pending.md", "task_done.md"):
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
    claude_dst = state_dir / "CLAUDE.md"
    if refresh_claude_md:
        if claude_dst.exists() and not fresh:
            log.warn("CLAUDE.md will be overwritten with the latest bundled version "
                     "to ensure agent roles are up to date.")
        _copy_file(claude_src, claude_dst, overwrite=True)
    else:
        if not claude_dst.exists() and claude_src.exists():
            _copy_file(claude_src, claude_dst)
        log.step("Preserved merged CLAUDE.md (skipping overwrite)")

    log.step(f"Copied {copied} new template file(s), preserved {preserved} existing")

    _update_gitignore(project_path, ".archon/")
    log.success("State directory ready")


def _step1b_inner_git(project_path: Path) -> None:
    """Initialize the inner git repo at .archon/git-dir/ if missing.

    Idempotent — does nothing if already initialized. Safe to call on
    every init (fresh or re-init) since it only creates the bare repo
    and default excludes; commits happen later, inside the loop.
    """
    from archon.commands.tooling.inner_git import InnerGit

    log.phase(1, "Setting up inner git (.archon/git-dir/)")
    inner = InnerGit(project_path)
    if inner.init():
        log.step("Initialized inner git repo")
    else:
        inner.ensure_excludes()
        log.step("Inner git already initialized")
    log.success("Inner git ready")


def _step2_copy_prompts(state_dir: Path, fresh: bool) -> None:
    """Copy prompt files into .archon/prompts/.

    Args:
      fresh: If True (first init or 'overwrite' mode), replace existing prompts
        with the bundled versions. If False (e.g. after a merge), preserve
        existing local prompts — any reconciliation was already handled by
        the merge step.
    """
    log.phase(2, "Copying prompts")

    prompts_src = _data_path("prompts")
    prompts_dst = state_dir / "prompts"
    prompts_dst.mkdir(parents=True, exist_ok=True)

    if not prompts_src.exists():
        log.error(f"Prompts directory not found at {prompts_src}")
        return

    new = 0
    preserved = 0
    overwritten = 0
    for f in sorted(prompts_src.glob("*.md")):
        dst = prompts_dst / f.name
        if dst.exists():
            # If it's a stale symlink from the old layout, replace it with a real file
            # regardless of fresh mode — broken symlinks must always be fixed.
            if dst.is_symlink():
                dst.unlink()
                _copy_file(f, dst, overwrite=True)
                new += 1
                continue
            if fresh:
                # Overwrite mode: replace existing prompts with the bundled version.
                _copy_file(f, dst, overwrite=True)
                overwritten += 1
            else:
                # Re-init without overwrite: keep the user's local copy.
                preserved += 1
            continue
        _copy_file(f, dst)
        new += 1

    if fresh:
        if overwritten:
            log.success(f"Copied {new} new prompt(s), overwrote {overwritten} existing "
                        "with bundled versions")
        else:
            log.success(f"Copied {new} prompt(s) to .archon/prompts/")
    else:
        log.success(f"Added {new} new prompt(s), preserved {preserved} existing")
    log.step("To customize: edit files directly in .archon/prompts/")


def _step3_lean_lsp_mcp(project_path: Path, fresh: bool) -> None:
    """Install lean-lsp MCP server at project scope."""
    log.phase(3, "Installing lean-lsp MCP server (project scope)")

    lean_lsp_dir = _data_path("tools/lean-lsp-mcp")

    existing = _run(["claude", "mcp", "list"], cwd=project_path)
    already_registered = "archon-lean-lsp" in (existing.stdout or "")

    if already_registered:
        log.step("Found existing archon-lean-lsp. Removing old registration to update paths...")
        _run(["claude", "mcp", "remove", "archon-lean-lsp", "-s", "project"], cwd=project_path)

    for name in _find_global_mcp_lean_lsp():
        log.warn(f"Found conflicting MCP server '{name}' in global config")
        log.step("Disabling for this project — Archon's version will be used instead")
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
        log.success("archon-lean-lsp added with updated paths (project scope)")
    else:
        log.error(f"Failed to add archon-lean-lsp: {output.strip()}")


def _step4_skills(project_path: Path, fresh: bool) -> None:
    """Install Archon skills via plugin marketplace."""
    log.phase(4, "Installing Archon skills")

    home = Path.home()
    skills_dir = _data_path("skills")
    plugin_json_path = skills_dir / "lean4" / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        log.error("Archon lean4 skills not found in package data")
        log.step(f"Searched at {plugin_json_path}")
        log.step("The installation may be incomplete — try reinstalling")
        raise typer.Exit(1)

    (project_path / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
    (project_path / ".claude" / "rules").mkdir(parents=True, exist_ok=True)

    # 4a: Register archon-local marketplace
    log.step("Registering archon-local marketplace")
    market_needs_update = True
    r = _run(["claude", "plugin", "marketplace", "list"])
    if "archon-local" in (r.stdout or ""):
        known_path = home / ".claude" / "plugins" / "known_marketplaces.json"
        data = _read_json(known_path)
        current = data.get("archon-local", {}).get("source", {}).get("path", "")
        if current == str(skills_dir):
            log.success("archon-local marketplace already registered at the current path")
            market_needs_update = False
        else:
            log.warn(f"archon-local points to a stale path: {current}")
            log.step(f"Updating to: {skills_dir}")
            _run(["claude", "plugin", "marketplace", "remove", "archon-local"])

    if market_needs_update:
        r = _run(["claude", "plugin", "marketplace", "add", str(skills_dir)])
        output = r.stdout + r.stderr
        if r.returncode == 0 or "already" in output.lower():
            log.success("Registered archon-local marketplace")
        else:
            log.error(f"Failed to register marketplace: {output.strip()}")
            raise typer.Exit(1)

    # 4b: Install lean4 plugin at project scope
    log.step("Installing lean4 plugin (project scope)")
    plugin_data = _read_json(plugin_json_path)
    plugin_version = plugin_data.get("version", "4.4.0")

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
            log.success("lean4@archon-local installed (project scope)")
        else:
            log.error(f"Failed to install lean4@archon-local: {output.strip()}")
            raise typer.Exit(1)
    else:
        log.success("lean4@archon-local already installed for this project")

    # 4c: Copy informal agent tool into project
    log.step("Copying informal agent tool")
    tools_dir = project_path / ".claude" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    agent_src = _data_path("tools/informal_agent.py")
    agent_dst = tools_dir / "archon-informal-agent.py"
    if agent_src.exists():
        _copy_file(agent_src, agent_dst, overwrite=True)
        log.success("Informal agent copied to .claude/tools/")
    else:
        log.warn("Informal agent not found in package data — skipping")


def _step5_disable_conflicting_plugins(project_path: Path) -> None:
    """Detect and disable conflicting global lean4-skills for this project."""
    log.phase(5, "Checking for conflicting global lean4-skills")

    conflicting = _find_global_lean4_plugins()
    if not conflicting:
        log.success("No conflicting global lean4-skills detected")
        return

    log.warn(f"Found {len(conflicting)} conflicting plugin(s) in global config:")
    for name in conflicting:
        log.step(f"  {name}")

    for name in conflicting:
        r = _run(
            ["claude", "plugin", "disable", name, "--scope", "project"],
            cwd=project_path,
        )
        if r.returncode == 0:
            log.success(f"Disabled '{name}' for this project")
        else:
            log.warn(f"Could not auto-disable '{name}'")

    log.step("Your global lean4-skills is untouched in all other projects")


def _step6_interactive_claude(project_path: Path, state_dir: Path, fresh: bool) -> None:
    """Launch interactive Claude Code session if still in init stage."""
    stage = _parse_stage(state_dir / "PROGRESS.md")
    project_name = project_path.name

    if stage != "init":
        log.success(f"Init already complete — current stage: {stage}")
        log.step(f"Next: archon loop {project_path}")
        return

    log.header(f"Initializing project: {project_name}")
    log.step("Claude will check the project state and guide you through setup")

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
        /archon-lean4:doctor to verify the full setup before exiting.""")

    subprocess.run(
        ["claude", "--dangerously-skip-permissions", "--permission-mode",
         "bypassPermissions", prompt],
        cwd=project_path,
    )

    new_stage = _parse_stage(state_dir / "PROGRESS.md")
    if new_stage == "init":
        log.warn("Stage is still 'init' — setup may not be complete")
        log.step(f"Re-run: archon init {project_path}")
    else:
        log.success(f"Init complete — stage is now: {new_stage}")
        log.step(f"Next: archon loop {project_path}")


# ── main command ──────────────────────────────────────────────────────


def init(
    project_path: str = typer.Argument(
        None,
        help="Path to Lean project (directory containing lakefile.lean/toml). "
        "If omitted, prompts for a name and creates the project.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Skip the re-init prompt and overwrite existing Archon files. "
        "Use with care — this discards local prompt/CLAUDE.md edits.",
    ),
) -> None:
    """
    Initialize a new Archon project.

    Creates .archon/ inside the target project with state files, copied
    prompts, skills, MCP server, and launches Claude for initial setup.

    You can add context files (pdf, markdown, etc.) directly in the project
    directory, and Claude will be able to read them during the init session.

    If the project has already been initialized (by this or an older version
    of Archon), you'll be offered a choice: keep the existing setup, merge
    changes interactively, or overwrite.

    [bold]Examples of use:[/bold]
      [cyan]archon init .[/cyan]                          Initialize the current directory.
      [cyan]archon init /path/to/lean-project[/cyan]      Initialize an existing external project.
    """
    log.header("archon init")

    if project_path is None:
        log.info("No project path specified")
        log.step("Enter a name to create a new project,")
        log.step("or Ctrl-C and re-run with: archon init /path/to/project")
        name = typer.prompt("  Project name")
        if not name:
            log.error("No project name entered")
            raise typer.Exit(1)
        resolved = Path.cwd() / name
        resolved.mkdir(parents=True, exist_ok=True)
        log.success(f"Created project at {resolved}")
    else:
        resolved = Path(project_path).resolve()
        if not resolved.exists():
            resolved.mkdir(parents=True, exist_ok=True)
            log.success(f"Created directory {resolved}")

    state_dir = resolved / ".archon"

    log.key_value({
        "Project": str(resolved),
        "State dir": str(state_dir),
    })

    if not _has("claude"):
        log.error("Claude Code is not installed")
        log.step("Run: archon setup")
        raise typer.Exit(1)

    # ── Re-init detection ────────────────────────────────────────
    info = _detect_existing_archon(state_dir)
    fresh = True
    # Track whether the merge step has already reconciled CLAUDE.md so step1
    # does not clobber the merged result.
    merged_claude_md = False

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
            _step3_lean_lsp_mcp(resolved, fresh=False)
            _step4_skills(resolved, fresh=False)
            _step5_disable_conflicting_plugins(resolved)
            log.success("Verification complete.")
            return

        if mode == "merge":
            staging = _stage_bundled_prompts(state_dir)
            _merge_prompts_with_claude(resolved, state_dir, staging)
            fresh = False
            merged_claude_md = True  # Claude just handled CLAUDE.md; do not overwrite.

        if mode == "overwrite":
            fresh = True  # proceed through normal init, overwriting where applicable

    _step1_state_dir(
        resolved,
        state_dir,
        fresh=fresh,
        refresh_claude_md=not merged_claude_md,
    )
    _step1b_inner_git(resolved)
    _step2_copy_prompts(state_dir, fresh=fresh)
    _step3_lean_lsp_mcp(resolved, fresh=fresh)
    _step4_skills(resolved, fresh=fresh)
    _step5_disable_conflicting_plugins(resolved)

    # If this was a merge run, PROGRESS.md already exists and the user has
    # a valid setup — skip the interactive init session.
    if fresh:
        _step6_interactive_claude(resolved, state_dir, fresh=fresh)
    else:
        log.success("Merge-based re-init complete.")
        log.step(f"Next: archon loop {resolved}")