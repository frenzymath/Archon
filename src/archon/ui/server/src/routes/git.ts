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

function runGit(gitDir: string, projectPath: string, args: string[]): string {
  const r = spawnSync('git', args, {
    env: { ...process.env, GIT_DIR: gitDir, GIT_WORK_TREE: projectPath },
    cwd: projectPath,
    encoding: 'utf-8',
    timeout: 8000,
  });
  return r.status === 0 ? (r.stdout ?? '') : '';
}

const ARCHON_MSG_RE = /archon\[(\d+)\/([^/\]]+)(?:\/([^\]]+))?\]/;

function parseIter(subject: string): { iteration?: string; phase?: string; fileSlug?: string } {
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
function parseMacros(src: string): Record<string, string> {
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
function loadBlueprintMacros(projectPath: string): Record<string, string> {
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
    // HEAD-decorated refs claim first so the current branch becomes the
    // trunk in the UI (ancestors render on HEAD's lane rather than on a
    // sibling branch's lane).
    const branchAt = new Map<string, string>();
    const cleanRef = (ref: string) => ref.replace(/^HEAD -> /, '').trim();
    const isUsableRef = (clean: string) =>
      !!clean && !clean.startsWith('tag:') && !clean.startsWith('origin/') && clean !== 'HEAD';
    for (const c of commits) {
      for (const ref of c.refs) {
        if (!ref.startsWith('HEAD -> ')) continue;
        const clean = cleanRef(ref);
        if (isUsableRef(clean) && !branchAt.has(c.sha)) branchAt.set(c.sha, clean);
      }
    }
    for (const c of commits) {
      for (const ref of c.refs) {
        const clean = cleanRef(ref);
        if (isUsableRef(clean) && !branchAt.has(c.sha)) branchAt.set(c.sha, clean);
      }
    }

    // Propagate branch from CHILD to parent — an ancestor sits on the
    // lane of the descendant that actually reaches it. Without this, a
    // fork-point commit that happens to be another branch's tip would
    // incorrectly stamp its label onto the sibling branch's commits.
    // --topo-order (newest first): children are at lower indices than
    // their parents, so walking 0..n-1 visits children before parents.
    const childrenOf = new Map<string, string[]>();
    const bySha = new Map<string, GitCommit>();
    for (const c of commits) {
      bySha.set(c.sha, c);
      for (const p of c.parents) {
        const arr = childrenOf.get(p);
        if (arr) arr.push(c.sha);
        else childrenOf.set(p, [c.sha]);
      }
    }
    for (let i = 0; i < commits.length; i++) {
      const c = commits[i];
      if (branchAt.has(c.sha)) continue;
      const kids = childrenOf.get(c.sha) ?? [];
      // Prefer the child that lists this commit as its FIRST parent (the
      // "primary" descendant in git's topology).
      let inherited: string | undefined;
      for (const childSha of kids) {
        const child = bySha.get(childSha);
        if (child && child.parents[0] === c.sha) {
          const cb = branchAt.get(childSha);
          if (cb) { inherited = cb; break; }
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

    return { commits };
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
