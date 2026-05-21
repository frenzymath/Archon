"""Subagent registry — file-based discovery of subagent descriptors.

Subagents are defined by ``.md`` files with YAML frontmatter sitting in:

* **Built-in** — ``src/archon/.archon-src/subagents/<name>.md``.
* **Project-local** — ``<project>/.archon/subagents/<name>.md``.

Discovery order (later overrides earlier):

1. Built-in defaults shipped with Archon.
2. Project-local overrides (lets a project hand-tune a built-in
   subagent's prompt body or write-domain hint without forking).

Loading honors ``config.json``'s ``subagents.enabled`` (positive
allowlist). When absent/null, every descriptor whose
``default_enabled`` is True loads. Drop a file → it's available;
delete the file → it's gone. No code changes anywhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from archon import log

from .base import SubagentDescriptor


# YAML frontmatter delimiter (same convention as static-site generators).
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*(?:\n|$)", re.DOTALL)


class SubagentRegistry:
    """In-memory map of subagent name → descriptor."""

    def __init__(self, descriptors: dict[str, SubagentDescriptor]):
        self._d = dict(descriptors)

    def __contains__(self, name: str) -> bool:
        return name in self._d

    def __getitem__(self, name: str) -> SubagentDescriptor:
        return self._d[name]

    def __len__(self) -> int:
        return len(self._d)

    def get(self, name: str) -> SubagentDescriptor | None:
        return self._d.get(name)

    def names(self) -> list[str]:
        return sorted(self._d.keys())

    def descriptors(self) -> list[SubagentDescriptor]:
        return [self._d[n] for n in self.names()]


def parse_descriptor_file(path: Path) -> SubagentDescriptor:
    """Parse one ``.md`` file into a :class:`SubagentDescriptor`.

    Required: YAML frontmatter (``---`` … ``---``) with a ``name``
    field that matches the filename stem. Everything else has a
    default.

    Raises:
        ValueError: malformed frontmatter, missing/empty ``name``, or
            ``name`` disagreeing with filename stem.
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(
            f"{path}: missing YAML frontmatter (expected leading "
            r"`---\n...\n---`)."
        )
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"{path}: invalid YAML frontmatter: {e}")
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping.")

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: frontmatter is missing `name`.")
    if name != path.stem:
        raise ValueError(
            f"{path}: frontmatter name {name!r} doesn't match filename "
            f"stem {path.stem!r}."
        )

    write_domain = meta.get("write_domain")
    if write_domain is not None and not isinstance(write_domain, str):
        raise ValueError(f"{path}: `write_domain` must be a string if set.")

    mandatory = _parse_mandatory(path, meta.get("mandatory"))
    dispatcher_notes = _parse_dispatcher_notes(path, meta.get("dispatcher_notes"))

    return SubagentDescriptor(
        name=name,
        description=str(meta.get("description") or ""),
        write_domain=write_domain,
        read_only=bool(meta.get("read_only", False)),
        can_spawn=bool(meta.get("can_spawn", False)),
        default_enabled=bool(meta.get("default_enabled", True)),
        mandatory=mandatory,
        dispatcher_notes=dispatcher_notes,
        prompt_body=text[m.end():],
        source_path=path,
    )


def _parse_mandatory(path: Path, raw: object) -> tuple[str, ...]:
    """Parse the ``mandatory`` frontmatter field.

    Accepts:
    * Missing / null → ``()``.
    * Single string ``"plan"`` → ``("plan",)`` (convenience).
    * List of strings ``["plan", "review"]`` → tuple of strings.

    Raises ValueError on other shapes so a typo doesn't silently
    demote a mandatory subagent to optional.
    """
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item:
                raise ValueError(
                    f"{path}: `mandatory` list entries must be non-empty strings."
                )
            out.append(item)
        return tuple(out)
    raise ValueError(
        f"{path}: `mandatory` must be a string, list of strings, or omitted."
    )


def _parse_dispatcher_notes(path: Path, raw: object) -> str:
    """Parse the ``dispatcher_notes`` frontmatter field.

    Accepts:
    * Missing / null / empty → ``""``.
    * Plain string → returned with trailing whitespace stripped.
    * List of strings → joined with newlines (one bullet per item).

    Raises ValueError on other shapes.
    """
    if raw is None or raw == "":
        return ""
    if isinstance(raw, str):
        return raw.rstrip()
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise ValueError(
                    f"{path}: `dispatcher_notes` list entries must be strings."
                )
            out.append(item.rstrip())
        return "\n".join(out)
    raise ValueError(
        f"{path}: `dispatcher_notes` must be a string, list of strings, or omitted."
    )


def load_descriptors_from_dir(directory: Path) -> dict[str, SubagentDescriptor]:
    """Load every ``*.md`` in ``directory`` as a descriptor.

    Files that fail to parse are skipped with a warning, not raised
    — one bad descriptor shouldn't poison the whole registry.
    """
    out: dict[str, SubagentDescriptor] = {}
    if not directory.is_dir():
        return out
    for p in sorted(directory.glob("*.md")):
        try:
            d = parse_descriptor_file(p)
        except (OSError, ValueError) as e:
            log.warn(f"skipping subagent descriptor: {e}")
            continue
        out[d.name] = d
    return out


def _builtin_dir() -> Path:
    """Locate the shipped subagent descriptor directory."""
    from archon.commands.init.utils import data_path
    return data_path("subagents")


def build_registry(
    project_path: Path,
    *,
    enabled: list[str] | None = None,
    extra_dirs: list[Path] | None = None,
) -> SubagentRegistry:
    """Build a registry for ``project_path``.

    Precedence (later overrides earlier by name):

    1. Built-in descriptor directory.
    2. ``<project_path>/.archon/subagents/`` (project-local).
    3. Any ``extra_dirs`` (used by tests / unusual configs).

    Filtering:

    * ``enabled is None`` → keep every descriptor whose
      ``default_enabled`` is True.
    * ``enabled`` is a list → keep descriptors whose name appears
      there, regardless of ``default_enabled``.
    """
    # Precedence (lowest → highest, later overrides earlier by name):
    # built-in defaults → extra_dirs (extension slots) → project-local.
    # Project descriptors always win, which is what users expect when
    # they hand-tune a built-in by dropping a same-named file under
    # `.archon/subagents/`.
    sources: list[Path] = [_builtin_dir()]
    if extra_dirs:
        sources.extend(extra_dirs)
    sources.append(project_path / ".archon" / "subagents")

    merged: dict[str, SubagentDescriptor] = {}
    for d in sources:
        merged.update(load_descriptors_from_dir(d))

    if enabled is None:
        kept = {n: d for n, d in merged.items() if d.default_enabled}
    else:
        wanted = set(enabled)
        kept = {n: d for n, d in merged.items() if n in wanted}
    return SubagentRegistry(kept)
