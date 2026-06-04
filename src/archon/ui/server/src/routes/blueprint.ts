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
  hasBlueprint: boolean;
  commit: string | null;
  error: string | null;
}

const CH_DIR = 'blueprint/src/chapters';
const MACROS_DIR = 'blueprint/src/macros';
const CONTENT = 'blueprint/src/content.tex';

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

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { projectPath, archonPath } = paths;
  const gitDir = path.join(archonPath, 'git-dir');

  fastify.get<{ Querystring: { commit?: string } }>(
    '/api/blueprint/chapters',
    async (req) => {
      const commit = req.query.commit?.trim() || null;
      const empty: BlueprintChaptersResponse = {
        chapters: [], macros: {}, hasBlueprint: false, commit, error: null,
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

      return { chapters, macros, hasBlueprint: true, commit, error: null };
    },
  );
}
