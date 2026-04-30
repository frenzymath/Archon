/**
 * ProofGraph v7
 *
 * v7 additions:
 *   - Git tree at the bottom replacing the bar-chart timeline
 *     Left = oldest commit, right = newest. One lane per branch.
 *     Click a commit to time-travel; hover for metadata tooltip;
 *     "+" button at right with branch-creation hint.
 *   - Resizable right sidebar and bottom git-tree panel (drag handles).
 *   - Blueprint LaTeX section in sidebar.
 *   - Phase logs (plan, refactor, review) shown in sidebar for non-prover commits.
 *   - Legacy "refactor" logs are now reachable by clicking the corresponding commit.
 */
import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import {
  useProofGraphDeclarations, useProofGraphTimeline, useProofGraphSnapshot, useProofGraphNodeDetail,
  useProofGraphLogs,
  type GraphDeclaration, type DeclarationsResponse, type ProverLogEntry, type LogStats,
} from '../hooks/useProofGraph';
import {
  useGitLog, usePhaseLogs, useBlueprint,
  type GitCommit,
} from '../hooks/useGitLog';
import { STATUS_COLORS } from '../utils/constants';
import AttemptCard from '../components/AttemptCard';
import LeanCodeLine from '../components/LeanCodeLine';
import BlueprintRendered from '../components/BlueprintRendered';
import { highlightLeanLines } from '../utils/leanHighlight';
import styles from './ProofGraph.module.css';

const C_GREEN = '#28a745', C_ORANGE = '#e36209', C_RED = '#cb2431';
function ncolor(sorry: boolean, touched: boolean) { return sorry ? (touched ? C_ORANGE : C_RED) : C_GREEN; }
function basename(p: string) { const i = p.lastIndexOf('/'); return i >= 0 ? p.slice(i + 1) : p; }

// Branch colors (matches file-group palettes but fully opaque for git tree)
const BRANCH_COLORS = ['#0366d6', '#6f42c1', '#e36209', '#28a745', '#cb2431', '#00868a', '#b08800', '#d73a49'];

// ── Layout ───────────────────────────────────────────────────────────

interface LN { id: string; d: GraphDeclaration; x: number; y: number; w: number; h: number; c: string; t: boolean; }
interface LG { file: string; label: string; x: number; y: number; w: number; h: number; ci: number; }
interface LE { from: LN; to: LN; blocked: boolean; }

const NW = 170, NH = 42, NG = 8, GP = 14, GH = 20, GG = 22;
const BG = ['rgba(3,102,214,0.06)','rgba(111,66,193,0.06)','rgba(227,98,9,0.06)','rgba(40,167,69,0.06)','rgba(203,36,49,0.06)','rgba(0,134,114,0.06)'];
const BS = ['rgba(3,102,214,0.22)','rgba(111,66,193,0.22)','rgba(227,98,9,0.22)','rgba(40,167,69,0.22)','rgba(203,36,49,0.22)','rgba(0,134,114,0.22)'];

function doLayout(decls: GraphDeclaration[], edgeList: { from: string; to: string }[], files: { file: string }[], changed: Set<string>) {
  const nm = new Map<string, LN>(); const gs: LG[] = [];
  const af = files.filter(f => decls.some(d => d.file === f.file));
  if (!af.length) return { n: [] as LN[], g: gs, e: [] as LE[], w: 400, h: 400 };
  const cols = Math.max(1, Math.round(Math.sqrt(af.length * 1.3)));
  let gx = GG, gy = GG, col = 0, rowH = 0;
  for (let fi = 0; fi < af.length; fi++) {
    const fd = decls.filter(d => d.file === af[fi].file); if (!fd.length) continue;
    const ic = fd.length > 6 ? 2 : 1, pc = Math.ceil(fd.length / ic);
    const gw = ic * NW + (ic - 1) * NG + GP * 2, gh = GH + pc * (NH + NG) - NG + GP;
    for (let di = 0; di < fd.length; di++) {
      const d = fd[di], c = Math.floor(di / pc), r = di % pc;
      const x = gx + GP + c * (NW + NG), y = gy + GH + r * (NH + NG);
      const t = changed.has(d.id);
      nm.set(d.id, { id: d.id, d, x, y, w: NW, h: NH, c: ncolor(d.hasSorry, t), t });
    }
    gs.push({ file: af[fi].file, label: basename(af[fi].file), x: gx, y: gy, w: gw, h: gh, ci: fi % BG.length });
    rowH = Math.max(rowH, gh); col++;
    if (col >= cols) { col = 0; gx = GG; gy += rowH + GG; rowH = 0; } else { gx += gw + GG; }
  }
  const es: LE[] = [];
  for (const e of edgeList) { const f = nm.get(e.from), t = nm.get(e.to); if (f && t) es.push({ from: f, to: t, blocked: t.d.hasSorry }); }
  return { n: Array.from(nm.values()), g: gs, e: es, w: Math.max(...gs.map(g => g.x + g.w), 400) + GG, h: Math.max(...gs.map(g => g.y + g.h), 400) + GG };
}

// ── ViewBox zoom/pan ─────────────────────────────────────────────────

function useViewBox(cw: number, ch: number) {
  const svgRef = useRef<SVGSVGElement>(null);
  const cRef = useRef<HTMLDivElement>(null);
  const [vb, setVb] = useState<[number, number, number, number]>([0, 0, 800, 600]);
  const drag = useRef(false);
  const last = useRef({ x: 0, y: 0 });

  useEffect(() => {
    const el = cRef.current;
    if (!el || cw <= 0 || ch <= 0) return;
    const r = el.getBoundingClientRect();
    const s = Math.max(cw / r.width, ch / r.height) / 0.92;
    const vw = r.width * s, vh = r.height * s;
    setVb([cw / 2 - vw / 2, ch / 2 - vh / 2, vw, vh]);
  }, [cw, ch]);

  useEffect(() => {
    const el = cRef.current; if (!el) return;
    const MIN = 0.2, MAX = 5;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault(); e.stopPropagation();
      const rect = el.getBoundingClientRect();
      const fx = (e.clientX - rect.left) / rect.width, fy = (e.clientY - rect.top) / rect.height;
      setVb(([vx, vy, vw, vh]) => {
        if (e.ctrlKey || e.metaKey) {
          const factor = Math.pow(1.01, e.deltaY);
          const nw = Math.max(cw * MIN, Math.min(cw * MAX, vw * factor));
          const nh = Math.max(ch * MIN, Math.min(ch * MAX, vh * factor));
          return [vx + (vw - nw) * fx, vy + (vh - nh) * fy, nw, nh];
        } else {
          const sx = vw / rect.width, sy = vh / rect.height;
          return [vx + e.deltaX * sx, vy + e.deltaY * sy, vw, vh];
        }
      });
    };
    const onDown = (e: MouseEvent) => {
      if (e.button !== 0 || (e.target as HTMLElement).closest('[data-node]')) return;
      drag.current = true; last.current = { x: e.clientX, y: e.clientY }; el.style.cursor = 'grabbing'; e.preventDefault();
    };
    const onMove = (e: MouseEvent) => {
      if (!drag.current) return;
      const dx = e.clientX - last.current.x, dy = e.clientY - last.current.y;
      last.current = { x: e.clientX, y: e.clientY };
      setVb(([vx, vy, vw, vh]) => { const r = cRef.current?.getBoundingClientRect(); if (!r) return [vx, vy, vw, vh]; return [vx - dx * vw / r.width, vy - dy * vh / r.height, vw, vh]; });
    };
    const onUp = () => { drag.current = false; el.style.cursor = 'grab'; };
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { el.removeEventListener('wheel', onWheel); el.removeEventListener('mousedown', onDown); window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [cw, ch]);

  const zoomBy = useCallback((f: number) => {
    setVb(([vx, vy, vw, vh]) => { const nw = Math.max(cw * 0.2, Math.min(cw * 5, vw * f)), nh = Math.max(ch * 0.2, Math.min(ch * 5, vh * f)); return [vx + (vw - nw) / 2, vy + (vh - nh) / 2, nw, nh]; });
  }, [cw, ch]);
  const reset = useCallback(() => {
    const el = cRef.current; if (!el || cw <= 0 || ch <= 0) return;
    const r = el.getBoundingClientRect(); const s = Math.max(cw / r.width, ch / r.height) / 0.92;
    const vw = r.width * s, vh = r.height * s; setVb([cw / 2 - vw / 2, ch / 2 - vh / 2, vw, vh]);
  }, [cw, ch]);
  const scale = cw > 0 && vb[2] > 0 ? cw / vb[2] : 1;
  return { svgRef, cRef, vb, zoomIn: () => zoomBy(0.7), zoomOut: () => zoomBy(1.4), reset, scale };
}

// ── Sparkline ────────────────────────────────────────────────────────

function Spark({ data, ai, w = 280, h = 34 }: { data: number[]; ai?: number; w?: number; h?: number }) {
  if (data.length < 2) return null;
  const mx = Math.max(...data, 1), sx = w / (data.length - 1);
  const pts = data.map((v, i) => `${i * sx},${h - (v / mx) * (h - 4)}`).join(' ');
  return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: 'block' }}>
    <polygon points={`0,${h} ${pts} ${(data.length - 1) * sx},${h}`} fill="rgba(3,102,214,0.08)" />
    <polyline points={pts} fill="none" stroke="var(--blue)" strokeWidth="1.5" />
    {data.map((v, i) => <circle key={i} cx={i * sx} cy={h - (v / mx) * (h - 4)} r={i === ai ? 4 : 2} fill={v === 0 ? C_GREEN : i === ai ? '#0366d6' : 'var(--blue)'} stroke={i === ai ? 'white' : 'none'} strokeWidth={1.5} />)}
  </svg>;
}

// ── Agent Log Entry ─────────────────────────────────────────────────

const EVT_ICONS: Record<string, string> = {
  thinking: '💭', text: '💬', tool_call: '🔧', tool_result: '📋', code_snapshot: '📸', session_end: '🏁',
};
const EVT_COLORS: Record<string, string> = {
  thinking: 'rgba(111,66,193,0.08)', text: 'rgba(3,102,214,0.06)', tool_call: 'rgba(227,98,9,0.06)',
  tool_result: 'rgba(40,167,69,0.06)', code_snapshot: 'rgba(0,134,114,0.06)', session_end: 'rgba(203,36,49,0.06)',
};

function formatTime(ts: string) {
  try { const d = new Date(ts); return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return ''; }
}
function formatDuration(ms: number) {
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60000), s = Math.round((ms % 60000) / 1000);
  return `${m}m ${s}s`;
}

function LogEntry({ entry, defaultOpen }: { entry: ProverLogEntry; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const icon = EVT_ICONS[entry.event] || '•';
  const bg = EVT_COLORS[entry.event] || 'transparent';
  let label: string = entry.event;
  if (entry.event === 'tool_call' && entry.tool) label = `tool: ${entry.tool}`;
  if (entry.event === 'tool_result') label = 'result';
  if (entry.event === 'code_snapshot') label = `snapshot step ${entry.step ?? '?'}`;
  const hasContent = !!(entry.content || entry.input || entry.summary);
  return (
    <div className={styles.logEntry} style={{ background: bg }}>
      <div className={styles.logHead} onClick={() => hasContent && setOpen(!open)} style={{ cursor: hasContent ? 'pointer' : 'default' }}>
        <span className={styles.logIcon}>{icon}</span>
        <span className={styles.logLabel}>{label}</span>
        <span className={styles.logTs}>{formatTime(entry.ts)}</span>
        {hasContent && <span className={styles.logToggle}>{open ? '▾' : '▸'}</span>}
      </div>
      {open && hasContent && (
        <div className={styles.logBody}>
          {entry.content && <pre className={styles.logPre}>{entry.content}</pre>}
          {entry.input && <pre className={styles.logPre}>{typeof entry.input === 'string' ? entry.input : JSON.stringify(entry.input, null, 2)}</pre>}
          {entry.summary && <pre className={styles.logPre}>{entry.summary}</pre>}
        </div>
      )}
    </div>
  );
}

function SessionStats({ stats }: { stats: LogStats }) {
  const parts: string[] = [];
  if (stats.durationMs) parts.push(formatDuration(stats.durationMs));
  if (stats.numTurns) parts.push(`${stats.numTurns} turns`);
  if (stats.toolCallCount) parts.push(`${stats.toolCallCount} tool calls`);
  if (stats.thinkingCount) parts.push(`${stats.thinkingCount} thinking`);
  if (stats.totalCost != null) parts.push(`$${stats.totalCost.toFixed(2)}`);
  if (!parts.length) return null;
  return <div className={styles.logStats}>{parts.join(' · ')}</div>;
}

// ── Blueprint LaTeX section ──────────────────────────────────────────

function BlueprintSection({ file, name }: { file: string; name: string }) {
  const [open, setOpen] = useState(true);
  const [showSource, setShowSource] = useState(false);
  const { data, isFetching } = useBlueprint(file, name);
  if (!data && !isFetching) return null;
  if (!data?.tex) {
    return (
      <div className={styles.codeSection}>
        <div className={styles.codeHeader} style={{ cursor: 'default', color: 'var(--text-muted)' }}>
          Blueprint <span style={{ fontSize: 10, fontWeight: 400 }}>— no \lean{`{${name}}`} found</span>
        </div>
      </div>
    );
  }
  return (
    <div className={styles.codeSection}>
      <div className={styles.codeHeader} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span onClick={() => setOpen(!open)} style={{ cursor: 'pointer', flex: 1 }}>
          {open ? '▾' : '▸'} Blueprint
        </span>
        {open && (
          <span
            onClick={e => { e.stopPropagation(); setShowSource(s => !s); }}
            title={showSource ? 'Show rendered blueprint' : 'Show raw LaTeX source'}
            style={{ cursor: 'pointer', fontSize: 10, color: 'var(--text-muted)', fontWeight: 400 }}
          >
            {showSource ? 'rendered' : 'source'}
          </span>
        )}
      </div>
      {open && (
        showSource
          ? <pre className={styles.texBlock}>{data.tex}</pre>
          : <BlueprintRendered tex={data.tex} macros={data.macros} />
      )}
    </div>
  );
}

// ── Git Tree ─────────────────────────────────────────────────────────

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

function computeGitLayout(commits: GitCommit[], containerW: number) {
  // Display left=oldest, right=newest: reverse the newest-first topo-order array
  const ordered = [...commits].reverse();

  // Collect branches in order of first appearance (oldest-first)
  const branchOrder: string[] = [];
  const seen = new Set<string>();
  for (const c of ordered) {
    const b = c.branch ?? 'main';
    if (!seen.has(b)) { seen.add(b); branchOrder.push(b); }
  }

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

function GitTree({
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

  const { ordered, nodes, branchOrder, shaToPos, svgW, svgH, spacing } = useMemo(
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
              onMouseEnter={e => {
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
                textAnchor="end" fontFamily="var(--font-mono)" fontWeight={600}
                style={{ cursor: b.length > 10 ? 'default' : 'default' }}>
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
              {isPhaseEnd && (
                <text x={n.x} y={n.y - COMMIT_R - 3}
                  fontSize={7} fill="var(--text-muted)"
                  textAnchor="middle" fontFamily="var(--font-mono)">
                  {c.phase}
                </text>
              )}
              {c.shortSha && !isPhaseEnd && (
                <text x={n.x} y={n.y + COMMIT_R + 9}
                  fontSize={7} fill={isSel ? 'var(--blue)' : 'var(--text-muted)'}
                  textAnchor="middle" fontFamily="var(--font-mono)">
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
            textAnchor="middle" fontWeight={300}>+</text>
        </g>
      </svg>

      {/* Commit tooltip — rendered OUTSIDE svg to avoid panel clipping.
          pointerEvents: 'none' so the tooltip never intercepts clicks on
          nearby commit nodes underneath it (previously the tooltip lived
          at y ~= 4 and overlapped lanes 0–2, making those nodes
          unclickable). The branch-creation command below is purely
          informational; users can read it and retype, or click the node
          to select it first and copy from the banner.
      */}
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

// ── Drag-resize hook ─────────────────────────────────────────────────

function useDragResize(initial: number, min: number, max: number, axis: 'x' | 'y') {
  const [size, setSize] = useState(initial);
  const dragging = useRef(false);
  const startRef = useRef({ pos: 0, size: 0 });

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startRef.current = { pos: axis === 'x' ? e.clientX : e.clientY, size };
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = (axis === 'x' ? ev.clientX : ev.clientY) - startRef.current.pos;
      // sidebar: drag left → bigger (negative delta = bigger); bottom: drag up → bigger
      setSize(Math.max(min, Math.min(max, startRef.current.size - delta)));
    };
    const onUp = () => { dragging.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [size, axis, min, max]);

  return { size, onMouseDown };
}

// ── Main ─────────────────────────────────────────────────────────────

export default function ProofGraph() {
  const { data: declData, isLoading } = useProofGraphDeclarations();
  const { data: tlData } = useProofGraphTimeline();
  const { data: gitData } = useGitLog();

  const [selNode, setSelNode] = useState('');
  const [selTl, setSelTl] = useState(-1);
  const [selSha, setSelSha] = useState('');
  const [codeOpen, setCodeOpen] = useState(true);
  const [logsOpen, setLogsOpen] = useState(false);
  const [phaseLogsOpen, setPhaseLogsOpen] = useState(false);

  // Resizable panels
  const sideResize = useDragResize(350, 240, 600, 'x');
  const botResize = useDragResize(120, 60, 280, 'y');

  // Selected git commit (derived from selSha)
  const selCommit = useMemo(
    () => gitData?.commits.find(c => c.sha === selSha) ?? null,
    [gitData, selSha],
  );

  // Timeline index from selected commit
  const selTlFromCommit = useMemo(() => {
    if (!selCommit?.iteration || !tlData) return -1;
    return tlData.findIndex(t => t.iteration === selCommit.iteration);
  }, [selCommit, tlData]);

  const effectiveSelTl = selTl >= 0 ? selTl : selTlFromCommit;

  // selIter: from timeline (for snapshot) or from commit (for logs when no snapshot)
  const selIter = effectiveSelTl >= 0 && tlData
    ? tlData[effectiveSelTl]?.iteration
    : (selCommit?.iteration ?? '');

  const { data: snapData, isFetching: snapLoading } = useProofGraphSnapshot(selIter);

  // While snapData is being fetched, fall back to declData so the graph panel
  // never goes blank when the user clicks a commit. React-query's placeholderData
  // already keeps the previous snapshot around, but the first selection has no
  // previous value.
  const activeData: DeclarationsResponse | undefined = selIter
    ? (snapData ?? declData)
    : declData;

  const changedSet = useMemo(() => {
    const s = new Set<string>();
    if (!tlData?.length) return s;
    const idx = effectiveSelTl >= 0 ? effectiveSelTl : tlData.length - 1;
    const pt = tlData[idx];
    if (pt?.changedDeclarations) for (const id of pt.changedDeclarations) s.add(id);
    return s;
  }, [tlData, effectiveSelTl]);

  const selFile = selNode.split('::')[0] || '', selName = selNode.split('::')[1] || '';
  const { data: nd } = useProofGraphNodeDetail(selFile, selName, selIter || undefined);
  const { data: logData } = useProofGraphLogs(selFile, selIter || undefined);

  // Phase logs for non-prover commits
  const commitPhase = selCommit?.phase;
  const showPhaseLogs = !!selIter && !!commitPhase && commitPhase !== 'prover';
  const { data: phaseLogData } = usePhaseLogs(
    showPhaseLogs ? selIter : undefined,
    showPhaseLogs ? commitPhase : undefined,
  );

  const lo = useMemo(() => activeData ? doLayout(activeData.declarations, activeData.edges, activeData.files, changedSet) : null, [activeData, changedSet]);
  const { svgRef, cRef, vb, zoomIn, zoomOut, reset, scale } = useViewBox(lo?.w ?? 0, lo?.h ?? 0);

  const summary = useMemo(() => {
    if (!lo) return null;
    let s = 0, o = 0, r = 0;
    for (const n of lo.n) { if (!n.d.hasSorry) s++; else if (n.t) o++; else r++; }
    return { s, o, r };
  }, [lo]);

  const spark = useMemo(() => {
    if (!tlData || !selNode) return null;
    const d: number[] = [];
    for (const pt of tlData) {
      const e = pt.perDeclaration[selNode]; if (e) { d.push(e.sorryCount); continue; }
      const f = selNode.split('::')[0], n = selNode.split('::')[1];
      let found = false;
      for (const [k, v] of Object.entries(pt.perDeclaration)) { if (k.split('::')[1] === n && k.startsWith(f)) { d.push(v.sorryCount); found = true; break; } }
      if (!found) d.push(0);
    }
    return d.length > 1 ? d : null;
  }, [tlData, selNode]);

  const codeLines = useMemo(() => nd?.declaration?.body?.split('\n') ?? [], [nd]);
  const hlCode = useMemo(() => highlightLeanLines(codeLines), [codeLines]);

  const clickNode = useCallback((id: string) => {
    setSelNode(p => p === id ? '' : id);
    setCodeOpen(true);
    setLogsOpen(false);
    setPhaseLogsOpen(false);
  }, []);

  const handleCommitClick = useCallback((c: GitCommit) => {
    const isSame = c.sha === selSha;
    setSelSha(isSame ? '' : c.sha);
    if (!isSame && c.iteration && tlData) {
      const idx = tlData.findIndex(t => t.iteration === c.iteration);
      setSelTl(idx >= 0 ? idx : -1);
    } else if (isSame) {
      setSelTl(-1);
    }
  }, [selSha, tlData]);

  const filteredLogs = useMemo(() => logData?.entries?.length ? logData.entries : [], [logData]);

  const isSnap = !!selIter;
  const viewLabel = selIter
    ? selIter.replace('iter-', 'Iter #')
    : 'Current';

  // Container width for git tree
  const botContainerRef = useRef<HTMLDivElement>(null);
  const [gitContainerW, setGitContainerW] = useState(800);
  useEffect(() => {
    const el = botContainerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(entries => {
      setGitContainerW(entries[0].contentRect.width);
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  if (isLoading) return <div className={styles.loading}>Loading…</div>;
  if (!declData?.declarations?.length) return <div className={styles.page}><div className={styles.empty}><h3>No declarations</h3><p>No .lean files with declarations found</p></div></div>;
  // `lo` may briefly be null (first paint after clicking a commit before the
  // snapshot arrives). We still render the page frame so the git tree and
  // sidebar stay visible; the canvas shows a subtle loading message instead.

  return (
    <div className={styles.page}>
      {/* ── Banner ── */}
      <div className={styles.banner}>
        <span className={styles.viewLabel}>
          {viewLabel}{snapLoading && isSnap ? ' (loading…)' : ''}
          {selCommit && (
            <span className={styles.commitBadge}>{selCommit.shortSha} · {selCommit.phase ?? 'commit'}</span>
          )}
        </span>
        {summary && (<>
          {summary.r > 0 && <span className={`${styles.chip} ${styles.chipRed}`}><span className={styles.dot} style={{ background: C_RED }} />{summary.r} stuck</span>}
          {summary.o > 0 && <span className={`${styles.chip} ${styles.chipOrange}`}><span className={styles.dot} style={{ background: C_ORANGE }} />{summary.o} in progress</span>}
          {summary.s > 0 && <span className={`${styles.chip} ${styles.chipGreen}`}><span className={styles.dot} style={{ background: C_GREEN }} />{summary.s} solved</span>}
        </>)}
        {isSnap && (
          <button className={styles.tlReset} onClick={() => { setSelTl(-1); setSelSha(''); }}>
            ← Current
          </button>
        )}
        <div className={styles.zoom}>
          <button className={styles.zbtn} onClick={zoomOut}>−</button>
          <button className={styles.zbtn} onClick={reset}>⟲</button>
          <button className={styles.zbtn} onClick={zoomIn}>+</button>
          <span className={styles.zscale}>{Math.round(scale * 100)}%</span>
        </div>
      </div>

      {/* ── Legend ── */}
      <div className={styles.legend}>
        <span className={styles.li}><span className={styles.ld} style={{ background: C_GREEN }} />Solved</span>
        <span className={styles.li}><span className={styles.ld} style={{ background: C_ORANGE }} />Sorry (changed)</span>
        <span className={styles.li}><span className={styles.ld} style={{ background: C_RED }} />Sorry (stuck)</span>
      </div>

      {/* ── Body: graph + sidebar, then git tree ── */}
      <div className={styles.body}>
        <div className={styles.main}>
          {/* Graph canvas */}
          <div className={styles.gc} ref={cRef}>
            <svg ref={svgRef} className={styles.svg} viewBox={`${vb[0]} ${vb[1]} ${vb[2]} ${vb[3]}`} preserveAspectRatio="xMidYMid meet" width="100%" height="100%">
              {lo?.g.map(g => <g key={g.file}>
                <rect x={g.x} y={g.y} width={g.w} height={g.h} rx={10} ry={10} fill={BG[g.ci]} stroke={BS[g.ci]} strokeWidth={1.5} />
                <text x={g.x + 8} y={g.y + 14} fontSize="10" fontWeight="600" fill="var(--text-muted)" fontFamily="var(--font-mono)">{g.label}</text>
              </g>)}
              {lo?.n.map(n => {
                const sel = n.id === selNode, att = n.d.totalAttempts ?? 0, ms = n.d.latestMilestoneStatus;
                return <g key={n.id} data-node="1" onClick={() => clickNode(n.id)} style={{ cursor: 'pointer' }}>
                  {att > 3 && <rect x={n.x - 2} y={n.y - 2} width={n.w + 4} height={n.h + 4} rx={8} fill="none" stroke={n.c} strokeWidth={1.5} opacity={0.3} />}
                  <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={6} fill="var(--bg-primary)" stroke={sel ? 'var(--blue)' : n.c} strokeWidth={sel ? 2.5 : 1.5} />
                  <text x={n.x + 5} y={n.y + 12} fontSize="8" fontWeight="700" fill={n.c} fontFamily="var(--font-sans)">{n.d.kind.toUpperCase()}</text>
                  <text x={n.x + 5} y={n.y + 25} fontSize="10.5" fontWeight="500" fill="var(--text-primary)" fontFamily="var(--font-mono)">{n.d.name.length > 19 ? n.d.name.slice(0, 18) + '…' : n.d.name}</text>
                  {(att > 0 || ms) && <text x={n.x + 5} y={n.y + 36} fontSize="8" fill="var(--text-muted)" fontFamily="var(--font-mono)">{att > 0 ? `${att} att` : ''}{att > 0 && ms ? ' · ' : ''}{ms || ''}</text>}
                  {n.d.hasSorry ? <><rect x={n.x + n.w - 26} y={n.y + 3} width={20} height={12} rx={6} fill={n.c} opacity={0.15} /><text x={n.x + n.w - 16} y={n.y + 12} fontSize="8" fontWeight="700" fill={n.c} textAnchor="middle" fontFamily="var(--font-mono)">{n.d.sorryCount}s</text></> : <text x={n.x + n.w - 14} y={n.y + 13} fill={C_GREEN} fontSize="10" fontWeight="700">✓</text>}
                </g>;
              })}
              {/* Edges are intentionally not rendered — the dependency lines
                  were hard to read and provided little value. The underlying
                  edge data is still used by the sidebar for name lookups. */}
            </svg>
          </div>

          {/* Sidebar drag handle */}
          <div className={styles.resizeHandleV} onMouseDown={sideResize.onMouseDown} />

          {/* Sidebar */}
          <div className={styles.side} style={{ width: sideResize.size }}>
            {!selNode ? (
              <div className={styles.sideEmpty}>
                Click a graph node to inspect
                {isSnap ? ` (at ${viewLabel})` : ''}
              </div>
            ) : (
              <>
                <div className={styles.sideHead}>
                  <div className={styles.sideName}>{selName}</div>
                  <div className={styles.sideFile}>{selFile}:{nd?.declaration?.line ?? '?'}</div>
                  {isSnap && <div className={styles.sideIterTag}>Code at {viewLabel}</div>}
                  <div className={styles.sideMeta}>
                    {nd?.declaration && <span className={styles.badge} style={{ color: nd.declaration.hasSorry ? C_RED : C_GREEN, borderColor: nd.declaration.hasSorry ? 'rgba(203,36,49,0.3)' : 'rgba(40,167,69,0.3)', background: nd.declaration.hasSorry ? 'rgba(203,36,49,0.06)' : 'rgba(40,167,69,0.06)' }}>{nd.declaration.hasSorry ? `${nd.declaration.sorryCount} sorry` : 'solved'}</span>}
                    {nd?.declaration && <span className={styles.badge} style={{ color: 'var(--text-muted)', borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}>{nd.declaration.kind}</span>}
                    {nd?.milestones?.length ? <span className={styles.badge} style={{ color: 'var(--blue)', borderColor: 'rgba(3,102,214,0.3)', background: 'rgba(3,102,214,0.06)' }}>{nd.milestones.length} session{nd.milestones.length > 1 ? 's' : ''}</span> : null}
                  </div>
                </div>

                {spark && <div className={styles.spark}>
                  <div className={styles.sparkLabel}>Sorry across iterations</div>
                  <Spark data={spark} ai={effectiveSelTl >= 0 ? effectiveSelTl : undefined} />
                </div>}

                {/* Code */}
                <div className={styles.codeSection}>
                  <div className={styles.codeHeader} onClick={() => setCodeOpen(!codeOpen)}>
                    {codeOpen ? '▾' : '▸'} Code {codeLines.length > 0 ? `(${codeLines.length} lines)` : ''}
                  </div>
                  {codeOpen && codeLines.length > 0 && (
                    <div className={styles.codeBlock}>
                      {codeLines.map((l, i) => <div key={i}><LeanCodeLine text={l} tokens={hlCode[i]} /></div>)}
                    </div>
                  )}
                </div>

                {/* Blueprint LaTeX */}
                <BlueprintSection file={selFile} name={selName} />

                {/* Milestones */}
                {nd?.milestones?.length ? (
                  <div className={styles.msSection}>
                    <div className={styles.msLabel}>Milestones{isSnap ? ` (up to ${viewLabel})` : ''}</div>
                    {nd.milestones.map((m, i) => (
                      <div key={i} className={styles.msEntry} style={{ borderLeftColor: STATUS_COLORS[m.status] || 'var(--border)' }}>
                        <div className={styles.msHead}>
                          <span className={styles.msSess}>{m.sessionId.replace('session_', '#')}</span>
                          <span style={{ fontWeight: 600, color: STATUS_COLORS[m.status] || 'var(--text-muted)' }}>{m.status}</span>
                        </div>
                        {m.blocker && <div style={{ color: 'var(--red)', fontSize: 11, marginTop: 3 }}>Blocker: {m.blocker}</div>}
                        {m.nextSteps && <div style={{ color: 'var(--text-secondary)', fontSize: 11, marginTop: 3, fontStyle: 'italic' }}>Next: {m.nextSteps}</div>}
                        {m.keyLemmas?.length ? <div style={{ color: 'var(--text-muted)', fontSize: 10, fontFamily: 'var(--font-mono)', marginTop: 2 }}>Lemmas: {m.keyLemmas.join(', ')}</div> : null}
                        {Array.isArray(m.attempts) && m.attempts.length > 0 && (
                          <div className={styles.msAttempts}>
                            {(m.attempts as any[]).map((a, j) => <AttemptCard key={j} att={a} />)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : null}

                {/* Prover agent log (when a prover commit is selected or viewing snapshot) */}
                {isSnap && !showPhaseLogs && filteredLogs.length > 0 && (
                  <div className={styles.logSection}>
                    <div className={styles.logHeader} onClick={() => setLogsOpen(!logsOpen)}>
                      {logsOpen ? '▾' : '▸'} Agent Log ({filteredLogs.length} events)
                    </div>
                    {logsOpen && <>
                      {logData?.stats && <SessionStats stats={logData.stats} />}
                      {logData?.stats?.sessionSummary && <div className={styles.logSummary}>{logData.stats.sessionSummary}</div>}
                      <div className={styles.logList}>
                        {filteredLogs.map((e, i) => <LogEntry key={i} entry={e} defaultOpen={e.event === 'session_end'} />)}
                      </div>
                    </>}
                  </div>
                )}

                {/* Phase log (plan / refactor / review / finalize) */}
                {showPhaseLogs && (phaseLogData?.entries?.length ?? 0) > 0 && (
                  <div className={styles.logSection}>
                    <div className={styles.logHeader} onClick={() => setPhaseLogsOpen(!phaseLogsOpen)}>
                      {phaseLogsOpen ? '▾' : '▸'} {commitPhase} log ({phaseLogData!.entries.length} events)
                    </div>
                    {phaseLogsOpen && (
                      <div className={styles.logList}>
                        {(phaseLogData!.entries as ProverLogEntry[]).map((e, i) => <LogEntry key={i} entry={e} />)}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* Bottom panel drag handle */}
        <div className={styles.resizeHandleH} onMouseDown={botResize.onMouseDown} />

        {/* Git tree panel */}
        <div className={styles.gitPanel} style={{ height: botResize.size }} ref={botContainerRef}>
          <div className={styles.gitPanelHead}>
            <span className={styles.gitPanelTitle}>Git history</span>
            {isSnap && (
              <button className={styles.tlReset} onClick={() => { setSelTl(-1); setSelSha(''); }}>
                ← Current
              </button>
            )}
          </div>
          <GitTree
            commits={gitData?.commits ?? []}
            selectedSha={selSha}
            onSelect={handleCommitClick}
            containerW={gitContainerW}
          />
        </div>
      </div>
    </div>
  );
}
