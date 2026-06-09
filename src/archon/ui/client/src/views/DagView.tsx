/**
 * DAG page — interactive blueprint dependency graph, a native React port of
 * leandag's own `graph.html` navigator so the dashboard renders the project
 * exactly the way leandag does:
 *
 *  - nodes are dots whose colour encodes *local effort* (green = done/0,
 *    yellow→orange ramp for growing draft effort, red = ∞ no-proof);
 *  - force-directed (forceAtlas2) layout, direction shown by arrows;
 *  - per-node status glyphs (✓ Lean proof · ⚠ sorry · λ Lean decl · ★ LaTeX
 *    proof · § statement);
 *  - clicking a node lights up its whole transitive cone; double-click focuses
 *    it (filters to the cone); search jumps to any id;
 *  - a project-stats overlay (proved %, sorry/ready/gaps, effort done/remaining);
 *  - filters by node-set (all / blueprint / Lean), component, chapter, isolated;
 *  - a rich sidebar with KaTeX-rendered statement/proof and Lean syntax
 *    highlighting.
 *
 * Data comes from `GET /api/dag` (computed fresh by `archon dag-graph`, cached
 * to `.leandag/dag.json`); node fields mirror leandag's `GraphNode.to_dict()`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import 'katex/dist/katex.min.css';
import { useDag, useDagLastModified, type DagNode, type FileMod } from '../hooks/useDag';
import { useGitLog, useBlueprintChapters, type GitCommit } from '../hooks/useGitLog';
import { GitTimeline } from '../components/GitTimeline';
import { buildBlueprintModel, TexFragment } from '../components/BlueprintDoc';

// ── Effort colour scale (mirrors leandag.exporters) ─────────────────────────
const DONE_FILL = '#22c55e', DONE_BORDER = '#15803d'; // effort 0 — formalised
const MATHLIB_FILL = '#3b82f6', MATHLIB_BORDER = '#1d4ed8'; // \mathlibok — in mathlib (blue)
const INF_FILL = '#ef4444', INF_BORDER = '#7f1d1d';   // effort ∞ — no estimate
const GRAD_HUE_HI = 52; // yellow (smallest finite effort)
const GRAD_HUE_LO = 24; // orange (largest finite effort)

function hslHex(hue: number, sat: number, light: number): string {
  // Match Python colorsys.hls_to_rgb(h, l, s).
  const h = hue / 360;
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  let r: number, g: number, b: number;
  if (sat === 0) { r = g = b = light; }
  else {
    const q = light < 0.5 ? light * (1 + sat) : light + sat - light * sat;
    const p = 2 * light - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  const hex = (v: number) => Math.round(v * 255).toString(16).padStart(2, '0');
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

function effortColor(effort: number | null | undefined, maxEffort: number): { background: string; border: string } {
  if (effort === null || effort === undefined) return { background: INF_FILL, border: INF_BORDER };
  if (effort <= 0) return { background: DONE_FILL, border: DONE_BORDER };
  const t = maxEffort > 0 ? Math.min(1, Math.sqrt(effort) / Math.sqrt(maxEffort)) : 0;
  const hue = GRAD_HUE_HI - (GRAD_HUE_HI - GRAD_HUE_LO) * t;
  return { background: hslHex(hue, 0.72, 0.55), border: hslHex(hue, 0.6, 0.38) };
}

function statusGlyphs(n: DagNode): string {
  const hasLean = !!n.lean_source || n.type === 'lean_aux';
  let leanG = '';
  if (n.has_sorry) leanG = '⚠';
  else if (n.proof_size_lean != null) leanG = '✓';
  else if (n.mathlib_ok) leanG = 'ⓜ';
  else if (hasLean) leanG = 'λ';
  let texG = '';
  if (n.proof_size_tex != null) texG = '★';
  else if (n.type !== 'lean_aux') texG = '§';
  return leanG + texG;
}

function visNode(n: DagNode, maxEffort: number) {
  const isAux = n.type === 'lean_aux';
  const isInf = n.effort_local === null || n.effort_local === undefined;
  // \mathlibok is "done" too, but blue so it reads as distinct from green proofs.
  const { background, border } = n.mathlib_ok
    ? { background: MATHLIB_FILL, border: MATHLIB_BORDER }
    : effortColor(n.effort_local, maxEffort);
  const glyphs = statusGlyphs(n);
  const label = n.id.split(':').pop()! + (glyphs ? `\n${glyphs}` : '');
  return {
    id: n.id,
    label,
    shape: 'dot',
    size: isInf ? 16 : 11,
    color: { background, border, highlight: { background, border: '#0f172a' } },
    borderWidth: 2,
    font: { size: 11, multi: false, face: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", color: '#334155' },
    shapeProperties: isAux ? { borderDashes: [3, 3] } : {},
  };
}

// ── LaTeX rendering ──────────────────────────────────────────────────────────
// Statement/proof bodies render through BlueprintDoc's TexFragment (the same
// pipeline as the Blueprint page: math, \emph, comments, resolved \cref{}),
// so the two pages read identically. Only the Lean highlighter stays local.
function esc(s: string): string {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Lean syntax highlighter (mirrors leandag's highlightLean) ────────────────
const KW = /\b(def|lemma|theorem|instance|class|structure|inductive|abbrev|noncomputable|private|protected|section|namespace|end|open|variable|where|do|let|have|show|suffices|calc|if|then|else|return|for|in|fun|match|with|by|sorry)\b/g;
const TACTIC = /\b(simp|ext|rfl|exact|intro|intros|apply|refine|constructor|use|rw|rewrite|rcases|rintro|obtain|push_neg|norm_num|ring|group|linarith|omega|decide|trivial|assumption|congr|tauto|aesop|cases|induction|revert|clear|next|all_goals|repeat|try|first|solve|fin_cases|positivity|gcongr|field_simp)\b/g;
const TYPE = /\b(Nat|Int|Bool|String|List|Array|Option|Type|Prop|Sort|True|False|And|Or|Not|Iff|Eq|Subgroup|Group|Ring|Field|Fintype|Finite)\b/g;
const NUM = /\b(\d+)\b/g;
const STR = /("(?:[^"\\]|\\.)*")/g;
function highlightLean(raw: string): string {
  return raw.split('\n').map((line) => {
    let commentAt = -1, inStr = false;
    for (let i = 0; i < line.length - 1; i++) {
      if (line[i] === '"' && (i === 0 || line[i - 1] !== '\\')) inStr = !inStr;
      if (!inStr && line[i] === '-' && line[i + 1] === '-') { commentAt = i; break; }
    }
    const code = commentAt >= 0 ? line.slice(0, commentAt) : line;
    const comment = commentAt >= 0 ? line.slice(commentAt) : '';
    let h = esc(code);
    h = h.replace(STR, (mm) => `<span class="hl-str">${mm}</span>`);
    h = h.replace(KW, (mm) => `<span class="hl-kw">${mm}</span>`);
    h = h.replace(TACTIC, (mm) => `<span class="hl-tactic">${mm}</span>`);
    h = h.replace(TYPE, (mm) => `<span class="hl-type">${mm}</span>`);
    h = h.replace(NUM, (mm) => `<span class="hl-num">${mm}</span>`);
    const commentHtml = comment ? `<span class="hl-comment">${esc(comment)}</span>` : '';
    return h + commentHtml;
  }).join('\n');
}

// Physics — connected nodes attract so each settles next to its dependencies.
const PHYSICS_OPTS = {
  enabled: true,
  solver: 'forceAtlas2Based',
  forceAtlas2Based: {
    gravitationalConstant: -45, centralGravity: 0.012,
    springLength: 85, springConstant: 0.08, damping: 0.45, avoidOverlap: 0.7,
  },
  stabilization: { enabled: true, iterations: 300, updateInterval: 25, fit: false },
  minVelocity: 0.75, maxVelocity: 30,
} as const;

const ZOOM_MAX = 3.0;
const FIT_FLOOR = 0.34;

type NodeSet = 'union' | 'blueprint' | 'lean';
// Review-oriented graph queries (what to do next / what's wrong / what's dead).
type DagQuery =
  | 'all'        // no query filter
  | 'frontier'   // ready to prove: unproved, every dep done
  | 'zeroEffort' // sorry-free draft, just needs \leanok
  | 'sorry'      // has a sorry/admit
  | 'gaps'       // ∞ effort: no informal proof (roadmap hole)
  | 'unproved'   // not \leanok and not \mathlibok
  | 'leaves'     // nothing depends on it (rdep_count 0)
  | 'roots'      // depends on nothing (dep_count 0)
  | 'isolated';  // no edges at all (dep 0 and rdep 0) — possibly dead

const QUERY_LABEL: Record<DagQuery, string> = {
  all: 'All nodes',
  frontier: 'Frontier (ready to prove)',
  zeroEffort: 'Zero effort (needs \\leanok)',
  sorry: 'Has sorry',
  gaps: '∞ effort (no proof)',
  unproved: 'Unproved',
  leaves: 'Leaves (nothing uses)',
  roots: 'Roots (no deps)',
  isolated: 'Isolated (dead?)',
};

export default function DagView() {
  // Time-travel: when a commit is selected, the DAG is built at that commit
  // (server-side, in-memory — never overwriting the live .leandag/ files).
  const [selectedSha, setSelectedSha] = useState<string>('');
  const { data, isLoading, error, refetch, isFetching } = useDag(selectedSha || undefined);
  const { data: gitData } = useGitLog();
  const commits = gitData?.commits ?? [];
  const navigate = useNavigate();

  // The blueprint label map (cheap numbering pass over all chapters) so the
  // node panel renders statements/proofs exactly like the Blueprint page —
  // \cref{} resolved — and can deep-link into it. Same commit as the graph.
  const { data: bpData } = useBlueprintChapters(selectedSha || undefined);
  const bpLabels = useMemo(
    () => buildBlueprintModel(bpData?.chapters ?? [], true).labels,
    [bpData],
  );
  // Per-file "last modified at iter-NNN" chips (inner git, live view only).
  const { data: lastModData } = useDagLastModified();
  const lastMod = selectedSha ? undefined : lastModData?.files;

  // Resizable sidebar (width) and bottom timeline (height).
  const sideResize = useDragResize(340, 240, 680, 'x');
  const botResize = useDragResize(56, 56, 460, 'y');

  // Measure the git panel width for the timeline layout.
  const gitPanelRef = useRef<HTMLDivElement>(null);
  const [gitW, setGitW] = useState(800);
  useEffect(() => {
    const el = gitPanelRef.current; if (!el) return;
    const ro = new ResizeObserver(() => setGitW(el.clientWidth));
    ro.observe(el); setGitW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const containerRef = useRef<HTMLDivElement>(null);
  const netRef = useRef<Network | null>(null);
  const nodesDSRef = useRef<DataSet<any> | null>(null);
  const edgesDSRef = useRef<DataSet<any> | null>(null);
  const boundsRef = useRef<{ left: number; right: number; top: number; bottom: number } | null>(null);
  const zoomMinRef = useRef(0.05);
  const cbRef = useRef<{ click: (p: any) => void; dbl: (p: any) => void; wheel: (e: WheelEvent) => void; settled: () => void }>({
    click: () => {}, dbl: () => {}, wheel: () => {}, settled: () => {},
  });
  const jumpRef = useRef<string | null>(null);
  // Filled once doJump exists (defined after the highlight machinery); goTo
  // calls through the ref so a jump can fire even when no filter changes.
  const doJumpRef = useRef<() => void>(() => {});

  // Filter / selection state.
  const [nodeset, setNodeset] = useState<NodeSet>('union');
  const [componentSel, setComponentSel] = useState<number | null>(null);
  const [chapterSel, setChapterSel] = useState<string>('');
  const [showOrphans, setShowOrphans] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const [selId, setSelId] = useState<string>('');
  const [search, setSearch] = useState('');
  const [statsOpen, setStatsOpen] = useState(true);
  // Highlight overlay (does NOT subset the canvas — it dims the rest so the
  // matched subgraph stays embedded in its context). Combined with AND.
  const [query, setQuery] = useState<DagQuery>('all');
  const [depRange, setDepRange] = useState<[number, number]>([0, Infinity]);
  const [effRange, setEffRange] = useState<[number, number]>([0, Infinity]);
  const [fileSel, setFileSel] = useState<string>('');
  const [typeSel, setTypeSel] = useState<string>('');

  // ── Derived graph structures ──────────────────────────────────────────────
  const macros = useMemo(() => (data?.meta?.macros ?? {}) as Record<string, string>, [data]);

  // Dedupe nodes by id (duplicate \label{} would crash vis-network's DataSet).
  const uniqueNodes = useMemo(() => {
    const seen = new Set<string>(); const out: DagNode[] = [];
    for (const n of data?.nodes ?? []) { if (seen.has(n.id)) continue; seen.add(n.id); out.push(n); }
    return out;
  }, [data]);

  const allNodes = useMemo(() => {
    const m = new Map<string, DagNode>();
    for (const n of uniqueNodes) m.set(n.id, n);
    return m;
  }, [uniqueNodes]);

  const edges = useMemo(() => (data?.edges ?? []).filter((e) => allNodes.has(e.from) && allNodes.has(e.to)), [data, allNodes]);

  const maxEffort = useMemo(() => {
    let mx = 1;
    for (const n of uniqueNodes) if (n.effort_local != null && n.effort_local > 0) mx = Math.max(mx, n.effort_local);
    return mx;
  }, [uniqueNodes]);

  // Base vis nodes (full styling) — used to add and to restore after highlight.
  const baseVisNodes = useMemo(() => uniqueNodes.map((n) => visNode(n, maxEffort)), [uniqueNodes, maxEffort]);
  const baseVisById = useMemo(() => {
    const m = new Map<string, any>();
    for (const vn of baseVisNodes) m.set(vn.id as string, vn);
    return m;
  }, [baseVisNodes]);

  // Adjacency, degree.
  const { succ, pred, deg } = useMemo(() => {
    const succ = new Map<string, string[]>(), pred = new Map<string, string[]>(), deg = new Map<string, number>();
    for (const id of allNodes.keys()) { succ.set(id, []); pred.set(id, []); deg.set(id, 0); }
    for (const e of edges) {
      succ.get(e.from)?.push(e.to);
      pred.get(e.to)?.push(e.from);
      deg.set(e.from, (deg.get(e.from) ?? 0) + 1);
      deg.set(e.to, (deg.get(e.to) ?? 0) + 1);
    }
    return { succ, pred, deg };
  }, [allNodes, edges]);

  const isOrphan = useCallback((id: string) => !((deg.get(id) ?? 0) > 0), [deg]);

  // Weakly-connected components (union-find, orphans excluded).
  const { components, compOf } = useMemo(() => {
    const parent = new Map<string, string>();
    for (const id of allNodes.keys()) parent.set(id, id);
    const find = (x: string): string => { while (parent.get(x) !== x) { const p = parent.get(parent.get(x)!)!; parent.set(x, p); x = p; } return x; };
    for (const e of edges) { if (parent.has(e.from) && parent.has(e.to)) parent.set(find(e.from), find(e.to)); }
    const members = new Map<string, string[]>();
    for (const id of allNodes.keys()) { if (isOrphan(id)) continue; const r = find(id); (members.get(r) ?? members.set(r, []).get(r)!).push(id); }
    const components = [...members.values()].sort((a, b) => b.length - a.length);
    const compOf = new Map<string, number>();
    components.forEach((mem, i) => mem.forEach((id) => compOf.set(id, i)));
    return { components, compOf };
  }, [allNodes, edges, isOrphan]);

  const compRepr = useCallback((mem: string[]) => {
    let best = mem[0];
    for (const id of mem) if ((deg.get(id) ?? 0) > (deg.get(best) ?? 0)) best = id;
    return best.split(':').pop();
  }, [deg]);

  const chapters = useMemo(() => [...new Set(uniqueNodes.map((n) => n.chapter).filter(Boolean))].sort(), [uniqueNodes]);

  // Slider / select domains for the highlight overlay.
  const fileOf = useCallback((n: DagNode) => (n.lean_file || n.tex_file || '') as string, []);
  const depMax = useMemo(() => uniqueNodes.reduce((mx, n) => Math.max(mx, n.dep_count ?? 0), 0), [uniqueNodes]);
  const effMax = useMemo(() => uniqueNodes.reduce((mx, n) => (n.effort_total != null ? Math.max(mx, n.effort_total) : mx), 0), [uniqueNodes]);
  const files = useMemo(() => [...new Set(uniqueNodes.map(fileOf).filter(Boolean))].sort(), [uniqueNodes, fileOf]);
  const types = useMemo(() => [...new Set(uniqueNodes.map((n) => n.type).filter(Boolean))].sort(), [uniqueNodes]);

  // Cone (ancestors ∪ descendants ∪ self) and ancestors-only walkers.
  const coneOf = useCallback((id: string) => {
    const out = new Set<string>([id]);
    const walk = (adj: Map<string, string[]>, start: string) => {
      const st = [start];
      while (st.length) { const x = st.pop()!; for (const y of adj.get(x) ?? []) if (!out.has(y)) { out.add(y); st.push(y); } }
    };
    walk(pred, id); walk(succ, id);
    return out;
  }, [pred, succ]);

  const ancestorsOf = useCallback((id: string) => {
    const out = new Set<string>(); const st = [...(pred.get(id) ?? [])];
    while (st.length) { const x = st.pop()!; if (!out.has(x)) { out.add(x); for (const y of pred.get(x) ?? []) st.push(y); } }
    return out;
  }, [pred]);

  const inNodeset = useCallback((id: string) => {
    const n = allNodes.get(id); if (!n) return false;
    if (nodeset === 'blueprint') return n.type !== 'lean_aux';
    if (nodeset === 'lean') return n.type === 'lean_aux' || !!(n.lean_source && n.lean_source.length);
    return true;
  }, [allNodes, nodeset]);

  // A node needs no more work if it's leanok, in mathlib, or has a Lean proof.
  const isDoneNode = useCallback(
    (n: DagNode) => n.proved || !!n.mathlib_ok || n.effort_local === 0, [],
  );
  const doneIds = useMemo(() => {
    const s = new Set<string>();
    for (const n of allNodes.values()) if (isDoneNode(n)) s.add(n.id);
    return s;
  }, [allNodes, isDoneNode]);

  // Predicate for the active review query (see DagQuery).
  const matchesQuery = useCallback((id: string): boolean => {
    const n = allNodes.get(id); if (!n) return false;
    switch (query) {
      case 'frontier':
        return n.type !== 'lean_aux' && !isDoneNode(n)
          && n.uses.every((d) => !allNodes.has(d) || doneIds.has(d));
      case 'zeroEffort':
        return n.type !== 'lean_aux' && !n.proved && !n.mathlib_ok && n.effort_local === 0;
      case 'sorry': return n.has_sorry;
      case 'gaps': return n.effort_local === null || n.effort_local === undefined;
      case 'unproved': return n.type !== 'lean_aux' && !n.proved && !n.mathlib_ok;
      case 'leaves': return n.rdep_count === 0;
      case 'roots': return n.dep_count === 0;
      case 'isolated': return n.dep_count === 0 && n.rdep_count === 0;
      default: return true;
    }
  }, [allNodes, query, isDoneNode, doneIds]);

  // What's actually loaded on the canvas — structural filters only (nodeset /
  // component / chapter / focus / orphans). The review query and the slider /
  // file / type filters do NOT subset here; they highlight (see highlightSet).
  const visibleSet = useMemo(() => {
    if (focus && allNodes.has(focus)) return new Set([...coneOf(focus)].filter(inNodeset));
    let ids = [...allNodes.keys()].filter(inNodeset);
    if (!showOrphans) ids = ids.filter((id) => !isOrphan(id));
    if (componentSel !== null) ids = ids.filter((id) => compOf.get(id) === componentSel);
    if (chapterSel) ids = ids.filter((id) => allNodes.get(id)!.chapter === chapterSel);
    return new Set(ids);
  }, [focus, allNodes, coneOf, inNodeset, showOrphans, componentSel, chapterSel, compOf, isOrphan]);

  // Highlight overlay: query ∧ dep-range ∧ effort-range ∧ file ∧ type. When any
  // of these is engaged we keep the whole visible graph but dim everything that
  // doesn't match, so the subgraph stays visible *in context* (its edges to the
  // rest are still drawn). null ⇒ nothing to highlight (full colour).
  const highlightActive =
    query !== 'all' ||
    depRange[0] > 0 || (depRange[1] !== Infinity && depRange[1] < depMax) ||
    effRange[0] > 0 || (effRange[1] !== Infinity && effRange[1] < effMax) ||
    !!fileSel || !!typeSel;

  const matchesHighlight = useCallback((id: string): boolean => {
    const n = allNodes.get(id); if (!n) return false;
    if (query !== 'all' && !matchesQuery(id)) return false;
    const dc = n.dep_count ?? 0;
    if (dc < depRange[0] || dc > depRange[1]) return false;
    const eff = n.effort_total ?? Infinity;
    if (eff < effRange[0] || eff > effRange[1]) return false;
    if (fileSel && fileOf(n) !== fileSel) return false;
    if (typeSel && n.type !== typeSel) return false;
    return true;
  }, [allNodes, query, matchesQuery, depRange, effRange, fileSel, typeSel, fileOf]);

  const highlightSet = useMemo<Set<string> | null>(() => {
    if (!highlightActive) return null;
    return new Set([...visibleSet].filter(matchesHighlight));
  }, [highlightActive, visibleSet, matchesHighlight]);

  // ── Imperative graph helpers (read latest via refs) ───────────────────────
  const recomputeBounds = useCallback(() => {
    const net = netRef.current; if (!net) return;
    const pts = Object.values(net.getPositions()) as { x: number; y: number }[];
    if (pts.length) {
      const pad = 200;
      boundsRef.current = {
        left: Math.min(...pts.map((p) => p.x)) - pad, right: Math.max(...pts.map((p) => p.x)) + pad,
        top: Math.min(...pts.map((p) => p.y)) - pad, bottom: Math.max(...pts.map((p) => p.y)) + pad,
      };
    } else boundsRef.current = null;
  }, []);

  const fitFloored = useCallback(() => {
    const net = netRef.current; if (!net) return;
    net.fit({ animation: false } as any);
    const fitScale = net.getScale();
    zoomMinRef.current = Math.max(0.02, Math.min(fitScale * 0.7, ZOOM_MAX));
    if (fitScale < FIT_FLOOR) net.moveTo({ scale: FIT_FLOOR, animation: false } as any);
    recomputeBounds();
  }, [recomputeBounds]);

  const highlightCone = useCallback((id: string) => {
    const nodesDS = nodesDSRef.current, edgesDS = edgesDSRef.current;
    if (!nodesDS || !edgesDS) return;
    const cone = coneOf(id);
    nodesDS.update((nodesDS.getIds() as string[]).map((nid) =>
      cone.has(nid) ? baseVisById.get(nid)
        : { id: nid, color: { background: '#e5e7eb', border: '#d1d5db' }, font: { color: '#cbd5e1' } }));
    edgesDS.update((edgesDS.get() as any[]).map((e) => {
      const on = cone.has(e.from) && cone.has(e.to);
      return { id: e.id, color: on ? { color: '#475569', highlight: '#334155' } : { color: '#edf0f4' }, width: on ? 2.5 : 1 };
    }));
  }, [coneOf, baseVisById]);

  // Base skin (no node cone selected): honour the highlight overlay if one is
  // active (dim non-matches but keep them on canvas), else full colour.
  const applyBaseStyling = useCallback(() => {
    const nodesDS = nodesDSRef.current, edgesDS = edgesDSRef.current;
    if (!nodesDS || !edgesDS) return;
    const hl = highlightSet;
    if (!hl) {
      nodesDS.update((nodesDS.getIds() as string[]).map((nid) => baseVisById.get(nid)).filter(Boolean));
      edgesDS.update((edgesDS.get() as any[]).map((e) => ({ id: e.id, color: { color: '#cbd5e1', highlight: '#64748b' }, width: 1 })));
      return;
    }
    nodesDS.update((nodesDS.getIds() as string[]).map((nid) =>
      hl.has(nid) ? baseVisById.get(nid)
        : { id: nid, color: { background: '#e5e7eb', border: '#d1d5db' }, font: { color: '#cbd5e1' } }).filter(Boolean));
    edgesDS.update((edgesDS.get() as any[]).map((e) => {
      const on = hl.has(e.from) && hl.has(e.to);
      return { id: e.id, color: on ? { color: '#475569', highlight: '#334155' } : { color: '#edf0f4' }, width: on ? 2 : 1 };
    }));
  }, [baseVisById, highlightSet]);
  const clearHighlight = applyBaseStyling;
  const applyBaseRef = useRef(applyBaseStyling);
  applyBaseRef.current = applyBaseStyling;

  // Reveal a node by relaxing filters, then jump to it once it is visible.
  const goTo = useCallback((id: string) => {
    if (!allNodes.has(id)) return;
    setSelId(id);
    jumpRef.current = id;
    if (!visibleSet.has(id)) {
      setFocus(null); setComponentSel(null); setChapterSel('');
      if (!inNodeset(id)) setNodeset('union');
      if (isOrphan(id)) setShowOrphans(true);
    } else {
      // Already on canvas: no filter change will fire, so doJump would never
      // run — the panel would open without selecting/centering the node on
      // the graph. Jump explicitly (deferred so doJumpRef is current).
      setTimeout(() => doJumpRef.current(), 0);
    }
  }, [allNodes, visibleSet, inNodeset, isOrphan]);

  // Cross-page deep links: /dag?node=<id> (Blueprint ⬡ chips) and
  // /dag?file=<lean-or-tex-file> (Diffs page). Consumed once, after the
  // graph data is in.
  const [searchParams, setSearchParams] = useSearchParams();
  const consumedNodeLink = useRef(false);
  useEffect(() => {
    if (consumedNodeLink.current || !allNodes.size) return;
    const id = searchParams.get('node');
    const file = searchParams.get('file');
    if (!id && !file) return;
    consumedNodeLink.current = true;
    setSearchParams({}, { replace: true });
    if (id && allNodes.has(id)) { goTo(id); setSearch(id); return; }
    if (file && files.includes(file)) {
      setFileSel(file);
      // Select the file's first node so the panel has context.
      const first = uniqueNodes.find((n) => fileOf(n) === file);
      if (first) goTo(first.id);
    }
  }, [allNodes, files, uniqueNodes, fileOf, searchParams, setSearchParams, goTo]);

  // Read selection via a ref so doJump stays stable across clicks — otherwise
  // selecting a node would re-create doJump, re-run the apply effect, and
  // rebuild (re-settle) the whole graph instead of just highlighting the cone.
  const selIdRef = useRef(selId);
  selIdRef.current = selId;
  const doJump = useCallback(() => {
    const net = netRef.current, nodesDS = nodesDSRef.current; if (!net || !nodesDS) return;
    const id = jumpRef.current;
    if (id && (nodesDS.getIds() as string[]).includes(id)) {
      net.selectNodes([id]); net.focus(id, { scale: 1.2, animation: { duration: 400, easingFunction: 'easeInOutQuad' } } as any);
      highlightCone(id); jumpRef.current = null;
    } else if (selIdRef.current && (nodesDS.getIds() as string[]).includes(selIdRef.current)) {
      highlightCone(selIdRef.current);
    }
  }, [highlightCone]);
  doJumpRef.current = doJump;

  // Keep latest imperative callbacks reachable from the once-registered handlers.
  useEffect(() => {
    cbRef.current = {
      click: (p) => { const id = p?.nodes?.[0]; if (id) { setSelId(String(id)); highlightCone(String(id)); } else { setSelId(''); clearHighlight(); } },
      dbl: (p) => { const id = p?.nodes?.[0]; if (id && allNodes.has(String(id))) { setFocus(String(id)); setSelId(String(id)); jumpRef.current = String(id); } },
      wheel: (e) => {
        const net = netRef.current; if (!net) return;
        e.preventDefault(); e.stopPropagation();
        const pos = net.getViewPosition(); const sc = net.getScale();
        if (e.ctrlKey) {
          const f = e.deltaY > 0 ? 0.9 : 1 / 0.9;
          net.moveTo({ scale: Math.max(zoomMinRef.current, Math.min(ZOOM_MAX, sc * f)), animation: false } as any);
        } else {
          let nx = pos.x + e.deltaX / sc, ny = pos.y + e.deltaY / sc;
          const b = boundsRef.current;
          if (b) { nx = Math.max(b.left, Math.min(b.right, nx)); ny = Math.max(b.top, Math.min(b.bottom, ny)); }
          net.moveTo({ position: { x: nx, y: ny }, animation: false } as any);
        }
      },
      settled: () => { const net = netRef.current; if (!net) return; net.setOptions({ physics: false }); fitFloored(); if (selIdRef.current) doJump(); else applyBaseStyling(); },
    };
  });

  // ── Init the network once the graph is ready ──────────────────────────────
  const ready = !!(data && !data.error && uniqueNodes.length);
  useEffect(() => {
    if (!ready || !containerRef.current || netRef.current) return;
    const nodesDS = new DataSet<any>([]); const edgesDS = new DataSet<any>([]);
    nodesDSRef.current = nodesDS; edgesDSRef.current = edgesDS;
    const network = new Network(
      containerRef.current,
      { nodes: nodesDS as never, edges: edgesDS as never },
      {
        layout: { improvedLayout: uniqueNodes.length <= 250 },
        physics: { enabled: false },
        edges: { smooth: false, color: { color: '#cbd5e1', highlight: '#64748b' }, arrows: { to: { scaleFactor: 0.55 } }, width: 1 },
        nodes: { shape: 'dot' },
        interaction: { hover: true, tooltipDelay: 150, zoomView: false },
      },
    );
    netRef.current = network;
    network.on('stabilizationIterationsDone', () => cbRef.current.settled());
    network.on('click', (p) => cbRef.current.click(p));
    network.on('doubleClick', (p) => cbRef.current.dbl(p));
    const el = containerRef.current;
    const wheel = (e: WheelEvent) => cbRef.current.wheel(e);
    el.addEventListener('wheel', wheel, { capture: true, passive: false });
    return () => {
      el.removeEventListener('wheel', wheel, { capture: true } as any);
      network.destroy(); netRef.current = null; nodesDSRef.current = null; edgesDSRef.current = null;
    };
  }, [ready, uniqueNodes.length]);

  // ── Apply the visible set to the canvas whenever filters/data change ───────
  useEffect(() => {
    const net = netRef.current, nodesDS = nodesDSRef.current, edgesDS = edgesDSRef.current;
    if (!net || !nodesDS || !edgesDS) return;
    const nodes = baseVisNodes.filter((n) => visibleSet.has(n.id as string));
    const es = edges.filter((e) => visibleSet.has(e.from) && visibleSet.has(e.to)).map((e, i) => ({ id: i, from: e.from, to: e.to, arrows: 'to' }));
    nodesDS.clear(); edgesDS.clear();
    nodesDS.add(nodes); edgesDS.add(es);
    if (nodes.length > 1) net.setOptions({ physics: PHYSICS_OPTS as any });
    else { net.setOptions({ physics: false }); setTimeout(() => { fitFloored(); if (selIdRef.current) doJump(); else applyBaseRef.current(); }, 0); }
    // Deps intentionally exclude selection-derived callbacks (fitFloored/doJump
    // are stable) so the canvas only rebuilds on real filter/data changes, not
    // when a node is clicked. eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleSet, baseVisNodes, edges]);

  // Re-skin when the highlight overlay changes (slider / file / type / query),
  // unless a node cone is currently selected (that takes visual precedence).
  useEffect(() => {
    if (!netRef.current || selIdRef.current) return;
    applyBaseStyling();
  }, [highlightSet, applyBaseStyling]);

  // Commit a search entry.
  const commitSearch = useCallback(() => {
    const v = search.trim();
    if (allNodes.has(v)) goTo(v);
  }, [search, allNodes, goTo]);

  const resetView = useCallback(() => {
    setShowOrphans(false); setFocus(null); setComponentSel(null); setChapterSel(''); setNodeset('union'); setSearch(''); setQuery('all');
    setDepRange([0, Infinity]); setEffRange([0, Infinity]); setFileSel(''); setTypeSel('');
  }, []);

  // Toggle a review query from a clickable stat row (click again to clear). This
  // now *highlights* the matches in place — it no longer subsets the canvas — so
  // we keep any active focus cone for composition.
  const toggleQuery = useCallback((q: DagQuery) => {
    setQuery((prev) => (prev === q ? 'all' : q));
    // Isolated nodes are hidden by default; reveal them so the highlight lands.
    if (q === 'isolated') setShowOrphans(true);
  }, []);

  // ── Stats overlay ─────────────────────────────────────────────────────────
  const stats = useMemo(() => {
    const vals = [...allNodes.values()];
    const bp = vals.filter((n) => n.type !== 'lean_aux');
    const proved = bp.filter((n) => n.proved || n.mathlib_ok).length;
    const mathlib = bp.filter((n) => n.mathlib_ok).length;
    const sorry = vals.filter((n) => n.has_sorry).length;
    const ready = bp.filter((n) => !isDoneNode(n) && n.uses.every((d) => !allNodes.has(d) || doneIds.has(d))).length;
    const gaps = bp.filter((n) => !n.lean_name && !n.mathlib_ok).length;
    const leanok = bp.filter((n) => !n.proved && !n.mathlib_ok && n.effort_local === 0).length;
    // Structure / health.
    const leaves = vals.filter((n) => n.rdep_count === 0).length;
    const roots = vals.filter((n) => n.dep_count === 0).length;
    const isolated = vals.filter((n) => n.dep_count === 0 && n.rdep_count === 0).length;
    let done = 0, remLower = 0, infNodes = 0;
    for (const n of vals) {
      if (n.proof_size_lean != null) done += n.proof_size_lean;
      if (n.effort_local == null) infNodes++; else remLower += n.effort_local;
    }
    const pct = bp.length ? Math.round((100 * proved) / bp.length) : 0;
    return { provedN: proved, mathlib, bpN: bp.length, pct, sorry, ready, gaps, leanok, done, remLower, infNodes, leaves, roots, isolated };
  }, [allNodes, isDoneNode, doneIds]);

  const sel = selId ? allNodes.get(selId) : undefined;

  if (isLoading) return <div style={pad}>Loading blueprint DAG…</div>;
  if (error) return <div style={pad}>Failed to load the DAG. Is the dashboard server running?</div>;
  if (data?.error) return <div style={pad}><strong>No DAG available.</strong><div style={{ marginTop: 8, color: 'var(--text-muted)' }}>{data.error}</div></div>;
  if (!uniqueNodes.length) return <div style={pad}>The blueprint DAG is empty — no declarations found yet.</div>;

  const m = data!.meta;
  const dups = m.duplicate_ids ?? [];
  const fmt = (v: number) => v.toLocaleString('en-US');

  // A stat row that doubles as a graph filter (click to apply, click again to clear).
  const qRow = (label: string, value: number, q: DagQuery, vClass = '') => (
    <div className={`row dv-qrow ${query === q ? 'dv-qon' : ''}`}
      onClick={() => toggleQuery(q)} title={`Filter graph: ${QUERY_LABEL[q]}`}>
      <span>{label}</span><span className={`v ${vClass}`}>{value}</span>
    </div>
  );

  return (
    <div className="dv-root">
      <style>{DV_CSS}</style>
      <style>{`
        .dv-stats .dv-qrow { cursor: pointer; border-radius: 4px; padding-left: 3px; margin-left: -3px; }
        .dv-stats .dv-qrow:hover { background: var(--bg-tertiary); }
        .dv-stats .dv-qrow.dv-qon { background: rgba(59,130,246,0.16); }
        .dv-select.dv-select-on { border-color: #3b82f6; color: #1d4ed8; font-weight: 600; }
      `}</style>

      {/* Toolbar */}
      <div className="dv-toolbar">
        <span className="dv-brand">Blueprint DAG</span>
        <span className="dv-stat">{visibleSet.size} / {uniqueNodes.length} nodes · {edges.length} edges{highlightSet ? ` · ${highlightSet.size} highlighted` : ''}{m.entry ? ` · ${m.entry}` : ''}{selectedSha ? <span className="dv-hist"> · @{selectedSha.slice(0, 7)} (historical)</span> : ''}</span>
        <span className="dv-legend">
          <span className="leg-dot" style={{ background: DONE_FILL }} /><span className="leg-txt">done</span>
          <span className="leg-dot" style={{ background: MATHLIB_FILL }} /><span className="leg-txt">mathlib</span>
          <span className="leg-bar" /><span className="leg-txt">more effort</span>
          <span className="leg-dot" style={{ background: INF_FILL }} /><span className="leg-txt">∞ no proof</span>
        </span>
        <span className="dv-legend dv-sym-legend">
          <span className="leg-txt">✓ Lean proof</span><span className="leg-txt">⚠ sorry</span><span className="leg-txt">ⓜ mathlib</span>
          <span className="leg-txt">λ Lean decl</span><span className="leg-txt">★ LaTeX proof</span><span className="leg-txt">§ statement</span>
        </span>
        {dups.length > 0 && (
          <span className="dv-warn" title={dups.join('\n')}>⚠ {dups.length} duplicate label{dups.length > 1 ? 's' : ''}</span>
        )}
        <button className="dv-btn" style={{ marginLeft: 'auto' }} onClick={() => refetch()} disabled={isFetching}>{isFetching ? 'Rebuilding…' : 'Rebuild'}</button>
      </div>

      {/* Controls */}
      <div className="dv-controls">
        <input className="dv-input" type="text" list="dv-node-ids" placeholder="Search node id…" autoComplete="off" spellCheck={false}
          value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') commitSearch(); }} onBlur={commitSearch} />
        <datalist id="dv-node-ids">{[...allNodes.keys()].sort().map((id) => <option key={id} value={id} />)}</datalist>
        <select className="dv-select" value={nodeset} onChange={(e) => { setNodeset(e.target.value as NodeSet); setFocus(null); }} title="Which declarations to include">
          <option value="union">All declarations</option>
          <option value="blueprint">Blueprint only</option>
          <option value="lean">Lean only</option>
        </select>
        <select className="dv-select" value={componentSel === null ? '' : String(componentSel)} onChange={(e) => { setComponentSel(e.target.value === '' ? null : Number(e.target.value)); setFocus(null); }}>
          <option value="">All components</option>
          {components.map((mem, i) => <option key={i} value={i}>#{i + 1} · {mem.length} nodes · {compRepr(mem)}</option>)}
        </select>
        <select className="dv-select" value={chapterSel} onChange={(e) => { setChapterSel(e.target.value); setFocus(null); }}>
          <option value="">All chapters</option>
          {chapters.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select className={`dv-select ${query !== 'all' ? 'dv-select-on' : ''}`} value={query}
          onChange={(e) => { const q = e.target.value as DagQuery; setQuery(q); if (q === 'isolated') setShowOrphans(true); }}
          title="Highlight a review query (dims the rest, keeps it on canvas)">
          {(Object.keys(QUERY_LABEL) as DagQuery[]).map((q) => (
            <option key={q} value={q}>{QUERY_LABEL[q]}</option>
          ))}
        </select>
        <select className={`dv-select ${typeSel ? 'dv-select-on' : ''}`} value={typeSel}
          onChange={(e) => setTypeSel(e.target.value)} title="Highlight by declaration type">
          <option value="">Any type</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select className={`dv-select ${fileSel ? 'dv-select-on' : ''}`} value={fileSel}
          onChange={(e) => setFileSel(e.target.value)} title="Highlight by source file">
          <option value="">Any file</option>
          {files.map((f) => <option key={f} value={f}>{f.split('/').pop()}</option>)}
        </select>
        {depMax > 0 && (
          <DualRange label="deps" min={0} max={depMax} step={1} value={depRange}
            onChange={setDepRange} fmt={(v) => String(v)} />
        )}
        {effMax > 0 && (
          <DualRange label="effort" min={0} max={effMax} step={Math.max(1, Math.round(effMax / 100))}
            value={effRange} onChange={setEffRange} fmt={(v) => v.toLocaleString('en-US')} infiniteTop />
        )}
        <label className="dv-chk"><input type="checkbox" checked={showOrphans} onChange={(e) => { setShowOrphans(e.target.checked); setFocus(null); }} /> show isolated nodes</label>
        {focus && (
          <span className="dv-pill" title="Clear focus" onClick={() => setFocus(null)}>
            <span className="lbl">focus: {focus.split(':').pop()}</span><span className="x">×</span>
          </span>
        )}
        <button className="dv-btn" onClick={resetView}>Reset view</button>
      </div>

      {/* Main */}
      <div className="dv-main">
        <div className="dv-graph" ref={containerRef} />
        {statsOpen ? (
          <div className="dv-stats">
            <div className="dv-stats-head">
              <h4>Project</h4>
              <button className="dv-stats-toggle" title="Minimize" onClick={() => setStatsOpen(false)}>–</button>
            </div>
            <div className="row"><span>Proved (\leanok)</span><span className="v done">{stats.provedN}/{stats.bpN} · {stats.pct}%</span></div>
            <div className="bar"><span style={{ width: `${stats.pct}%` }} /></div>
            {stats.mathlib > 0 && <div className="row"><span>Mathlib-backed</span><span className="v mathlib">{stats.mathlib}</span></div>}
            {qRow('With sorry', stats.sorry, 'sorry', stats.sorry ? 'inf' : '')}
            {qRow('Ready to formalize', stats.ready, 'frontier')}
            <div className="row"><span>Needs \lean{'{}'}</span><span className="v">{stats.gaps}</span></div>
            {qRow('Needs \\leanok', stats.leanok, 'zeroEffort')}
            <div className="sep" />
            <h4>Structure</h4>
            <div className="row"><span>Components</span><span className="v">{components.length}</span></div>
            {qRow('Leaves', stats.leaves, 'leaves')}
            {qRow('Roots', stats.roots, 'roots')}
            {qRow('Isolated', stats.isolated, 'isolated', stats.isolated ? 'inf' : '')}
            <div className="sep" />
            <h4>Effort (chars)</h4>
            <div className="row"><span>Done</span><span className="v done">{fmt(stats.done)}</span></div>
            <div className="row"><span>Remaining ≥</span><span className="v work">{fmt(stats.remLower)}</span></div>
            {qRow('∞ nodes', stats.infNodes, 'gaps', 'inf')}
          </div>
        ) : (
          <button className="dv-stats-show" title="Show project stats" onClick={() => setStatsOpen(true)}>
            ▸ Stats · {stats.pct}% proved
          </button>
        )}

        {/* Sidebar */}
        <div className="dv-resize-v" onMouseDown={sideResize.onMouseDown} title="Drag to resize" />
        <aside className="dv-sidebar" style={{ width: sideResize.size }}>
          {!sel ? (
            <div className="dv-sidebar-empty"><p>Click a node to inspect it</p></div>
          ) : (
            <NodePanel n={sel} ancestors={ancestorsOf(selId)} macros={macros} focused={focus === selId}
              labels={bpLabels} lastMod={lastMod}
              onGoTo={goTo} onToggleFocus={() => (focus === selId ? setFocus(null) : (setFocus(selId), setSelId(selId)))}
              onOpenBlueprint={(label) => navigate(`/blueprint?focus=${encodeURIComponent(label)}`)}
              onOpenBlueprintAt={(slug, anchor) => navigate(`/blueprint?slug=${encodeURIComponent(slug)}&anchor=${encodeURIComponent(anchor)}`)}
              onOpenLogs={(iter) => navigate(`/logs?iter=${encodeURIComponent(iter)}`)}
              onOpenDiffs={(iter) => navigate(`/diffs?iter=${encodeURIComponent(iter)}`)}
              onOpenDiffsFile={(slug) => navigate(`/diffs?file=${encodeURIComponent(slug)}`)} />
          )}
        </aside>
      </div>

      {/* Temporal axis — same commit rail as the Graph view. Click a commit to
          rebuild the DAG as it was at that commit (in-memory, never cached). */}
      <div className="dv-resize-h" onMouseDown={botResize.onMouseDown} title="Drag to resize" />
      <div className="dv-git-panel" ref={gitPanelRef} style={{ height: botResize.size }}>
        <div className="dv-git-head">
          <span className="dv-git-title">Git history{isFetching && selectedSha ? ' · building…' : ''}</span>
          {selectedSha && (
            <button className="dv-git-live" onClick={() => setSelectedSha('')}>← Live</button>
          )}
        </div>
        <GitTimeline
          commits={commits}
          selectedSha={selectedSha}
          onSelect={(c: GitCommit) => setSelectedSha((prev) => (prev === c.sha ? '' : c.sha))}
          containerW={gitW}
        />
      </div>
    </div>
  );
}

// ── Sidebar node panel ───────────────────────────────────────────────────────
function charVal(v: number | null | undefined) {
  if (v === null || v === undefined) return <span className="m-val m-inf">∞</span>;
  return <span className="m-val">{v.toLocaleString('en-US')}</span>;
}
function workVal(v: number | null | undefined) {
  if (v === null || v === undefined) return <span className="m-val m-inf">∞</span>;
  if (v === 0) return <span className="m-val m-done">0 ✓</span>;
  return <span className="m-val m-work">{v.toLocaleString('en-US')}</span>;
}
function ModChip({ label, mod, onOpenLogs, onOpenDiffs }: {
  label: string; mod: FileMod | undefined;
  onOpenLogs: (iter: string) => void; onOpenDiffs: (iter: string) => void;
}) {
  if (!mod || !mod.iteration) return null;
  return (
    <span className="mod-chip" title={`${mod.subject}\n${mod.date}`}>
      <span className="mod-lbl">{label}</span>
      <button className="mod-iter" onClick={() => onOpenLogs(mod.iteration!)}
        title={`Open ${mod.iteration} logs`}>✎ {mod.iteration}{mod.phase ? `/${mod.phase}` : ''}</button>
      <button className="mod-diff" onClick={() => onOpenDiffs(mod.iteration!)}
        title={`Open ${mod.iteration} diffs`}>±</button>
    </span>
  );
}

const DEP_PREVIEW = 2;
function DepList({ items, empty, onGoTo }: { items: string[]; empty: string; onGoTo: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  if (!items.length) return <div className="deps-list"><span className="no-deps">{empty}</span></div>;
  const shown = expanded ? items : items.slice(0, DEP_PREVIEW);
  const hidden = items.length - shown.length;
  return (
    <div className="deps-list">
      {shown.map((u) => <span key={u} className="dep-chip" onClick={() => onGoTo(u)}>{u}</span>)}
      {items.length > DEP_PREVIEW && (
        <button className="dep-more" onClick={() => setExpanded((v) => !v)}>
          {expanded ? '− less' : `+${hidden} more`}
        </button>
      )}
    </div>
  );
}

function NodePanel({ n, ancestors, macros, focused, labels, lastMod, onGoTo, onToggleFocus, onOpenBlueprint, onOpenBlueprintAt, onOpenLogs, onOpenDiffs, onOpenDiffsFile }: {
  n: DagNode; ancestors: Set<string>; macros: Record<string, string>; focused: boolean;
  labels: ReturnType<typeof buildBlueprintModel>['labels'];
  lastMod: Record<string, FileMod> | undefined;
  onGoTo: (id: string) => void; onToggleFocus: () => void;
  onOpenBlueprint: (label: string) => void;
  onOpenBlueprintAt: (slug: string, anchor: string) => void;
  onOpenLogs: (iter: string) => void;
  onOpenDiffs: (iter: string) => void;
  onOpenDiffsFile: (slug: string) => void;
}) {
  const statusBadge = n.proved
    ? <span className="badge badge-proved">✓ leanok</span>
    : n.mathlib_ok ? <span className="badge badge-mathlib">ⓜ mathlib</span>
    : n.has_sorry ? <span className="badge badge-sorry">sorry</span> : <span className="badge badge-unproved">unproved</span>;
  const directSet = new Set(n.uses);
  const indirect = [...ancestors].filter((x) => !directSet.has(x)).sort();
  const inBlueprint = labels.has(n.id);
  // lean_file is project-relative (matches the inner-git paths); tex_file is
  // relative to blueprint/src/ — try both forms.
  const lookupMod = (f?: string | null) =>
    lastMod && f ? (lastMod[f] ?? lastMod[`blueprint/src/${f}`]) : undefined;
  const texMod = lookupMod(n.tex_file);
  const leanMod = lookupMod(n.lean_file);

  return (
    <div className="dv-sidebar-content">
      <div className="card">
        <div className="node-badges"><span className="badge badge-type">{n.type.toUpperCase()}</span>{statusBadge}</div>
        <div className="node-title">{n.title || n.id}</div>
        <div className="node-id">{n.id}</div>
        {n.chapter && <div className="node-chapter">§ {n.chapter}</div>}
        {n.lean_name && <div className="lean-ref">Lean: <code>{n.lean_name}</code></div>}
        <button className="btn-focus" onClick={onToggleFocus}>⊙ {focused ? 'Clear focus' : 'Focus dependency cone'}</button>
        {inBlueprint && (
          <button className="btn-focus" onClick={() => onOpenBlueprint(n.id)}>📖 Open in blueprint</button>
        )}
        {n.lean_file && (
          <button className="btn-focus"
            onClick={() => onOpenDiffsFile(String(n.lean_file).replace(/\.lean$/, '').replace(/\//g, '_'))}>
            ± Open in diffs
          </button>
        )}
      </div>

      <div className="card">
        <div className="card-title">Structure</div>
        <div className="degrees">
          <div className="degree"><span className="degree-val">{n.dep_count}</span><span className="degree-label">direct deps</span></div>
          <div className="degree"><span className="degree-val">{ancestors.size}</span><span className="degree-label">total upstream</span></div>
          <div className="degree"><span className="degree-val">{n.rdep_count}</span><span className="degree-label">used by</span></div>
        </div>
        <div className="deps-sub">direct dependencies</div>
        <DepList key={`direct-${n.id}`} items={n.uses} empty="none — axiom" onGoTo={onGoTo} />
        <div className="deps-sub">indirect (transitive) dependencies</div>
        <DepList key={`indirect-${n.id}`} items={indirect} empty="none beyond the direct ones" onGoTo={onGoTo} />
      </div>

      <div className="card">
        <div className="card-title">Complexity</div>
        <div className="metrics-grid">
          <span />
          <span className="col-head dv-help" title="local = this declaration alone">local</span>
          <span className="col-head dv-help" title="total = this declaration plus ALL its ancestors (everything it transitively \uses). ∞ as soon as any ancestor is ∞.">total</span>
          <span className="m-label dv-help" title="ℓ = length (characters) of the informal LaTeX proof. — means no informal proof is written yet.">LaTeX ℓ</span>{charVal(n.proof_size_tex)}{charVal(n.proof_size_tex_total)}
          <span className="m-label dv-help" title="ℓ = length (characters) of the Lean proof. — means the declaration has no (matched) Lean proof yet.">Lean ℓ</span>{charVal(n.proof_size_lean)}{charVal(n.proof_size_lean_total)}
          <span className="m-label dv-help" title="effort = 0 if proved sorry-free in Lean · |LaTeX proof| if only an informal proof exists (work still to formalize) · ∞ if neither (a roadmap hole). total sums it over the dependency cone.">Effort</span>{workVal(n.effort_local)}{workVal(n.effort_total)}
        </div>
      </div>

      <div className="card">
        <div className="sec-hdr">
          <span className="card-title" style={{ margin: 0 }}>LaTeX statement</span>
          <ModChip label="tex" mod={texMod} onOpenLogs={onOpenLogs} onOpenDiffs={onOpenDiffs} />
        </div>
        <div className="latex-rendered">
          <TexFragment tex={n.statement} macros={macros} labels={labels} onNavigate={onOpenBlueprintAt} />
        </div>
      </div>

      <div className="card">
        <div className="sec-hdr">
          <span className="card-title" style={{ margin: 0 }}>LaTeX proof</span>
          <ModChip label="tex" mod={texMod} onOpenLogs={onOpenLogs} onOpenDiffs={onOpenDiffs} />
        </div>
        <div className="latex-rendered">
          <TexFragment tex={n.proof_tex ? n.proof_tex.trim() : ''} macros={macros} labels={labels} onNavigate={onOpenBlueprintAt} />
        </div>
      </div>

      <div className="card">
        <div className="sec-hdr">
          <span className="card-title" style={{ margin: 0 }}>Lean code</span>
          <ModChip label="lean" mod={leanMod} onOpenLogs={onOpenLogs} onOpenDiffs={onOpenDiffs} />
        </div>
        {n.lean_source
          ? <pre className="code-block" dangerouslySetInnerHTML={{ __html: highlightLean(n.lean_source) }} />
          : <pre className="code-block empty">declaration not found</pre>}
      </div>
    </div>
  );
}

// Two range thumbs (min / max) with a live readout. The max thumb at its ceiling
// stores Infinity so unbounded (∞-effort) nodes stay included in the highlight.
function DualRange({ label, min, max, step, value, onChange, fmt, infiniteTop }: {
  label: string; min: number; max: number; step: number;
  value: [number, number]; onChange: (v: [number, number]) => void;
  fmt: (v: number) => string; infiniteTop?: boolean;
}) {
  const [lo, hi] = value;
  const hiVal = hi === Infinity ? max : hi;
  const hiDisp = hi === Infinity ? (infiniteTop ? '∞' : fmt(max)) : fmt(hi);
  const active = lo > min || (hi !== Infinity && hi < max);
  return (
    <div className={`dv-range ${active ? 'dv-range-on' : ''}`} title={`Highlight by ${label}`}>
      <span className="dv-range-lbl">{label}</span>
      <input className="dv-slider" type="range" min={min} max={max} step={step} value={lo}
        onChange={(e) => { const v = Math.min(Number(e.target.value), hiVal); onChange([v, hi]); }} />
      <input className="dv-slider" type="range" min={min} max={max} step={step} value={hiVal}
        onChange={(e) => { const v = Number(e.target.value); onChange([Math.min(lo, v), v >= max ? Infinity : Math.max(v, lo)]); }} />
      <span className="dv-range-val">{fmt(lo)}–{hiDisp}</span>
    </div>
  );
}

const pad: React.CSSProperties = { padding: 24, color: 'var(--text-secondary)' };

// Drag-to-resize a panel. Handle sits on the panel's leading edge, so dragging
// toward the panel (left for the sidebar / up for the bottom rail) grows it.
function useDragResize(initial: number, min: number, max: number, axis: 'x' | 'y') {
  const [size, setSize] = useState(initial);
  const startRef = useRef({ pos: 0, size: 0 });
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    startRef.current = { pos: axis === 'x' ? e.clientX : e.clientY, size };
    const onMove = (ev: MouseEvent) => {
      const delta = (axis === 'x' ? ev.clientX : ev.clientY) - startRef.current.pos;
      setSize(Math.max(min, Math.min(max, startRef.current.size - delta)));
    };
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [size, axis, min, max]);
  return { size, onMouseDown };
}

// Port of leandag's graph.html stylesheet, re-themed to archon's light UI
// tokens (var(--bg-*)/--border/--text-*/--accent…) and scoped under .dv-root.
const DV_CSS = `
.dv-root { display:flex; flex-direction:column; height:100%; min-height:0; background:var(--bg-secondary); font-family:var(--font-sans); }
.dv-root *,.dv-root *::before,.dv-root *::after { box-sizing:border-box; }
.dv-toolbar { background:var(--bg-secondary); min-height:44px; padding:0 16px; display:flex; align-items:center; gap:14px; flex-shrink:0; flex-wrap:wrap; border-bottom:1px solid var(--border); }
.dv-brand { font-size:13px; font-weight:700; color:var(--text-primary); letter-spacing:.03em; }
.dv-stat { font-size:12px; color:var(--text-muted); white-space:nowrap; }
.dv-legend { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.dv-legend .leg-dot { width:10px; height:10px; border-radius:50%; display:inline-block; border:1px solid rgba(0,0,0,.12); }
.dv-legend .leg-bar { width:54px; height:8px; border-radius:4px; display:inline-block; background:linear-gradient(90deg,#facc15,#f59e0b,#f97316); }
.dv-legend .leg-txt { font-size:10px; font-weight:600; color:var(--text-muted); }
.dv-sym-legend { padding-left:10px; margin-left:4px; border-left:1px solid var(--border); gap:8px; }
.dv-warn { font-size:11px; color:var(--red); white-space:nowrap; }
.dv-controls { background:var(--bg-tertiary); min-height:40px; padding:6px 16px; display:flex; align-items:center; gap:10px; flex-shrink:0; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.dv-input,.dv-select { background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border); border-radius:5px; font-size:12px; padding:5px 8px; font-family:var(--font-sans); outline:none; }
.dv-input { width:200px; } .dv-input:focus,.dv-select:focus { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-ring); } .dv-select { max-width:260px; cursor:pointer; }
.dv-chk { font-size:12px; color:var(--text-secondary); display:flex; align-items:center; gap:5px; cursor:pointer; user-select:none; }
.dv-btn { background:var(--bg-primary); color:var(--text-secondary); border:1px solid var(--border); border-radius:5px; font-size:12px; padding:5px 12px; cursor:pointer; }
.dv-btn:hover { background:var(--bg-tertiary); } .dv-btn:disabled { opacity:.6; cursor:default; }
.dv-pill { display:inline-flex; align-items:center; gap:7px; background:var(--accent-bg); color:var(--accent-text); border:1px solid var(--accent-ring); border-radius:12px; font-size:11px; padding:3px 4px 3px 11px; cursor:pointer; max-width:280px; }
.dv-pill .lbl { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .dv-pill .x { font-weight:700; padding:0 6px; border-radius:9px; } .dv-pill:hover .x { background:var(--accent-bg); }
.dv-main { display:flex; flex:1; overflow:hidden; min-height:0; position:relative; }
.dv-graph { flex:1; min-height:0; background:var(--bg-secondary); }
.dv-graph canvas { display:block; }
.dv-hist { color:var(--blue); font-weight:600; }
.dv-git-panel { flex-shrink:0; height:56px; border-top:1px solid var(--border); background:var(--bg-secondary); display:flex; flex-direction:column; overflow:hidden; }
.dv-git-head { display:flex; align-items:center; gap:10px; padding:4px 12px; border-bottom:1px solid var(--border); flex-shrink:0; }
.dv-git-title { font-size:10px; font-weight:600; color:var(--text-muted); text-transform:uppercase; letter-spacing:.3px; }
.dv-git-live { margin-left:auto; padding:2px 8px; font-size:10px; font-weight:600; border:1px solid var(--blue); border-radius:4px; background:var(--accent-bg); color:var(--blue); cursor:pointer; }
.dv-git-live:hover { background:var(--accent-bg-soft); }
.dv-resize-v { width:5px; flex-shrink:0; cursor:col-resize; background:var(--border); transition:background .15s; }
.dv-resize-v:hover { background:var(--blue); }
.dv-resize-h { height:5px; flex-shrink:0; cursor:row-resize; background:var(--border); transition:background .15s; }
.dv-resize-h:hover { background:var(--blue); }
.dv-stats { position:absolute; top:12px; left:12px; z-index:5; background:var(--bg-primary); border:1px solid var(--border); border-radius:8px; padding:10px 12px; min-width:188px; font-size:11px; color:var(--text-secondary); box-shadow:0 4px 16px rgba(15,23,42,.10); }
.dv-stats-head { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.dv-stats h4 { font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted); margin-bottom:7px; }
.dv-stats-head h4 { margin-bottom:0; }
.dv-stats-toggle { width:18px; height:18px; line-height:1; display:flex; align-items:center; justify-content:center; background:var(--bg-tertiary); color:var(--text-secondary); border:1px solid var(--border); border-radius:4px; font-size:14px; cursor:pointer; padding:0; margin-bottom:6px; }
.dv-stats-toggle:hover { background:var(--accent-bg); color:var(--accent-text); }
.dv-stats-show { position:absolute; top:12px; left:12px; z-index:5; background:var(--bg-primary); color:var(--text-secondary); border:1px solid var(--border); border-radius:8px; font-size:11px; font-weight:600; padding:6px 10px; cursor:pointer; box-shadow:0 2px 8px rgba(15,23,42,.08); }
.dv-stats-show:hover { background:var(--bg-tertiary); }
.dv-stats .row { display:flex; justify-content:space-between; gap:14px; line-height:1.7; }
.dv-stats .row .v { font-family:var(--font-mono); color:var(--text-primary); }
.dv-stats .v.done { color:var(--green); } .dv-stats .v.work { color:var(--orange); } .dv-stats .v.inf { color:var(--red); } .dv-stats .v.mathlib { color:#3b82f6; }
.dv-stats .bar { height:6px; border-radius:3px; background:var(--bg-tertiary); margin:7px 0 3px; overflow:hidden; }
.dv-stats .bar > span { display:block; height:100%; background:var(--green); }
.dv-stats .sep { border-top:1px solid var(--border); margin:7px 0; }
.dv-sidebar { width:340px; flex-shrink:0; background:var(--bg-secondary); border-left:1px solid var(--border); display:flex; flex-direction:column; overflow:hidden; }
.dv-sidebar-empty { flex:1; display:flex; align-items:center; justify-content:center; } .dv-sidebar-empty p { font-size:13px; color:var(--text-muted); }
.dv-sidebar-content { flex:1; overflow-y:auto; padding:10px; display:flex; flex-direction:column; gap:8px; }
.dv-root .card { background:var(--bg-primary); border:1px solid var(--border); border-radius:7px; padding:12px; }
.dv-root .card-title { font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted); margin-bottom:10px; }
.dv-root .btn-focus { width:100%; margin-top:10px; background:var(--accent-bg); color:var(--accent-text); border:1px solid var(--accent-ring); border-radius:5px; font-size:11px; font-weight:600; padding:7px; cursor:pointer; letter-spacing:.02em; }
.dv-root .btn-focus:hover { background:var(--accent-bg); border-color:var(--accent); }
.dv-root .node-badges { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:8px; }
.dv-root .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:700; letter-spacing:.04em; }
.dv-root .badge-type { background:var(--accent-bg); color:var(--accent-text); border:1px solid var(--accent-ring); }
.dv-root .badge-proved { background:rgba(16,185,129,.12); color:var(--green); border:1px solid rgba(16,185,129,.3); }
.dv-root .badge-mathlib { background:rgba(59,130,246,.12); color:#2563eb; border:1px solid rgba(59,130,246,.3); }
.dv-root .badge-sorry { background:rgba(234,88,12,.10); color:var(--orange); border:1px solid rgba(234,88,12,.28); }
.dv-root .badge-unproved { background:rgba(220,38,38,.08); color:var(--red); border:1px solid rgba(220,38,38,.25); }
.dv-root .node-title { font-size:15px; font-weight:600; color:var(--text-primary); line-height:1.35; margin-bottom:5px; overflow-wrap:anywhere; }
.dv-root .node-id { font-family:var(--font-mono); font-size:11px; color:var(--text-muted); word-break:break-all; }
.dv-root .node-chapter { font-size:11px; color:var(--text-muted); margin-top:3px; }
.dv-root .lean-ref { font-size:11px; color:var(--text-secondary); margin-top:6px; overflow-wrap:anywhere; } .dv-root .lean-ref code { font-family:var(--font-mono); color:var(--blue); overflow-wrap:anywhere; }
.dv-root .degrees { display:flex; gap:20px; margin-bottom:10px; }
.dv-root .degree { display:flex; flex-direction:column; align-items:center; gap:2px; }
.dv-root .degree-val { font-size:22px; font-weight:700; color:var(--text-primary); line-height:1; }
.dv-root .degree-label { font-size:10px; color:var(--text-muted); text-align:center; }
.dv-root .deps-list { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:8px; }
.dv-root .deps-sub { font-size:10px; color:var(--text-muted); margin:6px 0 4px; letter-spacing:.03em; }
.dv-root .dep-chip { font-family:var(--font-mono); font-size:10px; padding:3px 7px; background:var(--bg-tertiary); border:1px solid var(--border); border-radius:4px; color:var(--blue); cursor:pointer; max-width:100%; overflow-wrap:anywhere; word-break:break-word; text-align:left; }
.dv-root .dep-chip:hover { background:var(--accent-bg); border-color:var(--accent-ring); }
.dv-root .dep-more { font-family:var(--font-mono); font-size:10px; padding:3px 7px; background:transparent; border:1px dashed var(--border); border-radius:4px; color:var(--text-muted); cursor:pointer; }
.dv-root .dep-more:hover { color:var(--text-secondary); border-color:var(--text-muted); }
.dv-root .no-deps { font-size:12px; color:var(--text-muted); font-style:italic; }
.dv-root .metrics-grid { display:grid; grid-template-columns:auto 1fr 1fr; gap:5px 10px; align-items:center; }
.dv-root .col-head { font-size:10px; color:var(--text-muted); text-align:right; font-weight:600; }
.dv-root .m-label { font-size:11px; color:var(--text-muted); }
.dv-root .m-val { font-family:var(--font-mono); font-size:12px; color:var(--text-secondary); text-align:right; }
.dv-root .m-done { color:var(--green); font-weight:700; } .dv-root .m-inf { color:var(--red); font-weight:700; } .dv-root .m-work { color:var(--orange); }
.dv-root .sec-hdr { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:2px 8px; margin-bottom:6px; }
.dv-root .latex-rendered { font-family:var(--font-sans); font-size:13px; line-height:1.7; color:var(--text-secondary); overflow-x:auto; min-height:1.5em; }
.dv-root .latex-rendered .katex-display { margin:6px 0; overflow-x:auto; }
.dv-root .latex-rendered em { font-style:italic; color:var(--text-primary); } .dv-root .latex-rendered strong { font-weight:600; color:var(--text-primary); }
.dv-root .dv-empty-mark { color:var(--text-muted); font-style:italic; }
.dv-root .dv-help { cursor:help; text-decoration:underline dotted var(--text-muted); text-underline-offset:3px; }
.dv-root .mod-chip { display:inline-flex; align-items:center; gap:3px; margin-left:auto; }
.dv-root .mod-lbl { font-size:9.5px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.04em; }
.dv-root .mod-iter, .dv-root .mod-diff { font-family:var(--font-mono); font-size:10px; cursor:pointer; border:1px solid var(--border); background:var(--bg-tertiary); color:var(--text-secondary); padding:0 5px; border-radius:4px; }
.dv-root .mod-iter:hover, .dv-root .mod-diff:hover { border-color:#3b82f6; color:#3b82f6; }
.dv-root .code-block { font-family:var(--font-mono); font-size:11px; line-height:1.6; background:#0d1117; color:#c9d1d9; border:1px solid var(--border-strong); border-radius:5px; padding:10px 12px; white-space:pre; overflow:auto; max-height:220px; margin:0; }
.dv-root .code-block.empty { color:var(--text-muted); font-style:italic; white-space:normal; background:var(--bg-tertiary); }
.dv-root .hl-kw { color:#c678dd; } .dv-root .hl-tactic { color:#61afef; } .dv-root .hl-type { color:#e5c07b; }
.dv-root .hl-comment { color:#7d8590; font-style:italic; } .dv-root .hl-str { color:#98c379; } .dv-root .hl-num { color:#d19a66; }
.dv-range { display:flex; align-items:center; gap:6px; padding:1px 8px; border:1px solid var(--border); border-radius:5px; background:var(--bg-primary); }
.dv-range.dv-range-on { border-color:#3b82f6; }
.dv-range-lbl { font-size:11px; color:var(--text-muted); font-weight:600; text-transform:lowercase; }
.dv-range .dv-slider { width:60px; accent-color:#3b82f6; cursor:pointer; height:14px; }
.dv-range-val { font-family:var(--font-mono); font-size:10px; color:var(--text-secondary); min-width:48px; text-align:right; }
`;
