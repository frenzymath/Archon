/**
 * GitTimeline — horizontal git-history rail with one lane per branch.
 *
 * Extracted from ProofGraph so multiple views (the proof graph and the DAG
 * page) share the exact same commit timeline: left = oldest, right = newest;
 * click a commit to time-travel; hover for a metadata tooltip; a "+" button
 * hints how to fork a branch. Pure rendering — the parent owns selection and
 * decides what selecting a commit means.
 */
import { useMemo, useRef, useState } from 'react';
import { type GitCommit } from '../hooks/useGitLog';
import styles from './GitTimeline.module.css';

// Branch colors (matches file-group palettes but fully opaque for git tree)
const BRANCH_COLORS = ['#0366d6', '#6f42c1', '#e36209', '#28a745', '#cb2431', '#00868a', '#b08800', '#d73a49'];

const LANE_H = 28;
const COMMIT_R = 5;
const PAD_X = 64; // space for branch labels on left
const PAD_Y = 16;
const MIN_SPACING = 52;
const PLUS_GAP = 52;          // reserved px at the right edge for the "+" button
const PLUS_BTN_W = 18;        // width of the "+" button itself
const PLUS_OFFSET_MAX = PLUS_GAP - PLUS_BTN_W - 6; // leaves 6 px clear of the edge
const TEXT_BLEED = 26;        // how far a centred SHA label can stick past its node

interface CommitPos { commit: GitCommit; x: number; y: number; lane: number; }

// Order branches so a branch sits adjacent to (and below) the branch it
// forked from, with active branches closer to their parent than dead ones.
//
// One-lane-per-branch layouts can never eliminate every crossing — a
// merge edge from a deeply-nested branch back to main always sweeps
// across whatever sits between them — so this is a heuristic, not a
// solver. The goal is to make the common cases clean:
//
//   1. For each branch, find its fork parent: the parent (on a
//      different branch) of this branch's earliest commit. Branches
//      with no such parent are "roots".
//   2. For each (parent, child) pair, count how many merge edges
//      cross between them. Pairs with merge edges get treated as a
//      stronger family bond.
//   3. DFS the fork tree. When ordering siblings under a parent,
//      sort by:
//         - has merge edges back to parent (yes first → adjacent),
//         - last-activity time (newest first → live branches before
//           dead ones),
//         - fork time (earliest first → stable tiebreaker),
//         - name.
function computeBranchOrder(ordered: GitCommit[]): string[] {
  const shaToCommit = new Map(ordered.map(c => [c.sha, c] as const));
  const allBranches = new Set<string>();
  for (const c of ordered) allBranches.add(c.branch ?? 'main');

  type Stats = {
    parentBranch: string | null;
    forkOrder: number;
    lastIdx: number;
    mergeBackToParent: boolean;
  };
  const stats = new Map<string, Stats>();

  for (const b of allBranches) {
    const earliestIdx = ordered.findIndex(c => (c.branch ?? 'main') === b);
    if (earliestIdx < 0) continue;
    let lastIdx = earliestIdx;
    for (let i = ordered.length - 1; i >= earliestIdx; i--) {
      if ((ordered[i].branch ?? 'main') === b) { lastIdx = i; break; }
    }
    const earliest = ordered[earliestIdx];
    const parentOnOther = earliest.parents
      .map(p => shaToCommit.get(p))
      .find(p => p && (p.branch ?? 'main') !== b) ?? null;
    stats.set(b, {
      parentBranch: parentOnOther ? (parentOnOther.branch ?? 'main') : null,
      forkOrder: earliestIdx,
      lastIdx,
      mergeBackToParent: false,  // populated in next pass
    });
  }

  // A merge "back to parent" is any commit on the parent branch with
  // a parent SHA that lives on the child branch. Also true symmetrically
  // for cross-merges between siblings — we only flag the parent direction
  // here because it's the ordering signal we need.
  for (const c of ordered) {
    const cBranch = c.branch ?? 'main';
    for (const pSha of c.parents) {
      const p = shaToCommit.get(pSha);
      if (!p) continue;
      const pBranch = p.branch ?? 'main';
      if (pBranch === cBranch) continue;
      // c is on the parent branch and merges in something from pBranch
      // (the would-be child). Mark the child as merging back.
      const childInfo = stats.get(pBranch);
      if (childInfo && childInfo.parentBranch === cBranch) {
        childInfo.mergeBackToParent = true;
      }
    }
  }

  // parentBranch -> ordered list of child branches.
  const children = new Map<string, string[]>();
  for (const [b, info] of stats) {
    if (!info.parentBranch) continue;
    const arr = children.get(info.parentBranch) ?? [];
    arr.push(b);
    children.set(info.parentBranch, arr);
  }
  function childCmp(a: string, b: string): number {
    const sa = stats.get(a)!, sb = stats.get(b)!;
    if (sa.mergeBackToParent !== sb.mergeBackToParent) {
      return sa.mergeBackToParent ? -1 : 1;
    }
    if (sa.lastIdx !== sb.lastIdx) return sb.lastIdx - sa.lastIdx;  // newest first
    if (sa.forkOrder !== sb.forkOrder) return sa.forkOrder - sb.forkOrder;
    return a.localeCompare(b);
  }
  for (const arr of children.values()) arr.sort(childCmp);

  // Roots: branches with no fork parent. main is pinned to lane 0;
  // other roots fall in by recency.
  const roots = Array.from(allBranches)
    .filter(b => !stats.get(b)?.parentBranch)
    .sort((a, b) => {
      if (a === 'main') return -1;
      if (b === 'main') return 1;
      const sa = stats.get(a)!, sb = stats.get(b)!;
      if (sa.lastIdx !== sb.lastIdx) return sb.lastIdx - sa.lastIdx;
      if (sa.forkOrder !== sb.forkOrder) return sa.forkOrder - sb.forkOrder;
      return a.localeCompare(b);
    });

  const result: string[] = [];
  const visited = new Set<string>();
  function dfs(b: string) {
    if (visited.has(b)) return;
    visited.add(b);
    result.push(b);
    for (const k of children.get(b) ?? []) dfs(k);
  }
  for (const r of roots) dfs(r);
  for (const b of allBranches) if (!visited.has(b)) dfs(b);
  return result;
}

function computeGitLayout(commits: GitCommit[], containerW: number) {
  // Display left=oldest, right=newest: reverse the newest-first topo-order array
  const ordered = [...commits].reverse();

  const branchOrder = computeBranchOrder(ordered);

  const N = ordered.length;
  // Reserve room for branch labels on the left (PAD_X), the "+" button on the
  // right (PLUS_GAP), and half an SHA label's width on each side so the first
  // and last commits' centred labels don't spill past the container edges.
  const available = Math.max(0, containerW - PAD_X - PLUS_GAP - TEXT_BLEED);
  const spacing = N > 1
    ? Math.max(MIN_SPACING, available / (N - 1))
    : Math.max(MIN_SPACING, available);
  const firstX = PAD_X + TEXT_BLEED / 2;          // shift right so leftmost label fits
  const singleX = N === 1 ? firstX + available / 2 : 0;
  const lastNodeX = N === 1 ? singleX : firstX + (N - 1) * spacing;
  // svgW must cover everything we draw: nodes, labels, and "+" button. Anything
  // beyond containerW triggers horizontal scroll in .gitScroll.
  const svgW = Math.max(containerW, lastNodeX + PLUS_GAP + 4);
  const svgH = PAD_Y * 2 + branchOrder.length * LANE_H;

  const nodes: CommitPos[] = ordered.map((c, i) => {
    const lane = branchOrder.indexOf(c.branch ?? 'main');
    const x = N === 1 ? singleX : firstX + i * spacing;
    return { commit: c, x, y: PAD_Y + lane * LANE_H, lane };
  });
  const shaToPos = new Map(nodes.map(n => [n.commit.sha, n]));

  return { ordered, nodes, branchOrder, shaToPos, svgW, svgH, spacing };
}

interface TooltipState {
  commit: GitCommit;
  // position relative to the scrollable container (accounts for scroll offset)
  left: number;
  top: number;
}

export function GitTimeline({
  commits,
  selectedSha,
  onSelect,
  containerW,
}: {
  commits: GitCommit[];
  selectedSha: string;
  onSelect: (c: GitCommit) => void;
  containerW: number;
}) {
  // All hooks MUST run on every render (no hooks after the commits.length guard).
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);
  const [branchTooltip, setBranchTooltip] = useState<{ label: string; left: number; top: number } | null>(null);
  const [showBranchHint, setShowBranchHint] = useState(false);
  const [branchHintPos, setBranchHintPos] = useState<{ left: number; top: number } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { nodes, branchOrder, shaToPos, svgW, svgH, spacing } = useMemo(
    () => computeGitLayout(commits, containerW),
    [commits, containerW],
  );

  if (!commits.length) {
    return (
      <div className={styles.gitEmpty}>
        No commits yet. Run an archon command to start.
      </div>
    );
  }

  const lastNode = nodes[nodes.length - 1];
  // Keep the "+" button fully inside the PLUS_GAP reserve — otherwise it ends
  // up past the container's right edge or gets cut when the SVG is narrower
  // than the drawn bounds.
  const plusX = lastNode
    ? lastNode.x + Math.min(spacing * 0.4, PLUS_OFFSET_MAX)
    : PAD_X + 20;
  const plusY = PAD_Y - 8;

  // Convert SVG-local coordinates to container-relative for tooltip placement
  function svgToContainer(svgX: number, svgY: number) {
    const scroll = scrollRef.current?.scrollLeft ?? 0;
    return { left: svgX - scroll, top: svgY };
  }

  return (
    <div ref={scrollRef} className={styles.gitScroll}>
      <svg width={svgW} height={svgH} style={{ display: 'block', minWidth: svgW }}>

        {/* Branch lane labels — full label in SVG title for native tooltip fallback */}
        {branchOrder.map((b, i) => {
          const y = PAD_Y + i * LANE_H;
          const col = BRANCH_COLORS[i % BRANCH_COLORS.length];
          const lastOnBranch = [...nodes].reverse().find(n => n.lane === i);
          const lineEndX = lastOnBranch ? lastOnBranch.x : PAD_X;
          const display = b.length > 10 ? b.slice(0, 9) + '…' : b;
          return (
            <g key={b}
              onMouseEnter={() => {
                if (b.length <= 10) return;
                const pos = svgToContainer(PAD_X - 4, y - 22);
                setBranchTooltip({ label: b, left: pos.left, top: pos.top });
              }}
              onMouseLeave={() => setBranchTooltip(null)}
            >
              <title>{b}</title>
              <line x1={PAD_X} y1={y} x2={lineEndX} y2={y}
                stroke={col} strokeWidth={1.5} strokeDasharray="4 3" opacity={0.35} />
              <text x={PAD_X - 6} y={y + 4} fontSize={9} fill={col}
                textAnchor="end" fontFamily="var(--font-mono)" fontWeight={600} paintOrder="stroke fill">
                {display}
              </text>
            </g>
          );
        })}

        {/* Edges: child → parent */}
        {nodes.map(n => n.commit.parents.map(pSha => {
          const pNode = shaToPos.get(pSha);
          if (!pNode) return null;
          const col = BRANCH_COLORS[n.lane % BRANCH_COLORS.length];
          const sameLane = n.lane === pNode.lane;
          if (sameLane) {
            return <line key={`${n.commit.sha}-${pSha}`}
              x1={n.x} y1={n.y} x2={pNode.x} y2={pNode.y}
              stroke={col} strokeWidth={1.5} opacity={0.55} />;
          }
          // Cross-lane: cubic bezier
          const mx = (n.x + pNode.x) / 2;
          return <path key={`${n.commit.sha}-${pSha}`}
            d={`M${n.x},${n.y} C${mx},${n.y} ${mx},${pNode.y} ${pNode.x},${pNode.y}`}
            fill="none" stroke={col} strokeWidth={1.5} opacity={0.55} />;
        }))}

        {/* Commit nodes */}
        {nodes.map(n => {
          const c = n.commit;
          const col = BRANCH_COLORS[n.lane % BRANCH_COLORS.length];
          const isSel = c.sha === selectedSha;
          const isPhaseEnd = !!c.phase && !c.fileSlug;
          return (
            <g key={c.sha} style={{ cursor: 'pointer' }}
              onClick={() => onSelect(c)}
              onMouseEnter={() => {
                const pos = svgToContainer(n.x, n.y);
                setTooltip({ commit: c, left: pos.left, top: pos.top });
              }}
              onMouseLeave={() => setTooltip(null)}
            >
              {isSel && <circle cx={n.x} cy={n.y} r={COMMIT_R + 4}
                fill="none" stroke="var(--blue)" strokeWidth={1.5} opacity={0.4} />}
              <circle cx={n.x} cy={n.y} r={COMMIT_R}
                fill={isSel ? 'var(--blue)' : col}
                stroke={isSel ? 'white' : 'var(--bg-primary)'}
                strokeWidth={isSel ? 2 : 1.5} />
              {c.iteration && (
                <text x={n.x} y={n.y - COMMIT_R - 3}
                  fontSize={8} fill="var(--text-primary)"
                  textAnchor="middle" fontFamily="var(--font-mono)" fontWeight={600} paintOrder="stroke fill">
                  {c.iteration.replace('iter-', '#')}
                </text>
              )}
              {c.shortSha && !isPhaseEnd && (
                <text x={n.x} y={n.y + COMMIT_R + 9}
                  fontSize={7} fill={isSel ? 'var(--blue)' : 'var(--text-muted)'}
                  textAnchor="middle" fontFamily="var(--font-mono)" paintOrder="stroke fill">
                  {c.shortSha}
                </text>
              )}
            </g>
          );
        })}

        {/* "+" new branch button */}
        <g style={{ cursor: 'pointer' }}
          onMouseEnter={() => {
            const pos = svgToContainer(plusX + 10, plusY + 20);
            setBranchHintPos(pos);
            setShowBranchHint(true);
          }}
          onMouseLeave={() => setShowBranchHint(false)}
        >
          <rect x={plusX - 8} y={plusY} width={18} height={18} rx={4}
            fill="var(--bg-tertiary)" stroke="var(--border)" strokeWidth={1} />
          <text x={plusX + 1} y={plusY + 13} fontSize={13} fill="var(--text-muted)"
            textAnchor="middle" fontWeight={300} paintOrder="stroke fill">+</text>
        </g>
      </svg>

      {/* Commit tooltip — rendered OUTSIDE svg to avoid panel clipping. */}
      {tooltip && (
        <div className={styles.gitTooltip} style={{
          left: Math.min(tooltip.left + 10, containerW - 238),
          top: Math.max(4, tooltip.top - 82),
          pointerEvents: 'none',
        }}>
          <div className={styles.gitTooltipSha}>{tooltip.commit.shortSha} · {tooltip.commit.branch}</div>
          <div className={styles.gitTooltipMsg}>
            {tooltip.commit.subject.length > 62 ? tooltip.commit.subject.slice(0, 61) + '…' : tooltip.commit.subject}
          </div>
          <div className={styles.gitTooltipHint}>Fork a new branch here:</div>
          <div className={styles.gitTooltipCmd}>archon branch at-{tooltip.commit.shortSha} . --from {tooltip.commit.shortSha}</div>
        </div>
      )}

      {/* Full branch name tooltip */}
      {branchTooltip && (
        <div className={styles.gitTooltip} style={{ left: Math.max(4, branchTooltip.left), top: branchTooltip.top }}>
          <div className={styles.gitTooltipCmd}>{branchTooltip.label}</div>
        </div>
      )}

      {/* New branch hint — positioned right next to the "+" button */}
      {showBranchHint && branchHintPos && (
        <div className={styles.gitTooltip} style={{
          left: Math.min(Math.max(4, branchHintPos.left), containerW - 200),
          top: branchHintPos.top,
        }}>
          <div className={styles.gitTooltipHint}>To create a new branch:</div>
          <div className={styles.gitTooltipCmd}>archon branch &lt;name&gt;</div>
        </div>
      )}
    </div>
  );
}
