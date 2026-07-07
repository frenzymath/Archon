"""Regression: Ctrl+C during the verify gate must explain where the work is.

The verify gate runs *after* the interactive session, outside its
try/except, and `lake build` (on by default for merge) can take minutes. A
user who Ctrl+C's the gate previously got nothing — finalize was skipped, the
in-place edits sat uncommitted in the working tree, and no message said so, so
the merge looked like it "didn't modify any files". The flow must now catch
the interrupt, announce where the content is, and exit non-zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from archon.commands.extract.command import ExtractCommand


def test_gate_interrupt_is_caught_and_announced(monkeypatch, tmp_path, capsys):
    edited = {}

    class DummyRunner:
        def run_interactive(self, prompt, *, cwd):
            edited["cwd"] = cwd
            return 0

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "archon.commands.extract.command.build_runner",
        lambda **kw: DummyRunner(),
    )
    monkeypatch.setattr(ExtractCommand, "_preflight", lambda self: None)
    monkeypatch.setattr(ExtractCommand, "_build_prompt", lambda self: "P")
    monkeypatch.setattr(ExtractCommand, "_announce_in_place", lambda self, r: None)
    # The session prepares an in-place merge; skip the real snapshot machinery.
    monkeypatch.setattr(
        "archon.commands.extract.command.prepare_in_place",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("archon.commands.extract.command.verify_sandbox", boom)

    cmd = ExtractCommand(
        str(tmp_path / "B"), None,
        merge_source=str(tmp_path / "A"), in_place=True, build_check=True,
    )
    # KeyboardInterrupt from the gate must be converted to a clean Exit(130),
    # not propagate as a raw KeyboardInterrupt.
    with pytest.raises(typer.Exit) as exc:
        cmd.run()
    assert exc.value.exit_code == 130

    out = capsys.readouterr().out
    assert "interrupted" in out.lower()
    assert "working tree" in out.lower()
    assert "--resume" in out
