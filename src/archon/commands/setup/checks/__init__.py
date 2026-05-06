"""Per-tool dependency checks for `archon setup`.

Each subclass of :class:`DependencyCheck` probes for a specific tool,
optionally installs it (via :class:`PackageInstaller`), and returns
True iff the dependency is now usable.
"""

from .api_keys import ApiKeysCheck
from .base import DependencyCheck
from .claude_code import ClaudeCodeCheck
from .curl import CurlCheck
from .dashboard_deps import DashboardDepsCheck
from .git import GitCheck
from .graphviz import GraphvizCheck
from .leanblueprint import LeanBlueprintCheck
from .lean import LeanToolchainCheck
from .node import NodeCheck
from .python import PythonCheck
from .ripgrep import RipgrepCheck
from .tex import TexToolchainCheck
from .uv import UvCheck

__all__ = [
    "DependencyCheck",
    "GitCheck",
    "PythonCheck",
    "CurlCheck",
    "LeanToolchainCheck",
    "UvCheck",
    "RipgrepCheck",
    "ClaudeCodeCheck",
    "GraphvizCheck",
    "TexToolchainCheck",
    "LeanBlueprintCheck",
    "NodeCheck",
    "DashboardDepsCheck",
    "ApiKeysCheck",
]
