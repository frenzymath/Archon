"""Interactive harness selection for `archon init`."""

from __future__ import annotations

import typer

from archon import log
from archon.commands.tooling.project_config import DEFAULT_HARNESS, LOOP_ROLES

_CODEX_HARNESS = "codex"
_ANTIGRAVITY_HARNESS = "antigravity"
_FLAG_CHOICES = ("claude-code", _CODEX_HARNESS, _ANTIGRAVITY_HARNESS, "mixed")
_MIXED_ROLE_DEFAULTS = {
    "plan": DEFAULT_HARNESS,
    "prover": _CODEX_HARNESS,
    "review": DEFAULT_HARNESS,
}


def selection_from_choice(choice: str, role_choices: dict | None = None):
    """Map a menu/flag choice to a project_config harness selection."""
    normalized = choice.strip().lower()
    if normalized in ("1", "claude-code", "claude", "c"):
        return None
    if normalized in ("2", _CODEX_HARNESS, "x"):
        return _CODEX_HARNESS
    if normalized in ("3", _ANTIGRAVITY_HARNESS, "a"):
        return _ANTIGRAVITY_HARNESS
    if normalized in ("4", "mixed", "m"):
        roles = dict(role_choices or {})
        return {r: roles[r] for r in LOOP_ROLES if r in roles}
    raise ValueError(f"unknown harness choice {choice!r}")


def _prompt_role_choices() -> dict:
    """Ask one harness per role for the Mixed preset."""
    log.step(
        "Mixed mode: choose an engine per role. Tip: codex fits the prover; "
        "plan/review work best on claude-code."
    )
    choices: dict[str, str] = {}
    for role in LOOP_ROLES:
        default_name = _MIXED_ROLE_DEFAULTS[role]
        default_letter = "x" if default_name == _CODEX_HARNESS else "c"
        while True:
            ans = typer.prompt(
                f"  {role:6s} engine - [c] claude-code  [x] codex  [a] antigravity",
                default=default_letter,
            ).strip().lower()
            if ans in ("c", "claude-code", "claude"):
                choices[role] = DEFAULT_HARNESS
                break
            if ans in ("x", "codex"):
                choices[role] = _CODEX_HARNESS
                break
            if ans in ("a", "antigravity"):
                choices[role] = _ANTIGRAVITY_HARNESS
                break
    return choices


def prompt_harness_selection():
    """Interactively ask which harness setup the project should use."""
    typer.echo("")
    typer.echo("Which engine should run the loop's agents (plan / prover / review)?")
    typer.echo("  [1] Claude Code + Opus      - default")
    typer.echo("  [2] Codex CLI              - uses native ~/.codex login/default model")
    typer.echo("  [3] Antigravity CLI        - deepmind native autonomous platform")
    typer.echo("  [4] Mixed                  - choose per-role (e.g. plan/review=claude, prover=codex)")
    typer.echo("")

    while True:
        choice = typer.prompt("Choice (1/2/3/4)", default="1").strip().lower()
        try:
            if choice in ("4", "mixed", "m"):
                return selection_from_choice("4", _prompt_role_choices())
            return selection_from_choice(choice)
        except ValueError:
            typer.echo("Please answer 1, 2, 3, or 4.")


def resolve_harness_selection(ctx):
    """Resolve harness selection for a fresh-config init."""
    flag = getattr(ctx, "harness", None)
    if flag:
        normalized = flag.strip().lower()
        if normalized not in _FLAG_CHOICES:
            log.error(
                f"--harness {flag!r} is not one of "
                f"{', '.join(_FLAG_CHOICES)}."
            )
            raise typer.Exit(1)
        if normalized == "mixed":
            return selection_from_choice("3", _prompt_role_choices())
        return selection_from_choice(normalized)
    return prompt_harness_selection()


__all__ = [
    "selection_from_choice",
    "prompt_harness_selection",
    "resolve_harness_selection",
]
