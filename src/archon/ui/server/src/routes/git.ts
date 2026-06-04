/** Git log API — exposes the inner archon git repo (.archon/git-dir) to the UI */
import { spawnSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';

export interface GitCommit {
  sha: string;
  shortSha: string;
  subject: string;
  date: string;
  parents: string[];
  refs: string[];
  branch?: string;
  iteration?: string;
  phase?: string;
  fileSlug?: string;
}

export function runGit(gitDir: string, projectPath: string, args: string[]): string {
  const r = spawnSync('git', args, {
    env: { ...process.env, GIT_DIR: gitDir, GIT_WORK_TREE: projectPath },
    cwd: projectPath,
    encoding: 'utf-8',
    timeout: 8000,
    // Default maxBuffer is 1 MB — a full `log --name-only` over a long inner
    // history exceeds that and the child gets killed (status null, output
    // silently dropped). 64 MB matches the dag-graph CLI call's headroom.
    maxBuffer: 64 * 1024 * 1024,
  });
  return r.status === 0 ? (r.stdout ?? '') : '';
}

const ARCHON_MSG_RE = /archon\[(\d+)\/([^/\]]+)(?:\/([^\]]+))?\]/;

export function parseIter(subject: string): { iteration?: string; phase?: string; fileSlug?: string } {
  const m = subject.match(ARCHON_MSG_RE);
  if (!m) return {};
  const num = parseInt(m[1], 10);
  return {
    iteration: `iter-${String(num).padStart(3, '0')}`,
    phase: m[2],
    fileSlug: m[3] as string | undefined,
  };
}

const LOG_EVENTS = new Set(['thinking', 'text', 'tool_call', 'tool_result', 'session_end']);

/** Locate the matching closing brace for `src[openIdx] === '{'`. */
function matchBrace(src: string, openIdx: number): number {
  if (src[openIdx] !== '{') return -1;
  let depth = 1;
  for (let i = openIdx + 1; i < src.length; i++) {
    if (src[i] === '\\' && i + 1 < src.length) { i++; continue; }
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return i; }
  }
  return -1;
}

/**
 * Strip `%` line comments from a LaTeX source. Honours `\%` as literal `%`.
 * Preserves newlines so offsets stay useful for downstream parsing.
 */
function stripTexComments(src: string): string {
  return src.split('\n').map(line => {
    let out = '';
    for (let i = 0; i < line.length; i++) {
      if (line[i] === '%' && (i === 0 || line[i - 1] !== '\\')) break;
      out += line[i];
    }
    return out;
  }).join('\n');
}

/**
 * Parse `\newcommand`/`\renewcommand`/`\providecommand`/`\DeclareMathOperator`
 * definitions out of a LaTeX source. Returns a KaTeX-compatible macro map
 * keyed by the command (with leading backslash). Unknown / unparseable macros
 * are skipped silently — a handful of exotic definitions shouldn't stop the
 * rest from rendering.
 */
export function parseMacros(src: string): Record<string, string> {
  const out: Record<string, string> = {};
  const source = stripTexComments(src);
  const re = /\\(newcommand|renewcommand|providecommand|DeclareMathOperator)\*?\s*/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(source)) !== null) {
    const cmdType = m[1];
    let i = re.lastIndex;

    // Command name: `\foo` or `{\foo}`
    let name: string | null = null;
    if (source[i] === '{') {
      const close = matchBrace(source, i);
      if (close === -1) continue;
      const inside = source.slice(i + 1, close).trim();
      const nm = inside.match(/^\\([A-Za-z@]+)$/);
      if (nm) name = nm[1];
      i = close + 1;
    } else if (source[i] === '\\') {
      const nm = source.slice(i).match(/^\\([A-Za-z@]+)/);
      if (!nm) continue;
      name = nm[1];
      i += nm[0].length;
    }
    if (!name) continue;

    // Optional [N] arg count — KaTeX auto-detects #N, so we just skip it.
    while (i < source.length && /\s/.test(source[i])) i++;
    if (source[i] === '[') {
      const close = source.indexOf(']', i);
      if (close === -1) continue;
      i = close + 1;
    }
    // Optional [default] for optional args — also skip.
    while (i < source.length && /\s/.test(source[i])) i++;
    if (source[i] === '[') {
      const close = source.indexOf(']', i);
      if (close === -1) continue;
      i = close + 1;
    }
    while (i < source.length && /\s/.test(source[i])) i++;

    // Body: balanced braces.
    if (source[i] !== '{') continue;
    const close = matchBrace(source, i);
    if (close === -1) continue;
    let body = source.slice(i + 1, close);
    re.lastIndex = close + 1;

    if (cmdType === 'DeclareMathOperator') {
      body = `\\operatorname{${body}}`;
    }

    out[`\\${name}`] = body;
  }
  return out;
}

/** Read every .tex file in `blueprint/src/macros/` and merge macro definitions. */
export function loadBlueprintMacros(projectPath: string): Record<string, string> {
  const macrosDir = path.join(projectPath, 'blueprint', 'src', 'macros');
  if (!fs.existsSync(macrosDir) || !fs.statSync(macrosDir).isDirectory()) return {};
  const merged: Record<string, string> = {};
  for (const entry of fs.readdirSync(macrosDir)) {
    if (!entry.endsWith('.tex')) continue;
    const full = path.join(macrosDir, entry);
    try {
      const content = fs.readFileSync(full, 'utf-8');
      Object.assign(merged, parseMacros(content));
    } catch { /* unreadable .tex file, skip */ }
  }
  return merged;
}

function readPhaseLog(logsPath: string, iteration: string, phase: string): unknown[] {
  const logFile = path.join(logsPath, iteration, `${phase}.jsonl`);
  if (!fs.existsSync(logFile)) return [];
  const entries: unknown[] = [];
  try {
    for (const line of fs.readFileSync(logFile, 'utf-8').split('\n')) {
      if (!line.trim()) continue;
      try {
        const e = JSON.parse(line);
        if (!LOG_EVENTS.has(e.event)) continue;
        if (e.event === 'thinking' && typeof e.content === 'string' && e.content.length > 3000)
          e.content = e.content.slice(0, 3000) + '\n... [truncated]';
        entries.push(e);
      } catch { /* skip malformed */ }
    }
  } catch { /* file not readable */ }
  return entries;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { projectPath, archonPath, logsPath } = paths;
  const gitDir = path.join(archonPath, 'git-dir');

  /** Full commit log from the inner archon git repo */
  fastify.get('/api/git/log', async (_, reply) => {
    if (!fs.existsSync(gitDir)) return reply.status(404).send({ commits: [] });

    // %x01 = field separator (SOH), %x02 = record separator (STX)
    const raw = runGit(gitDir, projectPath, [
      'log', '--all', '--topo-order',
      '--format=%H%x01%h%x01%s%x01%ai%x01%P%x01%D%x02',
    ]);
    if (!raw.trim()) return { commits: [] };

    const commits: GitCommit[] = [];
    for (const record of raw.split('\x02')) {
      const trimmed = record.trim();
      if (!trimmed) continue;
      const parts = trimmed.split('\x01');
      if (parts.length < 6) continue;
      const [sha, shortSha, subject, date, parentsRaw, refsRaw] = parts;
      const parents = parentsRaw?.trim() ? parentsRaw.trim().split(' ').filter(Boolean) : [];
      const refs = refsRaw?.trim()
        ? refsRaw.split(',').map(r => r.trim()).filter(Boolean)
        : [];
      const { iteration, phase, fileSlug } = parseIter(subject ?? '');
      commits.push({ sha, shortSha, subject: subject ?? '', date, parents, refs, iteration, phase, fileSlug });
    }

    // Assign a primary branch to each commit from its ref decorations.
    //
    // The naive approach — claim by any ref the commit carries — gets
    // wrong answers when a `lane/*` branch tip happens to sit on a
    // commit that is also reachable from main. Concretely:
    //
    //   main:     A — B — C — D — E (HEAD)
    //                   ↑
    //                   lane/anthropic, lane/kimi (both stuck here
    //                   from a prior multilane round; never advanced)
    //
    // C carries only `lane/*` refs (it has no main ref because main's
    // tip is E). The naive ref-claim loop labels C as `lane/anthropic`,
    // and propagation then carries that lane label backward to A and
    // B too. The visibility filter drops everything → user sees only
    // D and E.
    //
    // The fix is to give branch labels in *priority order*: HEAD's
    // branch first (walks the entire main ancestry along first
    // parents, claiming every commit as `main`), then non-lane branch
    // tips, then lane branch tips last. By the time lane refs are
    // considered, every commit reachable from main is already claimed,
    // so lane refs only stick to commits that are genuinely lane-only
    // work (forward of main).
    const branchAt = new Map<string, string>();
    const bySha = new Map<string, GitCommit>();
    for (const c of commits) bySha.set(c.sha, c);

    const cleanRef = (ref: string) => ref.replace(/^HEAD -> /, '').trim();
    const isUsableRef = (clean: string) =>
      !!clean && !clean.startsWith('tag:') && !clean.startsWith('origin/') && clean !== 'HEAD';
    const isLane = (b: string | undefined) => !!b && b.startsWith('lane/');

    // Walk first-parent ancestry from `tip`, claiming each unclaimed
    // commit for `branch`.
    function claimFirstParentChain(tip: string, branch: string) {
      let cur: string | undefined = tip;
      const seen = new Set<string>();
      while (cur && !seen.has(cur)) {
        seen.add(cur);
        if (!branchAt.has(cur)) branchAt.set(cur, branch);
        const next = bySha.get(cur);
        cur = next?.parents[0];
      }
    }

    // Pass 1: HEAD's branch first. This is `main` in the inner-archon
    // git repo by default but the user can branch off via `archon
    // branch …`, in which case HEAD points at that user branch.
    for (const c of commits) {
      for (const ref of c.refs) {
        if (!ref.startsWith('HEAD -> ')) continue;
        const clean = cleanRef(ref);
        if (isUsableRef(clean)) claimFirstParentChain(c.sha, clean);
      }
    }

    // Pass 2: every other non-lane branch tip claims its first-parent
    // ancestry too. User-created branches from `archon branch` land
    // here.
    for (const c of commits) {
      for (const ref of c.refs) {
        const clean = cleanRef(ref);
        if (!isUsableRef(clean) || isLane(clean)) continue;
        claimFirstParentChain(c.sha, clean);
      }
    }

    // Pass 3: lane tips claim their *new* (unclaimed) work. Anything
    // already claimed by main / a user branch stays where it was.
    for (const c of commits) {
      for (const ref of c.refs) {
        const clean = cleanRef(ref);
        if (!isUsableRef(clean) || !isLane(clean)) continue;
        claimFirstParentChain(c.sha, clean);
      }
    }

    // Build the children index for the propagation fallback below.
    const childrenOf = new Map<string, string[]>();
    for (const c of commits) {
      for (const p of c.parents) {
        const arr = childrenOf.get(p);
        if (arr) arr.push(c.sha);
        else childrenOf.set(p, [c.sha]);
      }
    }
    // The first-parent ancestry walks above already cover everything
    // that's reachable from a tip. The fallback below catches commits
    // that are only reachable via SECOND-parent edges (e.g. merge
    // commits) — rare in Archon's history but worth handling.
    for (let i = 0; i < commits.length; i++) {
      const c = commits[i];
      if (branchAt.has(c.sha)) continue;
      const kids = childrenOf.get(c.sha) ?? [];
      let inherited: string | undefined;
      // Pass 1: first-parent children with a non-lane branch.
      for (const childSha of kids) {
        const child = bySha.get(childSha);
        if (child && child.parents[0] === c.sha) {
          const cb = branchAt.get(childSha);
          if (cb && !isLane(cb)) { inherited = cb; break; }
        }
      }
      // Pass 2: any child with a non-lane branch (e.g. user-created
      // branch from `archon branch`).
      if (!inherited) {
        for (const childSha of kids) {
          const cb = branchAt.get(childSha);
          if (cb && !isLane(cb)) { inherited = cb; break; }
        }
      }
      // Pass 3: any child at all. Only reached when the commit has
      // exclusively lane descendants — in that case the lane label is
      // the right answer.
      if (!inherited) {
        for (const childSha of kids) {
          const child = bySha.get(childSha);
          if (child && child.parents[0] === c.sha) {
            const cb = branchAt.get(childSha);
            if (cb) { inherited = cb; break; }
          }
        }
      }
      if (!inherited) {
        for (const childSha of kids) {
          const cb = branchAt.get(childSha);
          if (cb) { inherited = cb; break; }
        }
      }
      if (inherited) branchAt.set(c.sha, inherited);
    }

    for (const c of commits) c.branch = branchAt.get(c.sha) ?? 'main';

    // 1. Hide multilane branches to avoid clutter.
    // 2. Keep the latest phase commit per (branch, iteration). Deduping
    //    by iteration ALONE drops every branch except the one whose
    //    representative happens to come first in topo order, so two
    //    branches that share iteration numbers (a normal occurrence
    //    when one branch was forked off another) end up disconnected
    //    in the graph — the loser branch has no visible nodes at all,
    //    so the renderer has nothing to draw an edge to. Per-branch
    //    dedup keeps each branch's own iter-NNN node.
    const seenBranchIters = new Set<string>();

    const visible = commits.filter(c => {
      // Drop lane branches entirely
      if ((c.branch ?? '').startsWith('lane/')) return false;

      // Keep commits that aren't tied to an iteration (like initial repo setup)
      if (!c.iteration) return true;

      // Because commits are newest-first, the first time we see a
      // (branch, iteration) pair it's the final phase of that
      // iteration on that branch. Keep it, and drop the earlier
      // phases of the same iteration on the same branch.
      const key = `${c.branch ?? 'main'}\x00${c.iteration}`;
      if (seenBranchIters.has(key)) return false;

      seenBranchIters.add(key);
      return true;
    });

    // Rewrite parents so they only reference SHAs we kept. Without
    // this, `c.parents` still names the (filtered-out) intermediate
    // phase commits of the previous iteration, and the client's edge
    // renderer (which looks up parent SHAs in a position map built
    // from `visible` only) finds nothing and silently draws no edge
    // between adjacent iterations. For each parent slot we BFS through
    // the original ancestry and substitute the nearest visible commit
    // (preserving slot order, which the merge-commit bezier renderer
    // depends on).
    const visibleShas = new Set(visible.map(c => c.sha));
    function nearestVisibleAncestor(startSha: string): string | null {
      const seen = new Set<string>();
      let frontier: string[] = [startSha];
      while (frontier.length) {
        const next: string[] = [];
        for (const sha of frontier) {
          if (seen.has(sha)) continue;
          seen.add(sha);
          if (visibleShas.has(sha)) return sha;
          const node = bySha.get(sha);
          if (!node) continue;
          for (const p of node.parents) next.push(p);
        }
        frontier = next;
      }
      return null;
    }
    for (const c of visible) {
      // Skip commits whose parents are already all visible — keeps the
      // payload stable for projects with no filtered iterations.
      if (c.parents.every(p => visibleShas.has(p))) continue;
      const rewritten: string[] = [];
      for (const p of c.parents) {
        const target = visibleShas.has(p) ? p : nearestVisibleAncestor(p);
        if (target && !rewritten.includes(target)) rewritten.push(target);
      }
      c.parents = rewritten;
    }

    return { commits: visible };
  });

  /**
   * HEAD of the inner archon git repo (for "Overview" / "Journal" / "Diffs" badges).
   * Returns { commit: null } when no inner git exists (legacy projects) — never 404s,
   * so the UI can render unconditionally without branching on status codes.
   */
  fastify.get('/api/git/head', async () => {
    if (!fs.existsSync(gitDir)) return { commit: null };
    const raw = runGit(gitDir, projectPath, [
      'log', '-1', '--format=%H%x01%h%x01%s%x01%ai%x01%D',
    ]);
    if (!raw.trim()) return { commit: null };
    const [sha, shortSha, subject, date, refsRaw] = raw.trim().split('\x01');
    const refs = refsRaw?.trim()
      ? refsRaw.split(',').map(r => r.trim()).filter(Boolean)
      : [];
    const branch = refs
      .map(r => r.replace(/^HEAD -> /, '').trim())
      .find(r => !r.startsWith('tag:') && !r.startsWith('origin/') && r !== 'HEAD');
    const { iteration, phase } = parseIter(subject ?? '');
    return {
      commit: { sha, shortSha, subject, date, branch: branch ?? 'main', iteration, phase },
    };
  });

  /** Phase logs for non-prover phases (plan, refactor, review, finalize) */
  fastify.get<{ Params: { iteration: string; phase: string } }>(
    '/api/git/phase-logs/:iteration/:phase',
    async (req, reply) => {
      const { iteration, phase } = req.params;
      if (!iteration.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration' });
      const entries = readPhaseLog(logsPath, iteration, phase);
      return { entries };
    }
  );

  /**
   * Blueprint LaTeX block for a declaration.
   *
   * The declaration `name` reported by the Lean parser is just the last
   * identifier (e.g. `my_thm`) and has no namespace prefix, but blueprints
   * routinely reference the fully-qualified form (e.g. `\lean{Alpha.my_thm}`).
   * We therefore match `\lean{...}` where `...` is either exactly `name` or
   * ends with `.name`, and we look in the per-chapter .tex file first, then
   * fall back to scanning every chapter .tex file if that misses.
   */
  fastify.get<{ Querystring: { file?: string; name?: string } }>(
    '/api/blueprint',
    async (req, reply) => {
      const { file, name } = req.query;
      if (!file || !name) return reply.status(400).send({ error: 'Missing file or name' });

      const escapeReg = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const leanTagRe = new RegExp(`\\\\lean\\{\\s*(?:[A-Za-z0-9_.']+\\.)?${escapeReg(name)}\\s*\\}`);

      function extractBlock(texContent: string): string | null {
        const match = leanTagRe.exec(texContent);
        if (!match) return null;
        const idx = match.index;
        const envRe = /\\begin\{(theorem|lemma|definition|remark|proposition|corollary)\}/g;
        let bestStart = -1;
        let envName = 'theorem';
        let m: RegExpExecArray | null;
        while ((m = envRe.exec(texContent)) !== null) {
          if (m.index <= idx) { bestStart = m.index; envName = m[1]; }
        }
        if (bestStart < 0) return null;
        const endTag = `\\end{${envName}}`;
        const endIdx = texContent.indexOf(endTag, bestStart);
        if (endIdx < 0) return null;
        let blockEnd = endIdx + endTag.length;

        // If a \begin{proof}...\end{proof} immediately follows (only whitespace
        // between), include it — that's the informal proof sketch the plan
        // agent writes and the prover uses as the source of truth.
        const afterStmt = texContent.slice(blockEnd);
        const proofMatch = afterStmt.match(/^\s*\\begin\{proof\}/);
        if (proofMatch) {
          const proofStart = blockEnd + (proofMatch[0].length - '\\begin{proof}'.length);
          const proofEndTag = '\\end{proof}';
          const proofEndIdx = texContent.indexOf(proofEndTag, proofStart);
          if (proofEndIdx >= 0) blockEnd = proofEndIdx + proofEndTag.length;
        }

        return texContent.slice(bestStart, blockEnd);
      }

      const chaptersDir = path.join(projectPath, 'blueprint', 'src', 'chapters');
      const macros = loadBlueprintMacros(projectPath);

      // 1. Try the per-file chapter first (e.g. Algebra/Foo.lean → Algebra_Foo.tex).
      const slug = file.replace(/\.lean$/, '').replace(/\//g, '_');
      const primary = path.join(chaptersDir, `${slug}.tex`);
      if (fs.existsSync(primary)) {
        const block = extractBlock(fs.readFileSync(primary, 'utf-8'));
        if (block) return { tex: block, macros };
      }

      // 2. Fall back to any other chapter file — the same declaration may have
      //    been documented in a different module's chapter.
      if (fs.existsSync(chaptersDir)) {
        for (const entry of fs.readdirSync(chaptersDir)) {
          if (!entry.endsWith('.tex') || entry === `${slug}.tex`) continue;
          const full = path.join(chaptersDir, entry);
          if (!fs.statSync(full).isFile()) continue;
          const block = extractBlock(fs.readFileSync(full, 'utf-8'));
          if (block) return { tex: block, macros };
        }
      }

      return { tex: null, macros };
    }
  );
}
