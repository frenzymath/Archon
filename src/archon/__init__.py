"""Archon: Autonomous Lean 4 formalization system."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("archon")
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"