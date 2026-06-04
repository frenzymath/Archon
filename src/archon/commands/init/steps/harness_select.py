"""Interactive harness selection for `archon init`.

A *harness* is the engine that runs the loop's roles (plan / prover /
review). At init time we offer the user three choices and translate the
answer into a ``harness_selection`` value understood by
:func:`archon.commands.tooling.project_config.apply_harness_selection`:

* **Claude Code + Opus** (default) → ``None`` (no ``loop.harness`` key;
  the zero-config single-Anthropic path).
* **Codex CLI + GPT-5.5** → the harness name ``"codex-gpt"`` (every role).
* **Mixed** → a ``{role: harness_name}`` dict (per-role routing).

The menu is a plain ``typer`` prompt mirroring
:meth:`archon.commands.init.reinit.ReinitController.prompt_mode`. The pure
mapping (:func:`selection_from_choice`) is split out so it can be unit
tested without driving stdin. ``archon init`` is always run with a human
present (the bootstrap's semantic pass is itself interactive), so the menu
is simply the default path; an explicit ``--harness`` flag presets the
answer and skips it.
"""

from __future__ import annotations

import typer

from archon import log
from archon.commands.tooling.project_config import (
    DEFAULT_HARNESS,
    LOOP_ROLES,
)

# The codex descriptor shipped in ``default_config()['harnesses']``.
_CODEX_HARNESS = "codex-gpt"

# Accepted ``--harness`` flag values (top-level presets).
_FLAG_CHOICES = ("claude-code", _CODEX_HARNESS, "mixed")

# Per-role defaults for the Mixed preset: codex shines as the prover; plan
# and review stay on claude-code (their prompts assume Claude Code tools
# and the codex prompt variant is prover-tuned).
_MIXED_ROLE_DEFAULTS = {
    "plan": DEFAULT_HARNESS,
    "prover": _CODEX_HARNESS,
    "review": DEFAULT_HARNESS,
}


def selection_from_choice(choice: str, role_choices: dict | None = None):
    """Map a top-level menu answer to an ``apply_harness_selection`` value.

    ``choice`` is ``"1"``/``"2"``/``"3"`` (or the equivalent flag value);
    ``role_choices`` is the per-role ``{role: harness_name}`` mapping
    required when ``choice`` selects Mixed. Pure — no I/O.

    Returns ``None`` (claude-code), ``"codex-gpt"``, or the (filtered)
    role-choices dict. Raises ``ValueError`` on an unknown choice.
    """
    normalized = choice.strip().lower()
    if normalized in ("1", "claude-code", "claude", "c"):
        return None
    if normalized in ("2", _CODEX_HARNESS, "codex", "x"):
        return _CODEX_HARNESS
    if normalized in ("3", "mixed", "m"):
        roles = dict(role_choices or {})
        return {r: roles[r] for r in LOOP_ROLES if r in roles}
    raise ValueError(f"unknown harness choice {choice!r}")


def _prompt_role_choices() -> dict:
    """Ask one harness per role for the Mixed preset (codex vs claude-code)."""
    log.step(
        "Mixed mode: choose an engine per role. Tip: codex fits the prover; "
        "plan/review work best on claude-code (their prompts assume Claude "
        "Code tools like WebSearch/WebFetch and the codex variant is "
        "prover-tuned)."
    )
    choices: dict[str, str] = {}
    for role in LOOP_ROLES:
        default_name = _MIXED_ROLE_DEFAULTS[role]
        default_letter = "x" if default_name == _CODEX_HARNESS else "c"
        while True:
            ans = typer.prompt(
                f"  {role:6s} engine — [c] claude-code  [x] codex-gpt",
                default=default_letter,
            ).strip().lower()
            if ans in ("c", "claude-code", "claude"):
                choices[role] = DEFAULT_HARNESS
                break
            if ans in ("x", "codex-gpt", "codex"):
                choices[role] = _CODEX_HARNESS
                break
    return choices


def prompt_harness_selection():
    """Interactively ask which harness setup the project should use.

    Returns an ``apply_harness_selection`` value (``None`` / ``"codex-gpt"``
    / a ``{role: name}`` dict). Assumes a TTY — callers gate this with
    :func:`_is_interactive`.
    """
    typer.echo("")
    typer.echo("Which engine should run the loop's agents (plan / prover / review)?")
    typer.echo("  [1] Claude Code + Opus 4.8   — default, recommended")
    typer.echo("  [2] Codex CLI + GPT-5.5       — uses your native ~/.codex (ChatGPT) login")
    typer.echo("  [3] Mixed                     — pick an engine per role")
    typer.echo("")

    while True:
        choice = typer.prompt("Choice [1/2/3]", default="1").strip().lower()
        try:
            if choice in ("3", "mixed", "m"):
                return selection_from_choice("3", _prompt_role_choices())
            return selection_from_choice(choice)
        except ValueError:
            typer.echo("Please answer 1, 2, or 3.")


def resolve_harness_selection(ctx):
    """Resolve the harness selection for a fresh-config init.

    An explicit ``--harness`` flag (``ctx.harness``) wins; otherwise the
    interactive menu is shown. ``archon init`` always runs with a human
    present, so the menu is the default path — there is no non-interactive
    fallback. Returns an ``apply_harness_selection`` value.
    """
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
