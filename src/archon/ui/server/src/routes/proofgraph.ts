/**
 * Proof Graph API v6
 *
 * v6 additions:
 *   - GET /api/proofgraph/logs/:file/:iteration — prover log entries for a file at an iteration
 *     Returns thinking, text, tool_call, tool_result, code_snapshot, session_end events
 *     from .archon/logs/iter-NNN/provers/File_Slug.jsonl
 */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import { countSorryInLean } from '../utils/sorryCount.js';
import { mapIterToCommit, lsLeanFilesAtCommit, showFileAtCommit, hasInnerGit } from '../utils/innerGit.js';
import { latestFileLaneStatus } from '../utils/multilane.js';
import type { ProjectPaths } from './project.js';

// Declaration opener: optional repeatable modifiers, then a kind keyword,
// then the (possibly namespaced) name. `*` on the name group lets anonymous
// `example`/`instance :` act as boundaries without producing a node.
// Inspired by leandag's scanner (LeanScanner._DECL_RE).
const DECL_RE = /^(?:(?:private|protected|noncomputable|irreducible|unsafe|scoped|partial)\s+)*(theorem|lemma|def|instance|class|structure|inductive|abbrev|example)\s+([^\s:(\[{=]*)/;

// Column-0 lines that mean we've left a declaration body — used to trim
// trailing scaffolding (namespace/section/end/open/#check…) that would
// otherwise be swallowed into the previous decl's displayed body.
// Ported from leandag's LeanScanner._OUTSIDE_DECL_RE.
const OUTSIDE_DECL_RE = /^(?:end\b|section\b|namespace\b|variable\b|universe\b|open\b|attribute\b|noncomputable\s+section\b|#check\b|#eval\b|#print\b)/;

interface LD {
  kind: string; name: string; file: string; line: number; endLine: number;
  hasSorry: boolean; sorryCount: number; signature: string; body: string; usedNames: string[];
}

/**
 * Return a comment-masked shadow of `lines`: every character inside a Lean
 * `--` line comment or a (nestable) `/- … -/` block comment is replaced with
 * a space, preserving line count and column positions. Declaration matching
 * runs on the shadow so a `def`/`theorem` sitting inside a comment or
 * docstring is never parsed as a real declaration.
 */
function maskComments(lines: string[]): string[] {
  const out: string[] = [];
  let depth = 0; // block-comment nesting depth
  for (const raw of lines) {
    let res = '';
    let i = 0;
    const n = raw.length;
    while (i < n) {
      if (depth > 0) {
        if (raw[i] === '/' && raw[i + 1] === '-') { depth++; res += '  '; i += 2; continue; }
        if (raw[i] === '-' && raw[i + 1] === '/') { depth--; res += '  '; i += 2; continue; }
        res += ' '; i++; continue;
      }
      if (raw[i] === '-' && raw[i + 1] === '-') { res += ' '.repeat(n - i); break; }
      if (raw[i] === '/' && raw[i + 1] === '-') { depth++; res += '  '; i += 2; continue; }
      res += raw[i]; i++;
    }
    out.push(res);
  }
  return out;
}

/**
 * Trim trailing scaffolding from a decl's body line range. Walks back from
 * `end` (exclusive) past blank lines and any column-0 namespace/section/end/
 * open/#check… lines (detected on the comment-masked shadow) so they don't
 * render as part of the declaration. Returns the new exclusive end line index.
 */
function trimDeclBodyEnd(code: string[], start: number, end: number): number {
  let cut = end;
  for (let i = start + 1; i < end; i++) {
    const codeLine = code[i];
    const stripped = codeLine.trim();
    if (!stripped) continue;
    const atCol0 = codeLine.length > 0 && !/\s/.test(codeLine[0]);
    if (atCol0 && OUTSIDE_DECL_RE.test(stripped)) {
      let j = i;
      while (j > start && !code[j - 1].trim()) j--;
      cut = j;
      break;
    }
  }
  return cut;
}

function parseContent(content: string, rel: string): LD[] {
  const lines = content.split('\n');
  const code = maskComments(lines); // comment-masked shadow for structural decisions
  const sl = new Set(countSorryInLean(content).map(o => o.line));
  const ds: LD[] = []; let i = 0;
  while (i < lines.length) {
    const m = code[i].match(DECL_RE);
    if (!m) { i++; continue; }
    const kind = m[1]; const name = (m[2] || '').replace(/[.,;]+$/, ''); const s = i + 1;
    let e = s, bd = 0;
    for (let j = i; j < lines.length; j++) {
      for (const c of code[j]) { if (c === '{' || c === '⟨') bd++; if (c === '}' || c === '⟩') bd--; }
      if (j > i && bd <= 0 && j + 1 < lines.length && code[j + 1].trim() && DECL_RE.test(code[j + 1])) { e = j + 1; break; }
      e = j + 1;
    }
    // Anonymous `example` / unnamed `instance :` — a structural boundary but
    // not a node the graph should show.
    if (!name) { i = e; continue; }
    // Trim trailing namespace/section/end scaffolding off the body extent.
    e = trimDeclBodyEnd(code, i, e);
    // Trim trailing whitespace + leading docstring of the *next* decl from
    // the current decl's body. Without this, the body includes the start of
    // the next /-- ... -/ block, which makes the UI show an unfinished
    // comment that visually looks like truncated content.
    let bodyEnd = e;
    while (bodyEnd > i + 1 && !lines[bodyEnd - 1].trim()) bodyEnd--;
    if (bodyEnd > i + 1 && lines[bodyEnd - 1].trim().endsWith('-/')) {
      let k = bodyEnd - 1;
      // single-line `/-- ... -/` block
      if (lines[k].trim().startsWith('/--') || lines[k].trim().startsWith('/-')) {
        bodyEnd = k;
      } else {
        // multi-line block: walk back to its `/-` opener
        while (k > i && !lines[k].trim().startsWith('/--') && !lines[k].trim().startsWith('/-')) k--;
        if (k > i) bodyEnd = k;
      }
      while (bodyEnd > i + 1 && !lines[bodyEnd - 1].trim()) bodyEnd--;
    }
    let sc = 0; for (let l = s; l <= e; l++) if (sl.has(l)) sc++;
    const body = lines.slice(i, bodyEnd).join('\n');
    ds.push({ kind, name, file: rel, line: s, endLine: e, hasSorry: sc > 0, sorryCount: sc, signature: lines[i].trim(), body, usedNames: refs(body) });
    i = e;
  }
  return ds;
}
function parseFile(fp: string, rel: string): LD[] { try { return parseContent(fs.readFileSync(fp, 'utf-8'), rel); } catch { return []; } }

function refs(body: string): string[] {
  const KW = new Set(['import','open','namespace','section','end','variable','universe','theorem','lemma','def','instance','class','structure','inductive','abbrev','example','by','where','fun','match','with','if','then','else','let','in','have','show','from','intro','simp','rw','rfl','exact','apply','constructor','cases','induction','sorry','calc','do','return','pure','true','false','Type','Prop','Sort','noncomputable','private','protected','partial','unsafe','mutual']);
  const re = /\b([A-Za-z_][A-Za-z0-9_.']*)\b/g;
  const ns = new Set<string>(); let m;
  while ((m = re.exec(body)) !== null) { const b = m[1].split('.')[0]; if (!KW.has(b) && b.length > 1) ns.add(b); }
  return Array.from(ns);
}

function edges(ds: LD[]) {
  const mp = new Map<string, string>(); for (const d of ds) mp.set(d.name, `${d.file}::${d.name}`);
  const out: { from: string; to: string }[] = []; const seen = new Set<string>();
  for (const d of ds) { const fk = `${d.file}::${d.name}`; for (const r of d.usedNames) { const tk = mp.get(r); if (tk && tk !== fk) { const ek = `${fk}->${tk}`; if (!seen.has(ek)) { seen.add(ek); out.push({ from: fk, to: tk }); } } } }
  return out;
}

function getAllMilestones(ap: string) {
  const dir = path.join(ap, 'proof-journal', 'sessions');
  if (!fs.existsSync(dir)) return new Map<string, { totalAttempts: number; latestStatus: string; sessions: string[]; blocker?: string }>();
  const res = new Map<string, { totalAttempts: number; latestStatus: string; sessions: string[]; blocker?: string }>();
  for (const sd of fs.readdirSync(dir).filter(d => d.startsWith('session_')).sort()) {
    const mf = path.join(dir, sd, 'milestones.jsonl'); if (!fs.existsSync(mf)) continue;
    for (const line of fs.readFileSync(mf, 'utf-8').split('\n')) {
      if (!line.trim()) continue;
      try {
        const m = JSON.parse(line); const t = m.target || {};
        const f = (t.file || '').replace(/\\/g, '/'), th = t.theorem || ''; if (!f || !th) continue;
        const att = Array.isArray(m.attempts) ? m.attempts.length : 0;
        for (const k of [`${f}::${th}`, `${path.basename(f)}::${th}`]) {
          const ex = res.get(k);
          if (ex) { ex.totalAttempts += att; ex.latestStatus = m.status || ex.latestStatus; if (!ex.sessions.includes(sd)) ex.sessions.push(sd); if (m.findings?.blocker) ex.blocker = m.findings.blocker; }
          else res.set(k, { totalAttempts: att, latestStatus: m.status || 'unknown', sessions: [sd], blocker: m.findings?.blocker });
        }
      } catch { /* */ }
    }
  }
  return res;
}

function getMilestonesForNode(ap: string, file: string, theorem: string, maxIter?: string) {
  const dir = path.join(ap, 'proof-journal', 'sessions');
  if (!fs.existsSync(dir)) return [];
  let maxN = Infinity;
  if (maxIter) { const n = parseInt(maxIter.replace('iter-', ''), 10); if (!isNaN(n)) maxN = n; }
  const out: any[] = [];
  for (const sd of fs.readdirSync(dir).filter(d => d.startsWith('session_')).sort()) {
    const sn = parseInt(sd.replace('session_', ''), 10); if (!isNaN(sn) && sn > maxN) continue;
    const mf = path.join(dir, sd, 'milestones.jsonl'); if (!fs.existsSync(mf)) continue;
    for (const line of fs.readFileSync(mf, 'utf-8').split('\n')) {
      if (!line.trim()) continue;
      try {
        const m = JSON.parse(line); const t = m.target || {};
        const mf2 = (t.file || '').replace(/\\/g, '/');
        if ((file.endsWith(mf2) || mf2.endsWith(file) || path.basename(mf2) === path.basename(file)) && t.theorem === theorem)
          out.push({ sessionId: sd, status: m.status || 'unknown', attempts: m.attempts || [], blocker: m.findings?.blocker, nextSteps: m.next_steps, keyLemmas: m.findings?.key_lemmas_used });
      } catch { /* */ }
    }
  }
  return out;
}

/**
 * Load every .lean file tracked in the inner archon git at a given commit
 * — same approach the diff section uses. Returns a `file → content` map.
 *
 * Files that can't be read (rare: git object corruption) are skipped silently.
 * Returns an empty map when no inner git exists.
 */
function loadFilesAtCommit(gitDir: string, pp: string, sha: string): Map<string, string> {
  const out = new Map<string, string>();
  if (!hasInnerGit(gitDir)) return out;
  for (const file of lsLeanFilesAtCommit(gitDir, pp, sha)) {
    const content = showFileAtCommit(gitDir, pp, sha, file);
    if (content !== null) out.set(file, content);
  }
  return out;
}

/** List iteration IDs present in the logs folder (any iter-NNN dir counts). */
function allIters(lp: string): string[] {
  if (!fs.existsSync(lp)) return [];
  return fs.readdirSync(lp)
    .filter(d => d.startsWith('iter-') && fs.statSync(path.join(lp, d)).isDirectory())
    .sort();
}

function bodyMapFromFiles(files: Map<string, string>): Map<string, string> {
  const m = new Map<string, string>();
  for (const [dn, content] of files) {
    for (const d of parseContent(content, dn)) m.set(`${dn}::${d.name}`, d.body);
  }
  return m;
}

function buildTimeline(lp: string, pp: string, gitDir: string) {
  const iters = allIters(lp);
  const commitByIter = mapIterToCommit(gitDir, pp);
  let prevBodies = new Map<string, string>();
  return iters.map(iterDir => {
    let timestamp: string | undefined;
    try { const m = JSON.parse(fs.readFileSync(path.join(lp, iterDir, 'meta.json'), 'utf-8')); timestamp = m.completedAt || m.startedAt; } catch { /* */ }
    // Prefer the git tree at the iter's commit; it's authoritative about which
    // files existed at that point.
    const commit = commitByIter.get(iterDir);
    const files = commit ? loadFilesAtCommit(gitDir, pp, commit.sha) : new Map<string, string>();
    const perFile: Record<string, number> = {};
    const perDecl: Record<string, { hasSorry: boolean; sorryCount: number }> = {};
    let total = 0;
    for (const [dn, content] of files) {
      const sc = countSorryInLean(content).length;
      perFile[dn] = sc; total += sc;
      for (const d of parseContent(content, dn)) perDecl[`${dn}::${d.name}`] = { hasSorry: d.hasSorry, sorryCount: d.sorryCount };
    }
    const curBodies = bodyMapFromFiles(files);
    const changed: string[] = [];
    for (const [id, body] of curBodies) {
      const prev = prevBodies.get(id);
      if (prev === undefined || prev !== body) changed.push(id);
    }
    prevBodies = curBodies;
    return { iteration: iterDir, timestamp, totalSorry: total, perFile, perDeclaration: perDecl, changedDeclarations: changed };
  });
}

/**
 * Build the graph at a given iteration by reading the files tracked in the
 * inner git at that iteration's commit. This is the same source of truth the
 * diff section uses, so file creations/deletions across iterations are
 * reflected correctly.
 */
function buildGraphAt(lp: string, pp: string, gitDir: string, iteration: string) {
  const commitByIter = mapIterToCommit(gitDir, pp);
  const commit = commitByIter.get(iteration);
  const files = commit ? loadFilesAtCommit(gitDir, pp, commit.sha) : new Map<string, string>();
  const allD: LD[] = [];
  for (const [dn, content] of files) allD.push(...parseContent(content, dn));
  const ed = edges(allD);
  const fg: Record<string, { file: string; declarations: string[] }> = {};
  for (const d of allD) { if (!fg[d.file]) fg[d.file] = { file: d.file, declarations: [] }; fg[d.file].declarations.push(d.name); }
  return {
    declarations: allD.map(d => ({ id: `${d.file}::${d.name}`, kind: d.kind, name: d.name, file: d.file, line: d.line, hasSorry: d.hasSorry, sorryCount: d.sorryCount, signature: d.signature, totalAttempts: 0, latestMilestoneStatus: undefined, milestoneSessions: [] as string[], blocker: undefined })),
    edges: ed, files: Object.values(fg),
  };
}

function findDeclAt(lp: string, pp: string, gitDir: string, iter: string, file: string, name: string): LD | undefined {
  const commitByIter = mapIterToCommit(gitDir, pp);
  const commit = commitByIter.get(iter);
  if (commit) {
    const content = showFileAtCommit(gitDir, pp, commit.sha, file);
    if (content !== null) {
      const d = parseContent(content, file).find(d => d.name === name);
      if (d) return d;
    }
  }
  // Fallback to on-disk file for legacy projects with no inner git.
  return parseFile(path.join(pp, file), file).find(d => d.name === name);
}

// ── Prover log reading ────────────────────────────────────────────────

/** Relevant event types to surface in the UI */
// const LOG_EVENTS = new Set(['thinking', 'text', 'tool_call', 'tool_result', 'code_snapshot', 'session_end']);
const LOG_EVENTS = new Set(['thinking']);

/**
 * Read prover log for a file slug at a given iteration.
 * Path: .archon/logs/{iteration}/provers/{FileSlug}.jsonl
 * File slug: "DecouplingMomentCurve/UncertaintyPrinciple.lean" -> "DecouplingMomentCurve_UncertaintyPrinciple"
 */
function readProverLog(lp: string, iteration: string, fileSlug: string) {
  const logPath = path.join(lp, iteration, 'provers', `${fileSlug}.jsonl`);
  if (!fs.existsSync(logPath)) return [];
  const entries: any[] = [];
  try {
    const raw = fs.readFileSync(logPath, 'utf-8');
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue;
      try {
        const e = JSON.parse(line);
        if (!LOG_EVENTS.has(e.event)) continue;
        // Truncate very long content to keep response size sane
        if (e.event === 'tool_result' && typeof e.content === 'string' && e.content.length > 2000) {
          e.content = e.content.slice(0, 2000) + `\n... [truncated, ${e.content.length} chars total]`;
        }
        if (e.event === 'thinking' && typeof e.content === 'string' && e.content.length > 3000) {
          e.content = e.content.slice(0, 3000) + `\n... [truncated, ${e.content.length} chars total]`;
        }
        entries.push(e);
      } catch { /* skip malformed lines */ }
    }
  } catch { /* file not readable */ }
  return entries;
}

function fileToSlug(file: string): string {
  return file.replace(/\.lean$/, '').replace(/\//g, '_');
}

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  // Paths resolved per-request (base project or an allowed peer via `?project=`).
  fastify.get('/api/proofgraph/declarations', async (req) => {
    const { projectPath: pp, archonPath: ap } = req.paths;
    const allD: LD[] = [];
    (function walk(dir: string) { try { for (const e of fs.readdirSync(dir, { withFileTypes: true })) { const f = path.join(dir, e.name); if (e.isDirectory()) { if (!['_lake','.lake','.archon','node_modules','.git'].includes(e.name)) walk(f); } else if (e.isFile() && e.name.endsWith('.lean')) allD.push(...parseFile(f, path.relative(pp, f))); } } catch { /* */ } })(pp);
    const ed = edges(allD); const ms = getAllMilestones(ap);
    const laneStatus = latestFileLaneStatus(ap);
    const fg: Record<string, { file: string; declarations: string[]; laneStatus?: string }> = {};
    for (const d of allD) {
      if (!fg[d.file]) {
        fg[d.file] = { file: d.file, declarations: [] };
        if (laneStatus[d.file]) fg[d.file].laneStatus = laneStatus[d.file];
      }
      fg[d.file].declarations.push(d.name);
    }
    return {
      declarations: allD.map(d => {
        const id = `${d.file}::${d.name}`; let mi = ms.get(id); if (!mi) { for (const [k, v] of ms) { if (k.split('::')[1] === d.name) { mi = v; break; } } }
        return { id, kind: d.kind, name: d.name, file: d.file, line: d.line, hasSorry: d.hasSorry, sorryCount: d.sorryCount, signature: d.signature, totalAttempts: mi?.totalAttempts ?? 0, latestMilestoneStatus: mi?.latestStatus, milestoneSessions: mi?.sessions ?? [], blocker: mi?.blocker };
      }), edges: ed, files: Object.values(fg),
    };
  });

  fastify.get('/api/proofgraph/timeline', async (req) => {
    const { projectPath: pp, archonPath: ap, logsPath: lp } = req.paths;
    return buildTimeline(lp, pp, path.join(ap, 'git-dir'));
  });

  fastify.get<{ Params: { iteration: string } }>('/api/proofgraph/snapshot/:iteration', async (req, reply) => {
    const { projectPath: pp, archonPath: ap, logsPath: lp } = req.paths;
    if (!req.params.iteration.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid' });
    return buildGraphAt(lp, pp, path.join(ap, 'git-dir'), req.params.iteration);
  });

  fastify.get<{ Params: { file: string; name: string }; Querystring: { iteration?: string } }>('/api/proofgraph/node/:file/:name', async (req) => {
    const { projectPath: pp, archonPath: ap, logsPath: lp } = req.paths;
    const gitDir = path.join(ap, 'git-dir');
    const file = decodeURIComponent(req.params.file), { name } = req.params, iter = req.query.iteration;
    const decl = iter ? findDeclAt(lp, pp, gitDir, iter, file, name) : parseFile(path.join(pp, file), file).find(d => d.name === name);
    return {
      declaration: decl ? { id: `${decl.file}::${decl.name}`, kind: decl.kind, name: decl.name, file: decl.file, line: decl.line, endLine: decl.endLine, hasSorry: decl.hasSorry, sorryCount: decl.sorryCount, signature: decl.signature, body: decl.body } : null,
      milestones: getMilestonesForNode(ap, file, name, iter),
    };
  });

  // ── Prover log endpoint ───────────────────────────────────────────
  fastify.get<{ Params: { file: string; iteration: string } }>('/api/proofgraph/logs/:file/:iteration', async (req, reply) => {
    const { logsPath: lp } = req.paths;
    const file = decodeURIComponent(req.params.file);
    const iteration = req.params.iteration;
    if (!iteration.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration' });
    const slug = fileToSlug(file);
    const entries = readProverLog(lp, iteration, slug);

    // Compute summary stats
    let thinkingCount = 0, toolCallCount = 0, textCount = 0, codeSnapshotCount = 0;
    let durationMs: number | undefined, totalCost: number | undefined, numTurns: number | undefined, sessionSummary: string | undefined;
    let startTs: string | undefined, endTs: string | undefined;
    for (const e of entries) {
      if (e.event === 'thinking') thinkingCount++;
      else if (e.event === 'tool_call') toolCallCount++;
      else if (e.event === 'text') textCount++;
      else if (e.event === 'code_snapshot') codeSnapshotCount++;
      else if (e.event === 'session_end') {
        durationMs = e.duration_ms;
        totalCost = e.total_cost_usd;
        numTurns = e.num_turns;
        sessionSummary = e.summary;
      }
      if (!startTs) startTs = e.ts;
      endTs = e.ts;
    }

    return {
      entries,
      stats: { thinkingCount, toolCallCount, textCount, codeSnapshotCount, totalEntries: entries.length, durationMs, totalCost, numTurns, sessionSummary, startTs, endTs }
    };
  });
}