"""Per-file merge agent for multi-lane prover outputs.

When more than one lane finishes a file cleanly (no `sorry`, no new
axioms, builds), the merger picks the best proof per declaration and
writes a single merged file back to the main project tree.

Why a Claude agent instead of a deterministic ranker:
  Provers don't introduce new declarations — the file structure is
  fixed across lanes. Each lane only fills in proofs of declarations
  that already existed. So merging is "for each lemma, pick the
  cleanest implementation" — and judging "cleanest" needs the same
  judgment that wrote the proofs in the first place. A Claude agent
  with the lane outputs side-by-side does this naturally.

The merger is single-shot per file: one prompt, one response, one
file written. Because every input version already compiles, the
merger only has to preserve one consistent set of declarations —
it can't accidentally produce a non-compiling file unless it
hallucinates new code.

When only ONE lane succeeded for a file, the merger is bypassed and
the lane's output is copied verbatim. That preserves the
"first-clean-wins" semantics for the single-lane case while letting
multi-lane runs benefit from per-declaration cherry-picking.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend, DEFAULT_MODEL


@dataclass
class LaneCandidate:
    """One lane's clean version of a target file."""
    lane_id: str
    source_path: Path  # absolute path to the lane's version of the file


@dataclass
class MergeOutcome:
    target_rel: str
    candidates: list[LaneCandidate]
    chosen_lane: str | None  # populated only when len(candidates) == 1
    merged: bool             # True if the merger agent ran; False for single-candidate copy
    log_path: Path | None    # JSONL log of the merger session, if it ran


def _build_merge_prompt(
    *,
    project_path: Path,
    target_rel: str,
    candidates: list[LaneCandidate],
    state_dir: Path,
) -> str:
    listing = "\n".join(
        f"  - lane {c.lane_id}: {c.source_path}"
        for c in candidates
    )
    return dedent(f"""\
        You are the multi-lane merge agent for project at {project_path}.

        Target file (the file you must produce on disk):
          {project_path / target_rel}

        You are given {len(candidates)} candidate versions of this file
        from different prover lanes. Each candidate already compiles on
        its own. Your job is to produce a single merged version that
        is at least as good as any individual candidate — for every
        declaration (theorem/lemma/def/instance), pick the cleanest
        proof from the available lanes and inline it.

        Candidates:
        {listing}

        Rules:
        1. Write the merged file in place at the target path above
           (overwrite whatever is there).
        2. Do NOT introduce declarations that were not present in any
           candidate. The set of declarations across candidates is
           identical — provers fill in proofs, they do not add new
           lemmas. If you ever feel tempted to invent a declaration,
           stop and re-read the candidates.
        3. For each declaration, prefer (in order):
             a. A proof with no `sorry` and no new axioms,
             b. A proof with a partial `sorry` that has clearly made
                more progress (more tactic steps, smaller remaining
                goal),
             c. A bare `sorry` only as a last resort.
        4. Imports, comments, namespace blocks, and section structure
           should match the candidates' shared structure. If candidates
           disagree on imports, take the union of imports.
        5. After writing the file, do not run `lake build` or any other
           verifier — the surrounding loop will rebuild and verify.

        Read {state_dir}/prompts/lane-merge.md for the full instruction
        set, then produce the merged file.""")


def merge_file_versions(
    *,
    project_path: Path,
    state_dir: Path,
    target_rel: str,
    candidates: list[LaneCandidate],
    iteration: int,
    model: str = DEFAULT_MODEL,
    verbose_logs: bool = False,
    backend: ClaudeBackend | None = None,
) -> MergeOutcome:
    """Produce a merged version of ``target_rel`` from lane candidates.

    With one candidate this is a verbatim copy; with two or more, it
    invokes the merge agent. Either way, the resulting file lives at
    ``project_path / target_rel`` when the call returns.
    """
    target_abs = (project_path / target_rel).resolve()
    target_abs.parent.mkdir(parents=True, exist_ok=True)

    if len(candidates) == 0:
        raise ValueError(f'No candidates to merge for {target_rel!r}')

    if len(candidates) == 1:
        only = candidates[0]
        log.step(f"  merge[{target_rel}]: single candidate (lane {only.lane_id}) — direct copy")
        shutil.copy2(only.source_path, target_abs)
        return MergeOutcome(
            target_rel=target_rel,
            candidates=candidates,
            chosen_lane=only.lane_id,
            merged=False,
            log_path=None,
        )

    log.step(
        f"  merge[{target_rel}]: {len(candidates)} candidates "
        f"({', '.join(c.lane_id for c in candidates)}) — invoking merge agent"
    )

    log_dir = state_dir / 'multilane' / 'runtime' / f'iter-{iteration:03d}' / 'merges'
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_slug = target_rel.replace('/', '_').removesuffix('.lean')
    log_base = log_dir / safe_slug

    prompt = _build_merge_prompt(
        project_path=project_path,
        target_rel=target_rel,
        candidates=candidates,
        state_dir=state_dir,
    )
    agent = ClaudeAgent(model=model, role='merge', backend=backend or ClaudeBackend())
    ok = agent.run(prompt, cwd=project_path, log_base=log_base, verbose_logs=verbose_logs)

    if not ok:
        # Fallback: pick the first candidate verbatim so the loop still
        # makes forward progress. The lane that ran first cleanly is by
        # convention the most-trusted one.
        log.warn(f"  merge[{target_rel}]: agent failed — falling back to first candidate ({candidates[0].lane_id})")
        shutil.copy2(candidates[0].source_path, target_abs)
        return MergeOutcome(
            target_rel=target_rel,
            candidates=candidates,
            chosen_lane=candidates[0].lane_id,
            merged=False,
            log_path=Path(f"{log_base}.jsonl"),
        )

    return MergeOutcome(
        target_rel=target_rel,
        candidates=candidates,
        chosen_lane=None,
        merged=True,
        log_path=Path(f"{log_base}.jsonl"),
    )
