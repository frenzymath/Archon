"""Init step classes — each phase of `archon init` as a self-contained class."""

from .base import InitStep
from .bootstrap import BootstrapStep
from .copy_prompts import CopyPromptsStep
from .disable_conflicts import DisableConflictingPluginsStep
from .env_config import EnvAndConfigStep
from .git_hooks import GitHooksStep
from .inner_git import InnerGitStep
from .lean_lsp import LeanLspMcpStep
from .report_protected import ReportProtectedStep
from .semantic_pass import SemanticPassStep
from .skills import SkillsStep
from .state_dir import StateDirStep
from .version_stamp import VersionStampStep

__all__ = [
    "InitStep",
    "StateDirStep",
    "CopyPromptsStep",
    "BootstrapStep",
    "LeanLspMcpStep",
    "SkillsStep",
    "DisableConflictingPluginsStep",
    "SemanticPassStep",
    "ReportProtectedStep",
    "EnvAndConfigStep",
    "InnerGitStep",
    "GitHooksStep",
    "VersionStampStep",
]
