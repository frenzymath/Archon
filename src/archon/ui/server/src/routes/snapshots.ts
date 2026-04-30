/**
 * Snapshots API — code snapshot browsing and diff computation
 *
 * Endpoints:
 *   GET /api/iterations/:id/snapshots
 *     → list all prover snapshot dirs with step counts
 *
 *   GET /api/iterations/:id/snapshots/:prover
 *     → list baseline + all steps for a prover, with file sizes
 *
 *   GET /api/iterations/:id/snapshots/:prover/:file
 *     → read a single snapshot file (baseline.lean or step-NNN.lean)
 *
 *   GET /api/iterations/:id/snapshots/:prover/diff/:step
 *     → compute unified diff between step N-1 (or baseline) and step N
 *
 *   GET /api/iterations/:id/snapshots/:prover/diff-all
 *     → return all diffs in sequence (for playback preloading)
 */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProjectPaths } from './project.js';
import { mapIterToCommit, lsLeanFilesAtCommit, showFileAtCommit, hasInnerGit } from '../utils/innerGit.js';

/** Canonical slug → Lean file mapping: "Foo/Bar.lean" ↔ "Foo_Bar". */
function slugToFile(slug: string): string {
  return slug.replace(/_/g, '/') + '.lean';
}
function fileToSlug(file: string): string {
  return file.replace(/\.lean$/, '').replace(/\//g, '_');
}

interface SnapshotProverSummary {
  slug: string;
  file?: string;       // from meta.json provers.<slug>.file
  stepCount: number;
  hasBaseline: boolean;
}

interface SnapshotFileInfo {
  name: string;
  size: number;
  modified: string;
}

interface DiffResult {
  step: number;
  fromFile: string;
  toFile: string;
  diff: string;          // unified diff text
  addedLines: number;
  removedLines: number;
}

/** Simple unified diff implementation (no external deps) */
function computeUnifiedDiff(
  oldText: string, newText: string,
  oldLabel: string, newLabel: string,
  contextLines = 3,
): string {
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');

  // LCS-based diff (simple O(n*m) for reasonable file sizes)
  const m = oldLines.length;
  const n = newLines.length;

  // Build edit script using Myers-like approach (simplified)
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (oldLines[i] === newLines[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  // Extract hunks
  interface Change { type: 'keep' | 'add' | 'remove'; oldIdx: number; newIdx: number; line: string }
  const changes: Change[] = [];
  let i = 0, j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && oldLines[i] === newLines[j]) {
      changes.push({ type: 'keep', oldIdx: i, newIdx: j, line: oldLines[i] });
      i++; j++;
    } else if (i < m && (j >= n || dp[i + 1][j] >= dp[i][j + 1])) {
      changes.push({ type: 'remove', oldIdx: i, newIdx: j, line: oldLines[i] });
      i++;
    } else if (j < n) {
      changes.push({ type: 'add', oldIdx: i, newIdx: j, line: newLines[j] });
      j++;
    }
  }

  // Group into hunks with context
  const hunks: Change[][] = [];
  let currentHunk: Change[] = [];
  let lastChangeIdx = -999;

  for (let k = 0; k < changes.length; k++) {
    const c = changes[k];
    if (c.type !== 'keep') {
      // Include context before
      const ctxStart = Math.max(lastChangeIdx === -999 ? 0 : lastChangeIdx + 1, k - contextLines);
      if (currentHunk.length > 0 && ctxStart > lastChangeIdx + 1 + contextLines) {
        // Gap too large, start new hunk
        // Add trailing context to current hunk
        for (let t = lastChangeIdx + 1; t < Math.min(lastChangeIdx + 1 + contextLines, k); t++) {
          if (changes[t].type === 'keep') currentHunk.push(changes[t]);
        }
        hunks.push(currentHunk);
        currentHunk = [];
      }
      // Add leading context
      for (let t = ctxStart; t < k; t++) {
        if (!currentHunk.includes(changes[t])) currentHunk.push(changes[t]);
      }
      currentHunk.push(c);
      lastChangeIdx = k;
    }
  }
  // Trailing context for last hunk
  if (currentHunk.length > 0) {
    for (let t = lastChangeIdx + 1; t < Math.min(lastChangeIdx + 1 + contextLines, changes.length); t++) {
      if (changes[t].type === 'keep') currentHunk.push(changes[t]);
    }
    hunks.push(currentHunk);
  }

  if (hunks.length === 0) return '';

  // Format unified diff
  let result = `--- ${oldLabel}\n+++ ${newLabel}\n`;
  for (const hunk of hunks) {
    const firstOld = hunk.find(c => c.type !== 'add')?.oldIdx ?? 0;
    const firstNew = hunk.find(c => c.type !== 'remove')?.newIdx ?? 0;
    const oldCount = hunk.filter(c => c.type !== 'add').length;
    const newCount = hunk.filter(c => c.type !== 'remove').length;
    result += `@@ -${firstOld + 1},${oldCount} +${firstNew + 1},${newCount} @@\n`;
    for (const c of hunk) {
      if (c.type === 'keep') result += ` ${c.line}\n`;
      else if (c.type === 'remove') result += `-${c.line}\n`;
      else if (c.type === 'add') result += `+${c.line}\n`;
    }
  }

  return result;
}

function countDiffLines(diff: string): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) added++;
    else if (line.startsWith('-') && !line.startsWith('---')) removed++;
  }
  return { added, removed };
}

/** Aggregate snapshots across all iterations, grouped by file slug */
interface FileSnapshotSummary {
  slug: string;
  file?: string;            // e.g. "SnapshotTest/Nat.lean"
  iterations: {
    id: string;             // "iter-001"
    stepCount: number;
    hasBaseline: boolean;
  }[];
  totalSteps: number;
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { logsPath, archonPath, projectPath } = paths;
  const gitDir = path.join(archonPath, 'git-dir');

  /** Sanitize URL params to prevent path traversal */
  const safe = (s: string) => path.basename(s);

  // --- Cross-iteration file-centric API ---

  /**
   * List every Lean file that has ever appeared in the project — either as a
   * prover snapshot dir under any iter-NNN/snapshots/ or as a file in the git
   * tree of any iter-NNN commit. For each file we list every iter-NNN group
   * (even those without a snapshots/<slug> dir) so the timeline can later
   * gap-fill from git or synthesise empty snapshots.
   */
  fastify.get('/api/snapshot-files', async () => {
    if (!fs.existsSync(logsPath)) return [];

    const iterDirs = fs.readdirSync(logsPath)
      .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
      .sort();

    const commitByIter = mapIterToCommit(gitDir, projectPath);
    const fileMap = new Map<string, FileSnapshotSummary>();

    // Record one entry per (slug, iter) across all iters.
    const recordIter = (slug: string, file: string | undefined, iter: string, stepCount: number, hasBaseline: boolean) => {
      let entry = fileMap.get(slug);
      if (!entry) {
        entry = { slug, file, iterations: [], totalSteps: 0 };
        fileMap.set(slug, entry);
      }
      if (!entry.file && file) entry.file = file;
      entry.iterations.push({ id: iter, stepCount, hasBaseline });
      entry.totalSteps += stepCount;
    };

    // Pass 1: collect the union of all slugs — from snapshot dirs AND from git
    // trees at every iter commit. This is what makes a file visible across
    // iterations where the prover never touched it.
    const allSlugs = new Set<string>();
    const slugToFileHint = new Map<string, string>();

    for (const iterDir of iterDirs) {
      // Slugs from snapshots/<slug>/
      const snapshotsDir = path.join(logsPath, iterDir, 'snapshots');
      if (fs.existsSync(snapshotsDir)) {
        for (const slug of fs.readdirSync(snapshotsDir)) {
          if (fs.statSync(path.join(snapshotsDir, slug)).isDirectory()) allSlugs.add(slug);
        }
      }

      // meta.json "provers.<slug>.file" gives us the slug→real-path mapping.
      try {
        const meta = JSON.parse(fs.readFileSync(path.join(logsPath, iterDir, 'meta.json'), 'utf-8'));
        const provers = (meta.provers || {}) as Record<string, { file?: string }>;
        for (const [slug, p] of Object.entries(provers)) {
          allSlugs.add(slug);
          if (p?.file) slugToFileHint.set(slug, p.file);
        }
      } catch { /* ignore */ }

      // Slugs from .lean files in the git tree at this iter's commit.
      const commit = commitByIter.get(iterDir);
      if (commit) {
        for (const file of lsLeanFilesAtCommit(gitDir, projectPath, commit.sha)) {
          const slug = fileToSlug(file);
          allSlugs.add(slug);
          if (!slugToFileHint.has(slug)) slugToFileHint.set(slug, file);
        }
      }
    }

    // Pass 2: for each (slug, iter), record either the real snapshot counts
    // or a placeholder (stepCount=0, hasBaseline=false). The timeline handler
    // is responsible for synthesising content from git or an empty string.
    for (const iterDir of iterDirs) {
      const snapshotsDir = path.join(logsPath, iterDir, 'snapshots');
      let provers: Record<string, { file?: string }> = {};
      try {
        const meta = JSON.parse(fs.readFileSync(path.join(logsPath, iterDir, 'meta.json'), 'utf-8'));
        provers = (meta.provers || {}) as Record<string, { file?: string }>;
      } catch { /* ignore */ }

      for (const slug of allSlugs) {
        const slugPath = path.join(snapshotsDir, slug);
        let stepCount = 0;
        let hasBaseline = false;
        if (fs.existsSync(slugPath) && fs.statSync(slugPath).isDirectory()) {
          const files = fs.readdirSync(slugPath);
          stepCount = files.filter(f => f.startsWith('step-') && f.endsWith('.lean')).length;
          hasBaseline = files.includes('baseline.lean');
        }
        recordIter(slug, provers[slug]?.file ?? slugToFileHint.get(slug), iterDir, stepCount, hasBaseline);
      }
    }

    return Array.from(fileMap.values()).sort((a, b) => a.slug.localeCompare(b.slug));
  });

  /**
   * Get the full timeline for a file across all iterations.
   *
   * For each iteration we emit one or more entries:
   *   - If the iteration has `snapshots/<slug>/` (= the prover worked on this
   *     file), emit every snapshot file it contains (baseline + each step).
   *   - Else, try to read the file at the iteration's inner-git commit and
   *     emit a single synthetic "baseline" entry carrying that content.
   *   - Else (file didn't exist at that commit, or no inner git), emit a
   *     single synthetic empty entry — so the diff view reads the iteration
   *     as "file created" / "file deleted" instead of skipping it.
   */
  fastify.get<{ Params: { slug: string } }>(
    '/api/snapshot-files/:slug/timeline',
    async (req, reply) => {
      const { slug } = req.params;
      if (!fs.existsSync(logsPath)) return [];
      const safeSlug = safe(slug);

      const iterDirs = fs.readdirSync(logsPath)
        .filter(d => d.startsWith('iter-') && fs.statSync(path.join(logsPath, d)).isDirectory())
        .sort();

      // Resolve slug → Lean file path. Prefer meta.json's provers.<slug>.file;
      // fall back to the canonical slug→path transform.
      let leanFile: string | undefined;
      for (const iterDir of iterDirs) {
        try {
          const meta = JSON.parse(fs.readFileSync(path.join(logsPath, iterDir, 'meta.json'), 'utf-8'));
          const f = (meta.provers || {})[safeSlug]?.file as string | undefined;
          if (f) { leanFile = f; break; }
        } catch { /* ignore */ }
      }
      if (!leanFile) leanFile = slugToFile(safeSlug);

      const commitByIter = mapIterToCommit(gitDir, projectPath);
      const gitAvailable = hasInnerGit(gitDir);

      interface TLEntry {
        iteration: string;
        step: number;
        file: string;
        ts?: string;
        proverLog?: string;
        sourceFile?: string;
        synthetic?: boolean;  // true = content came from git or was an empty placeholder
        diff?: string;
        addedLines?: number;
        removedLines?: number;
      }

      const timeline: TLEntry[] = [];
      const contentByIndex: (string | null)[] = [];

      for (const iterDir of iterDirs) {
        const snapDir = path.join(logsPath, iterDir, 'snapshots', safeSlug);
        const hasRealSnapshots = fs.existsSync(snapDir) && fs.statSync(snapDir).isDirectory();

        if (hasRealSnapshots) {
          // Collect per-step ts/sourceFile provenance from the prover jsonl.
          const tsMap = new Map<number, string>();
          const sourceFileMap = new Map<number, string>();
          const proverJsonlPath = path.join(logsPath, iterDir, 'provers', `${safeSlug}.jsonl`);
          if (fs.existsSync(proverJsonlPath)) {
            try {
              for (const line of fs.readFileSync(proverJsonlPath, 'utf-8').split('\n').filter(Boolean)) {
                const entry = JSON.parse(line);
                if (entry.event === 'code_snapshot' && entry.step) {
                  if (entry.ts) tsMap.set(entry.step, entry.ts);
                  if (entry.file) sourceFileMap.set(entry.step, entry.file);
                }
              }
            } catch { /* ignore */ }
          }

          const allFiles = fs.readdirSync(snapDir)
            .filter(f => f.endsWith('.lean'))
            .sort();

          for (const fname of allFiles) {
            const content = fs.readFileSync(path.join(snapDir, fname), 'utf-8');
            const step = fname === 'baseline.lean' ? 0 : parseInt(fname.replace('step-', '').replace('.lean', ''), 10);
            timeline.push({
              iteration: iterDir,
              step,
              file: fname,
              ts: tsMap.get(step),
              proverLog: safeSlug,
              sourceFile: sourceFileMap.get(step),
            });
            contentByIndex.push(content);
          }
        } else {
          // No snapshots for this iter. Try to pull content from the iter's git commit.
          const commit = commitByIter.get(iterDir);
          const gitContent = gitAvailable && commit
            ? showFileAtCommit(gitDir, projectPath, commit.sha, leanFile)
            : null;
          const content = gitContent ?? '';  // null → file didn't exist, treat as empty
          timeline.push({
            iteration: iterDir,
            step: 0,
            file: 'baseline.lean',
            proverLog: safeSlug,
            sourceFile: leanFile,
            synthetic: true,
          });
          contentByIndex.push(content);
        }
      }

      // Second pass: compute diffs against previous entry once all content is known.
      for (let i = 0; i < timeline.length; i++) {
        const cur = contentByIndex[i];
        const prev = i > 0 ? contentByIndex[i - 1] : null;
        if (prev !== null && cur !== null && cur !== prev) {
          const diff = computeUnifiedDiff(prev, cur, 'previous', timeline[i].file);
          const counts = countDiffLines(diff);
          timeline[i].diff = diff;
          timeline[i].addedLines = counts.added;
          timeline[i].removedLines = counts.removed;
        }
      }

      return timeline;
    },
  );

  /**
   * Read a snapshot file for a specific file+iteration.
   *
   * When no real snapshot exists for `(slug, iteration)` we fall back to the
   * git tree at the iteration's commit, and finally to empty content — this
   * matches the synthetic entries produced by the /timeline endpoint so the
   * diff view always has something to render.
   */
  fastify.get<{ Params: { slug: string; iteration: string; file: string } }>(
    '/api/snapshot-files/:slug/:iteration/:file',
    async (req, reply) => {
      const { slug, iteration, file: fileName } = req.params;
      const safeSlug = safe(slug);
      const safeIter = safe(iteration);
      const safeFile = safe(fileName);
      const filePath = path.join(logsPath, safeIter, 'snapshots', safeSlug, safeFile);
      if (fs.existsSync(filePath)) {
        return { name: safeFile, iteration: safeIter, content: fs.readFileSync(filePath, 'utf-8') };
      }

      // Synthetic fallback: try the git tree at the iteration's commit.
      let leanFile: string | undefined;
      try {
        const meta = JSON.parse(fs.readFileSync(path.join(logsPath, safeIter, 'meta.json'), 'utf-8'));
        leanFile = (meta.provers || {})[safeSlug]?.file;
      } catch { /* ignore */ }
      if (!leanFile) leanFile = slugToFile(safeSlug);

      const commitByIter = mapIterToCommit(gitDir, projectPath);
      const commit = commitByIter.get(safeIter);
      const gitContent = commit && hasInnerGit(gitDir)
        ? showFileAtCommit(gitDir, projectPath, commit.sha, leanFile)
        : null;

      return { name: safeFile, iteration: safeIter, content: gitContent ?? '' };
    },
  );

  // --- Per-iteration snapshot APIs (kept for compatibility) ---

  // List all prover snapshot dirs for an iteration
  fastify.get<{ Params: { id: string } }>(
    '/api/iterations/:id/snapshots',
    async (req, reply) => {
      const { id } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });

      const snapshotsDir = path.join(logsPath, safe(id), 'snapshots');
      if (!fs.existsSync(snapshotsDir)) return [];

      // Read meta.json for prover file mapping
      let provers: Record<string, { file: string }> = {};
      try {
        const meta = JSON.parse(fs.readFileSync(path.join(logsPath, safe(id), 'meta.json'), 'utf-8'));
        provers = meta.provers || {};
      } catch { /* ignore */ }

      const result: SnapshotProverSummary[] = [];
      for (const dir of fs.readdirSync(snapshotsDir).sort()) {
        const dirPath = path.join(snapshotsDir, dir);
        if (!fs.statSync(dirPath).isDirectory()) continue;
        const files = fs.readdirSync(dirPath);
        const stepCount = files.filter(f => f.startsWith('step-') && f.endsWith('.lean')).length;
        const hasBaseline = files.includes('baseline.lean');
        result.push({
          slug: dir,
          file: provers[dir]?.file,
          stepCount,
          hasBaseline,
        });
      }
      return result;
    },
  );

  // List files in a prover's snapshot dir
  fastify.get<{ Params: { id: string; prover: string } }>(
    '/api/iterations/:id/snapshots/:prover',
    async (req, reply) => {
      const { id, prover } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });

      const proverSnapDir = path.join(logsPath, safe(id), 'snapshots', safe(prover));
      if (!fs.existsSync(proverSnapDir)) return reply.status(404).send({ error: 'No snapshots' });

      const files: SnapshotFileInfo[] = [];
      for (const f of fs.readdirSync(proverSnapDir).filter(f => f.endsWith('.lean')).sort()) {
        const stat = fs.statSync(path.join(proverSnapDir, f));
        files.push({ name: f, size: stat.size, modified: stat.mtime.toISOString() });
      }
      return files;
    },
  );

  // Read a single snapshot file
  fastify.get<{ Params: { id: string; prover: string; file: string } }>(
    '/api/iterations/:id/snapshots/:prover/:file',
    async (req, reply) => {
      const { id, prover, file: fileName } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });

      const safeFile = path.basename(fileName);
      const filePath = path.join(logsPath, safe(id), 'snapshots', safe(prover), safeFile);
      if (!fs.existsSync(filePath)) return reply.status(404).send({ error: 'File not found' });

      return { name: safeFile, content: fs.readFileSync(filePath, 'utf-8') };
    },
  );

  // Compute diff between step N-1 (or baseline) and step N
  fastify.get<{ Params: { id: string; prover: string; step: string } }>(
    '/api/iterations/:id/snapshots/:prover/diff/:step',
    async (req, reply) => {
      const { id, prover, step: stepStr } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });

      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step < 1) return reply.status(400).send({ error: 'Invalid step number' });

      const snapDir = path.join(logsPath, safe(id), 'snapshots', safe(prover));
      if (!fs.existsSync(snapDir)) return reply.status(404).send({ error: 'No snapshots' });

      const stepPadded = step.toString().padStart(3, '0');
      const toFile = `step-${stepPadded}.lean`;
      const toPath = path.join(snapDir, toFile);
      if (!fs.existsSync(toPath)) return reply.status(404).send({ error: `Step ${step} not found` });

      // Determine the "from" file
      let fromFile: string;
      if (step === 1) {
        fromFile = 'baseline.lean';
      } else {
        const prevPadded = (step - 1).toString().padStart(3, '0');
        fromFile = `step-${prevPadded}.lean`;
      }
      const fromPath = path.join(snapDir, fromFile);
      if (!fs.existsSync(fromPath)) return reply.status(404).send({ error: `Previous file ${fromFile} not found` });

      const oldText = fs.readFileSync(fromPath, 'utf-8');
      const newText = fs.readFileSync(toPath, 'utf-8');
      const diff = computeUnifiedDiff(oldText, newText, `a/${fromFile}`, `b/${toFile}`);
      const { added, removed } = countDiffLines(diff);

      return { step, fromFile, toFile, diff, addedLines: added, removedLines: removed } as DiffResult;
    },
  );

  // Return all diffs in sequence for playback preloading
  fastify.get<{ Params: { id: string; prover: string } }>(
    '/api/iterations/:id/snapshots/:prover/diff-all',
    async (req, reply) => {
      const { id, prover } = req.params;
      if (!id.startsWith('iter-')) return reply.status(400).send({ error: 'Invalid iteration id' });

      const snapDir = path.join(logsPath, safe(id), 'snapshots', safe(prover));
      if (!fs.existsSync(snapDir)) return reply.status(404).send({ error: 'No snapshots' });

      const stepFiles = fs.readdirSync(snapDir)
        .filter(f => f.startsWith('step-') && f.endsWith('.lean'))
        .sort();

      const diffs: DiffResult[] = [];
      for (let i = 0; i < stepFiles.length; i++) {
        const fromFile = i === 0 ? 'baseline.lean' : stepFiles[i - 1];
        const toFile = stepFiles[i];
        const fromPath = path.join(snapDir, fromFile);
        const toPath = path.join(snapDir, toFile);
        if (!fs.existsSync(fromPath) || !fs.existsSync(toPath)) continue;

        const oldText = fs.readFileSync(fromPath, 'utf-8');
        const newText = fs.readFileSync(toPath, 'utf-8');
        const diff = computeUnifiedDiff(oldText, newText, `a/${fromFile}`, `b/${toFile}`);
        const { added, removed } = countDiffLines(diff);
        diffs.push({ step: i + 1, fromFile, toFile, diff, addedLines: added, removedLines: removed });
      }

      return diffs;
    },
  );
}
