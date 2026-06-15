"""Deterministic roadmap analysis over a scope's merge DAG.

Pure graph computation — no model in the loop. From the per-member Lean-name
sets it derives:

* **unblock matrix** — provider P satisfies names consumer C still needs;
* **duplication** — a name still open in >1 member (parallel work / a shared
  base candidate);
* **leverage** — members ranked by how many distinct (consumer, name) pairs
  they could unblock right now;
* **stalled** — members every one of whose open names is already satisfied by
  some peer (fully unblockable today).

The agentic layer (``archon scope discuss``) sits *on top* of this for the soft
"should we extract / in what order" judgement; the numbers stay reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .merge_dag import MergeDag, build_merge_dag
from archon.commands.tooling import peers as peers_mod


@dataclass
class UnblockEdge:
    provider: str
    consumer: str
    names: list[str]              # what provider satisfies that consumer needs


@dataclass
class Duplication:
    lean_name: str
    needed_by: list[str]          # >1 member still working it
    satisfied_by: list[str]       # members that already have it (reuse source)


@dataclass
class Leverage:
    member: str
    unblock_count: int            # distinct (consumer, name) pairs it unblocks
    consumers: list[str]


@dataclass
class Roadmap:
    members: list[str]
    unblock: list[UnblockEdge] = field(default_factory=list)
    duplication: list[Duplication] = field(default_factory=list)
    leverage: list[Leverage] = field(default_factory=list)
    stalled: list[str] = field(default_factory=list)
    no_dag: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "members": self.members,
            "unblock": [vars(e) for e in self.unblock],
            "duplication": [vars(d) for d in self.duplication],
            "leverage": [vars(lev) for lev in self.leverage],
            "stalled": self.stalled,
            "no_dag": self.no_dag,
        }


def build_roadmap(peers: list[peers_mod.Peer]) -> tuple[Roadmap, MergeDag]:
    """Compute the roadmap and the merge DAG it derives from."""
    mdag = build_merge_dag(peers)
    by_name = {m.name: m for m in mdag.members}
    names = [m.name for m in mdag.members]

    # Unblock matrix: provider satisfies what consumer still needs.
    unblock: list[UnblockEdge] = []
    leverage_count: dict[str, int] = {n: 0 for n in names}
    leverage_consumers: dict[str, set[str]] = {n: set() for n in names}
    for provider in names:
        for consumer in names:
            if provider == consumer:
                continue
            hits = sorted(by_name[provider].available & by_name[consumer].needed)
            if hits:
                unblock.append(UnblockEdge(provider, consumer, hits))
                leverage_count[provider] += len(hits)
                leverage_consumers[provider].add(consumer)
    unblock.sort(key=lambda e: (-len(e.names), e.provider, e.consumer))

    # Duplication: a name still open in more than one member.
    duplication: list[Duplication] = []
    for node in mdag.nodes:
        if len(node.needed_by) > 1:
            duplication.append(Duplication(
                lean_name=node.lean_name,
                needed_by=node.needed_by,
                satisfied_by=node.satisfied_by,
            ))
    duplication.sort(key=lambda d: (-len(d.needed_by), d.lean_name))

    # Leverage ranking (drop zeros).
    leverage = [
        Leverage(member=n, unblock_count=leverage_count[n],
                 consumers=sorted(leverage_consumers[n]))
        for n in names if leverage_count[n] > 0
    ]
    leverage.sort(key=lambda lev: (-lev.unblock_count, lev.member))

    # Stalled: has open names, and every one is satisfied by some peer.
    others_available: dict[str, set[str]] = {
        n: set().union(*[by_name[o].available for o in names if o != n]) if len(names) > 1 else set()
        for n in names
    }
    stalled = [
        n for n in names
        if by_name[n].needed and by_name[n].needed <= others_available[n]
    ]

    no_dag = [m.name for m in mdag.members if not m.has_dag]
    return Roadmap(
        members=names,
        unblock=unblock,
        duplication=duplication,
        leverage=leverage,
        stalled=stalled,
        no_dag=no_dag,
    ), mdag


_CAP = 15


def _trunc(items: list[str]) -> str:
    shown = items[:_CAP]
    extra = f" … (+{len(items) - _CAP} more)" if len(items) > _CAP else ""
    return ", ".join(f"`{x}`" for x in shown) + extra


def render_markdown(rm: Roadmap) -> str:
    """Human-readable roadmap report."""
    out: list[str] = ["# Archon scope roadmap", ""]
    out.append(f"{len(rm.members)} member project(s): "
               + ", ".join(f"**{m}**" for m in rm.members) + ".")
    if rm.no_dag:
        out += ["", "> ⚠ No built DAG yet (excluded from analysis): "
                + ", ".join(f"`{m}`" for m in rm.no_dag) + "."]

    out += ["", "## Leverage — work here to advance the most", ""]
    if rm.leverage:
        for lev in rm.leverage:
            out.append(f"- **{lev.member}** unblocks {lev.unblock_count} "
                       f"declaration(s) across {len(lev.consumers)} project(s): "
                       + ", ".join(lev.consumers))
    else:
        out.append("- No member currently satisfies anything another still needs.")

    out += ["", "## Unblock matrix — who can advance whom now", ""]
    if rm.unblock:
        for e in rm.unblock:
            out.append(f"- **{e.provider} → {e.consumer}**: {_trunc(e.names)}")
    else:
        out.append("- Nothing to unblock across the scope right now.")

    out += ["", "## Duplicated work — extraction candidates", ""]
    if rm.duplication:
        out.append("Declarations still open in more than one project — candidates "
                   "to do once and share (`archon extract` into a base all peer-read):")
        out.append("")
        for d in rm.duplication:
            tail = (f" — already satisfied in {', '.join(d.satisfied_by)}"
                    if d.satisfied_by else "")
            out.append(f"- **`{d.lean_name}`** — open in {', '.join(d.needed_by)}{tail}")
    else:
        out.append("- No declaration is being worked in parallel across projects.")

    if rm.stalled:
        out += ["", "## Stalled — fully unblockable by peers today", ""]
        out.append("Every open declaration these need is already satisfied "
                   "elsewhere in the scope — reuse before re-deriving:")
        out.append("")
        for n in rm.stalled:
            out.append(f"- **{n}**")

    out.append("")
    return "\n".join(out)
