/**
 * Shared DAG node colouring — the effort/status palette mirrors leandag's
 * exporters (see views/DagView.tsx, which uses the same scale) plus the
 * per-project encoding used by the Meta (union) view.
 *
 * In the single-project DAG, a node's *fill* encodes local effort (green =
 * done, yellow→orange ramp, red = ∞). The Meta view keeps that fill but moves
 * the *border* colour + *shape* onto the project the node belongs to, so a
 * union of several projects stays legible: inside = progress, outline = source.
 */
import type { DagNode } from '../hooks/useDag';

// ── Effort / status palette (mirrors leandag.exporters) ─────────────────────
export const DONE_FILL = '#22c55e', DONE_BORDER = '#15803d';   // effort 0 — formalised
export const MATHLIB_FILL = '#3b82f6', MATHLIB_BORDER = '#1d4ed8'; // \mathlibok — in mathlib
export const INF_FILL = '#ef4444', INF_BORDER = '#7f1d1d';     // effort ∞ — no estimate
const GRAD_HUE_HI = 52; // yellow (smallest finite effort)
const GRAD_HUE_LO = 24; // orange (largest finite effort)

export function hslHex(hue: number, sat: number, light: number): string {
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

export function effortColor(effort: number | null | undefined, maxEffort: number): { background: string; border: string } {
  if (effort === null || effort === undefined) return { background: INF_FILL, border: INF_BORDER };
  if (effort <= 0) return { background: DONE_FILL, border: DONE_BORDER };
  const t = maxEffort > 0 ? Math.min(1, Math.sqrt(effort) / Math.sqrt(maxEffort)) : 0;
  const hue = GRAD_HUE_HI - (GRAD_HUE_HI - GRAD_HUE_LO) * t;
  return { background: hslHex(hue, 0.72, 0.55), border: hslHex(hue, 0.6, 0.38) };
}

export function statusGlyphs(n: DagNode): string {
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

// ── Per-project encoding (Meta / union view) ────────────────────────────────
// Saturated outline colours + distinct vis-network shapes, chosen to read as
// node borders and to stay clear of the status fills (green/blue/red/amber).
// Cycled when there are more projects than entries.
export const PROJECT_BORDERS = [
  '#7c3aed', // violet
  '#0891b2', // cyan
  '#db2777', // pink
  '#b45309', // dark amber
  '#475569', // slate
  '#be123c', // crimson
  '#15803d', // dark green
  '#1e3a8a', // navy
];
export const PROJECT_SHAPES = ['dot', 'square', 'triangle', 'diamond', 'star', 'triangleDown', 'hexagon'];

export interface ProjectStyle { border: string; shape: string; }

/** Stable colour + shape for a project, keyed by its index in the project list. */
export function projectStyle(idx: number): ProjectStyle {
  return {
    border: PROJECT_BORDERS[idx % PROJECT_BORDERS.length],
    shape: PROJECT_SHAPES[idx % PROJECT_SHAPES.length],
  };
}
