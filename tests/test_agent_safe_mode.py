"""Native workspace-only sandbox selection for ``archon loop --safe``."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from archon.agent import (
    SAFE_MODE_ENV,
    ClaudeAgent,
    prepare_safe_tmp,
)
from archon.agents.codex import CodexAgent
from archon.commands.setup.checks.claude_sandbox import ClaudeSandboxCheck
from archon.commands.tooling.project_config import HarnessDescriptor


class ClaudeSafeModeTest(unittest.TestCase):
    def test_default_keeps_unrestricted_permissions(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            flags = ClaudeAgent()._build_flags("opus", cwd=Path("/workspace"))
        self.assertIn("--dangerously-skip-permissions", flags)
        self.assertEqual(flags[flags.index("--permission-mode") + 1], "bypassPermissions")
        self.assertNotIn("--settings", flags)

    def test_safe_mode_uses_strict_auto_allow_sandbox(self) -> None:
        with patch.dict(os.environ, {SAFE_MODE_ENV: "1"}, clear=True):
            flags = ClaudeAgent()._build_flags("opus", cwd=Path("/workspace"))

        self.assertNotIn("--dangerously-skip-permissions", flags)
        self.assertEqual(flags[flags.index("--permission-mode") + 1], "acceptEdits")
        settings = json.loads(flags[flags.index("--settings") + 1])
        sandbox = settings["sandbox"]
        self.assertTrue(sandbox["enabled"])
        self.assertTrue(sandbox["autoAllowBashIfSandboxed"])
        self.assertTrue(sandbox["failIfUnavailable"])
        self.assertFalse(sandbox["allowUnsandboxedCommands"])
        self.assertEqual(sandbox["filesystem"]["allowWrite"], ["/workspace"])
        self.assertEqual(sandbox["network"]["allowedDomains"], ["*"])

    def test_safe_tmp_is_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env: dict[str, str] = {}
            with patch.dict(os.environ, {SAFE_MODE_ENV: "1"}, clear=True):
                prepare_safe_tmp(root, env)

            sandbox_tmp = Path(env["TMPDIR"])
            self.assertTrue(sandbox_tmp.is_dir())
            self.assertTrue(sandbox_tmp.is_relative_to(root / ".archon" / "tmp"))
            self.assertEqual(env["TMP"], env["TMPDIR"])
            self.assertEqual(env["TEMP"], env["TMPDIR"])


class CodexSafeModeTest(unittest.TestCase):
    def _agent(self) -> CodexAgent:
        return CodexAgent(
            descriptor=HarnessDescriptor(
                name="codex", runner="codex", sandbox="danger-full-access",
            ),
            role="prover",
        )

    def test_default_keeps_descriptor_sandbox(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            argv = self._agent().build_argv("PROMPT", env_source={})
        self.assertEqual(argv[argv.index("--sandbox") + 1], "danger-full-access")
        self.assertNotIn('approval_policy="never"', argv)

    def test_safe_mode_uses_workspace_write_without_prompts(self) -> None:
        with patch.dict(os.environ, {SAFE_MODE_ENV: "1"}, clear=True):
            argv = self._agent().build_argv("PROMPT", env_source={})
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")
        self.assertIn('approval_policy="never"', argv)
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", argv)
        self.assertIn("sandbox_workspace_write.network_access=true", argv)
        self.assertEqual(argv[-1], "PROMPT")


class ClaudeSandboxDependencyTest(unittest.TestCase):
    def test_linux_installs_missing_dependencies(self) -> None:
        installer = Mock()
        availability = iter([True, False, True, True])
        with patch(
            "archon.commands.setup.checks.claude_sandbox.sys.platform", "linux"
        ), patch(
            "archon.commands.setup.checks.claude_sandbox.has",
            side_effect=lambda _name: next(availability),
        ):
            self.assertTrue(ClaudeSandboxCheck(installer).run())
        installer.install_bundle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
