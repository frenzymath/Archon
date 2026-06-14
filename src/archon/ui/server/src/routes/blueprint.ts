/**
 * Blueprint chapters API — serves the whole blueprint as ordered chapters
 * (raw LaTeX, comments preserved) for the dashboard's Blueprint reading view.
 *
 * Distinct from `/api/blueprint` (git.ts), which extracts a single declaration
 * block for the DAG sidebar. This returns *every* chapter in reading order plus
 * the merged macro map, with optional `?commit=<sha>` time-travel via the inner
 * archon git — mirroring the DAG page's historical builds. Comments are left in
 * the tex on purpose: the client surfaces `% SOURCE` / `% NOTE` lines as
 * expandable annotations rather than discarding them.
 */
import path from 'path';
import fs from 'fs';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { runGit, parseMacros, loadBlueprintMacros } from './git.js';

interface Chapter { slug: string; title: string; tex: string; }
interface BlueprintChaptersResponse {
  chapters: Chapter[];
  macros: Record<string, string>;
  docTitle: string | null;
  docAuthor: string | null;
  hasBlueprint: boolean;
  commit: string | null;
  error: string | null;
}

const CH_DIR = 'blueprint/src/chapters';
const MACROS_DIR = 'blueprint/src/macros';
const CONTENT = 'blueprint/src/content.tex';
// Where \title / \author typically live (the leanblueprint entry preambles).
const TITLE_CANDIDATES = [
  'blueprint/src/web.tex',
  'blueprint/src/print.tex',
  'blueprint/src/content.tex',
];

/** Extract the balanced-brace argument of `\<cmd>{...}` from `src`, or null. */
function bracedArg(src: string, cmd: string): string | null {
  const at = src.indexOf(`\\${cmd}`);
  if (at < 0) return null;
  let i = src.indexOf('{', at);
  if (i < 0) return null;
  let depth = 0;
  const start = i + 1;
  for (; i < src.length; i++) {
    if (src[i] === '\\') { i++; continue; }
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return src.slice(start, i).trim(); }
  }
  return null;
}

/** Fallback display name when a chapter has no `\chapter{}`/`\section{}`. */
function humanize(slug: string): string {
  return slug.replace(/_/g, ' / ').replace(/-/g, ' ');
}

function titleOf(tex: string, slug: string): string {
  const m = tex.match(/\\(?:chapter|section)\*?\s*\{([^{}]*)\}/);
  return m ? m[1].trim() : humanize(slug);
}

/** Drop the leading chapter/section heading — the client shows it separately. */
function stripHeading(tex: string): string {
  return tex.replace(/\\(?:chapter|section)\*?\s*\{[^{}]*\}\s*/, '');
}

export function register(fastify: FastifyInstance, _paths: ProjectPaths) {
  fastify.get<{ Querystring: { commit?: string } }>(
    '/api/blueprint/chapters',
    async (req) => {
      const { projectPath, archonPath } = req.paths;
      const gitDir = path.join(archonPath, 'git-dir');
      const commit = req.query.commit?.trim() || null;
      const empty: BlueprintChaptersResponse = {
        chapters: [], macros: {}, docTitle: null, docAuthor: null,
        hasBlueprint: false, commit, error: null,
      };

      // Read a repo-relative path from disk (live) or `git show` (historical).
      const readAt = (rel: string): string | null => {
        if (commit) {
          const out = runGit(gitDir, projectPath, ['show', `${commit}:${rel}`]);
          return out || null;
        }
        const full = path.join(projectPath, rel);
        try { return fs.existsSync(full) ? fs.readFileSync(full, 'utf-8') : null; }
        catch { return null; }
      };
      // List the `*.tex` basenames in a repo-relative dir, disk or historical.
      const listTex = (dirRel: string): string[] => {
        if (commit) {
          const out = runGit(gitDir, projectPath,
            ['ls-tree', '--name-only', commit, `${dirRel}/`]);
          return out.split('\n').map(s => s.trim())
            .filter(s => s.endsWith('.tex')).map(s => path.posix.basename(s));
        }
        const full = path.join(projectPath, dirRel);
        try {
          if (!fs.existsSync(full) || !fs.statSync(full).isDirectory()) return [];
          return fs.readdirSync(full).filter(f => f.endsWith('.tex'));
        } catch { return []; }
      };

      const chapterFiles = listTex(CH_DIR);
      if (!chapterFiles.length) {
        return commit
          ? { ...empty, error: `No blueprint chapters at commit ${commit.slice(0, 7)}.` }
          : empty;
      }

      // Title / author for the intro page (first entry file that has them).
      let docTitle: string | null = null;
      let docAuthor: string | null = null;
      for (const rel of TITLE_CANDIDATES) {
        const src = readAt(rel);
        if (!src) continue;
        docTitle = docTitle ?? bracedArg(src, 'title');
        docAuthor = docAuthor ?? bracedArg(src, 'author');
        if (docTitle) break;
      }
      const bySlug = new Map(chapterFiles.map(f => [f.replace(/\.tex$/, ''), f]));

      // Reading order from content.tex `\input{...}`; any unreferenced chapter
      // is appended so nothing is silently hidden.
      const order: string[] = [];
      const content = readAt(CONTENT);
      if (content) {
        const re = /\\input\s*\{([^{}]+)\}/g;
        let m: RegExpExecArray | null;
        while ((m = re.exec(content)) !== null) {
          const slug = path.posix.basename(m[1].trim()).replace(/\.tex$/, '');
          if (bySlug.has(slug) && !order.includes(slug)) order.push(slug);
        }
      }
      for (const slug of bySlug.keys()) if (!order.includes(slug)) order.push(slug);

      const chapters: Chapter[] = [];
      for (const slug of order) {
        const raw = readAt(`${CH_DIR}/${bySlug.get(slug)}`);
        if (raw == null) continue;
        chapters.push({ slug, title: titleOf(raw, slug), tex: stripHeading(raw) });
      }

      // Macros: from disk (live) or each macros/*.tex at the commit.
      let macros: Record<string, string> = {};
      if (commit) {
        for (const f of listTex(MACROS_DIR)) {
          const src = readAt(`${MACROS_DIR}/${f}`);
          if (src) Object.assign(macros, parseMacros(src));
        }
      } else {
        macros = loadBlueprintMacros(projectPath);
      }
      // Chapters may carry their own \providecommand/\newcommand (e.g. a
      // chapter-local \fatsemi) — collect those too so KaTeX sees them.
      // \providecommand semantics: macros/*.tex definitions win on collision.
      for (const ch of chapters) {
        const chMacros = parseMacros(ch.tex);
        for (const k of Object.keys(chMacros)) if (!(k in macros)) macros[k] = chMacros[k];
      }

      return { chapters, macros, docTitle, docAuthor, hasBlueprint: true, commit, error: null };
    },
  );
}
