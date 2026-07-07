"""Build the union *merge DAG* across a scope's member projects.

Each member has its own cached ``.leandag/dag.json`` (read-only — never
rebuilt here). The merge DAG unions them by **fully-qualified Lean name**: one
merge node per distinct ``lean_name``, carrying which members declare it, which
have it satisfied (proved locally or backed by Mathlib), and which still need
it. That provenance is the substrate the roadmap reasons over (unblock matrix,
duplication) and the dashboard renders as a constellation view.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from archon.commands.tooling import peers as peers_mod


@dataclass
class MemberView:
    """One member's name → Lean-name sets, read from its cached DAG."""
    name: str
    path: str
    has_dag: bool
    declared: set[str] = field(default_factory=set)
    available: set[str] = field(default_factory=set)   # proved or mathlib-backed
    needed: set[str] = field(default_factory=set)       # still open


@dataclass
class MergeNode:
    """One declaration in the union, with its cross-project provenance."""
    lean_name: str
    declared_by: list[str]
    satisfied_by: list[str]   # members where it is proved/mathlib-backed
    needed_by: list[str]      # members where it is still open
    shared: bool              # declared by >1 member


@dataclass
class MergeDag:
    members: list[MemberView]
    nodes: list[MergeNode]

    def to_dict(self) -> dict:
        return {
            "members": [
                {
                    "name": m.name,
                    "path": m.path,
                    "has_dag": m.has_dag,
                    "declared": sorted(m.declared),
                    "available": sorted(m.available),
                    "needed": sorted(m.needed),
                }
                for m in self.members
            ],
            "nodes": [asdict(n) for n in self.nodes],
        }


def member_view(peer: peers_mod.Peer) -> MemberView:
    """Read one member's cached DAG into name sets (read-only; no rebuild)."""
    dag = peers_mod.read_peer_dag(peer.path) if peer.has_dag else None
    if not dag:
        return MemberView(name=peer.name, path=peer.path, has_dag=peer.has_dag)
    return MemberView(
        name=peer.name,
        path=peer.path,
        has_dag=peer.has_dag,
        declared=peers_mod.declared_lean_names(dag),
        available=peers_mod.available_lean_names(dag),
        needed=peers_mod.needed_lean_names(dag),
    )


def build_merge_dag(peers: list[peers_mod.Peer]) -> MergeDag:
    """Union member DAGs by Lean name into a :class:`MergeDag`."""
    members = [member_view(p) for p in peers]

    all_names: set[str] = set()
    for m in members:
        all_names |= m.declared

    nodes: list[MergeNode] = []
    for name in sorted(all_names):
        declared_by = [m.name for m in members if name in m.declared]
        satisfied_by = [m.name for m in members if name in m.available]
        needed_by = [m.name for m in members if name in m.needed]
        nodes.append(MergeNode(
            lean_name=name,
            declared_by=declared_by,
            satisfied_by=satisfied_by,
            needed_by=needed_by,
            shared=len(declared_by) > 1,
        ))
    return MergeDag(members=members, nodes=nodes)
