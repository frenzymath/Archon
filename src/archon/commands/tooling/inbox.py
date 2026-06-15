"""Peer inbox — the one sanctioned way to write *into* a peer project.

A project is otherwise strictly read-only to its peers (see
``archon.commands.tooling.peers``). The single exception is a
**definition-improvement note**: after reading a peer's declaration, you may
leave structured feedback — "I had to reshape ``Foo.bar`` like this, here's
why" — so peers converge on definitions general enough for everyone.

Layout (machine-local, gitignored like the rest of ``.archon/``)::

    <target>/.archon/inbox/<author>.yaml      # one file per author project

One file per author keeps the channel stable: an author re-noting the same
declaration *updates* its existing note instead of spawning a new file each
time. Schema::

    from: picard-3                 # the author project's stable name
    notes:
      - type: pr                   # pr | issue
        lean_name: MeasureTheory.foo  # optional
        payload: "generalize the σ-finite hypothesis to ..."
        rationale: "picard-3 had to re-prove this in a more general form"
        status: open               # open | accepted | declined

Ownership is split: the **author** owns a note's ``suggestion`` / ``rationale``
(they wrote it); the **target** project owns ``status`` (it decides whether to
act). The plan/dag loop of the target surfaces ``open`` notes against its own
declarations; ``archon peers note`` is the only sanctioned writer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

INBOX_RELDIR = ".archon/inbox"

VALID_STATUSES = ("open", "accepted", "declined")


@dataclass
class Note:
    """One feedback item (PR or issue) from a peer."""
    type: str          # 'pr' or 'issue'
    payload: str
    lean_name: str = ""
    rationale: str = ""
    status: str = "open"


@dataclass
class AuthorInbox:
    """All notes one author project has left for the target."""
    author: str
    path: Path
    notes: list[Note] = field(default_factory=list)


def inbox_dir(project_path: Path) -> Path:
    return project_path / INBOX_RELDIR


def _safe_author_filename(author: str) -> str:
    """A filesystem-safe stem for an author name (slashes etc. → ``-``)."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", author.strip()).strip("-")
    return stem or "peer"


def author_file(project_path: Path, author: str) -> Path:
    return inbox_dir(project_path) / f"{_safe_author_filename(author)}.yaml"


# ── parsing ───────────────────────────────────────────────────────────────

def _parse_note(raw: object) -> Note | None:
    if not isinstance(raw, dict):
        return None
    lean_name = raw.get("lean_name")
    if not isinstance(lean_name, str) or not lean_name.strip():
        return None
    status = raw.get("status")
    status = status if status in VALID_STATUSES else "open"
    return Note(
        lean_name=lean_name.strip(),
        suggestion=str(raw.get("suggestion") or "").strip(),
        rationale=str(raw.get("rationale") or "").strip(),
        status=status,
    )


def load_author_inbox(path: Path) -> AuthorInbox | None:
    """Read a single ``<author>.yaml``. Returns None if absent/unreadable."""
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    author = str(raw.get("from") or path.stem)
    notes = [n for n in (_parse_note(x) for x in (raw.get("notes") or [])) if n]
    return AuthorInbox(author=author, path=path, notes=notes)


def load_all(project_path: Path) -> list[AuthorInbox]:
    """Every author inbox addressed to *project_path* (sorted by author)."""
    d = inbox_dir(project_path)
    if not d.is_dir():
        return []
    out: list[AuthorInbox] = []
    for p in sorted(d.glob("*.yaml")):
        ib = load_author_inbox(p)
        if ib is not None:
            out.append(ib)
    return sorted(out, key=lambda ib: ib.author)


def open_notes(project_path: Path) -> list[tuple[str, Note]]:
    """``(author, note)`` pairs for every ``open`` note addressed here."""
    return [
        (ib.author, n)
        for ib in load_all(project_path)
        for n in ib.notes
        if n.status == "open"
    ]


# ── writing ───────────────────────────────────────────────────────────────

def _dump(author: str, notes: list[Note]) -> str:
    header = (
        f"# Definition-improvement notes from peer project '{author}'.\n"
        f"# Written by `archon peers note`. THIS project owns each note's\n"
        f"# `status` (open | accepted | declined); the author owns the rest.\n\n"
    )
    body = yaml.safe_dump(
        {
            "from": author,
            "notes": [
                {
                    "type": n.type,
                    "lean_name": n.lean_name,
                    "payload": n.payload,
                    "rationale": n.rationale,
                    "status": n.status,
                }
                for n in notes
            ],
        },
        sort_keys=False,
        allow_unicode=True,
    )
    return header + body


def write_note(
    target_project: Path,
    author: str,
    note_type: str,
    payload: str,
    lean_name: str = "",
    rationale: str = "",
) -> Path:
    """Idempotently append or update a note in the target's inbox for this author.

    Matches existing open notes by `lean_name` and `type` (for PRs) or just appends.
    Returns the written file path.
    """
    path = author_file(target_project, author)
    path.parent.mkdir(parents=True, exist_ok=True)

    inbox = load_author_inbox(path)
    notes = list(inbox.notes) if inbox else []
    
    target_idx = -1
    for i, n in enumerate(notes):
        if n.status == "open" and n.type == note_type and n.lean_name == lean_name.strip():
            target_idx = i
            break

    new_note = Note(type=note_type, lean_name=lean_name.strip(), payload=payload.strip(), rationale=rationale.strip())

    if target_idx >= 0:
        notes[target_idx] = new_note
    else:
        notes.append(new_note)

    path.write_text(_dump(author, notes), encoding="utf-8")
    return path


def set_status(
    target_project: Path, author: str, lean_name: str, status: str,
) -> bool:
    """Target-side: mark one note ``accepted`` / ``declined`` / ``open``.

    Returns True if a matching note was found and updated.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    path = author_file(target_project, author)
    ib = load_author_inbox(path)
    if ib is None:
        return False
    changed = False
    for n in ib.notes:
        if n.lean_name == lean_name.strip():
            n.status = status
            changed = True
    if changed:
        path.write_text(_dump(ib.author, ib.notes), encoding="utf-8")
    return changed
