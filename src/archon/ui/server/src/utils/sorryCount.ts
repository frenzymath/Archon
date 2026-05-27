import fs from 'fs';
import path from 'path';

export interface LeanMetrics {
  sorries: number;
  loc: number;           // non-empty lines (comments included)
  locNoComments: number; // non-empty lines, comment lines excluded
  defs: number;          // def / noncomputable def / private def
  lemmas: number;        // lemma / theorem / proposition / corollary
  axioms: number;        // axiom declarations
}

/** Count various metrics from Lean source text in a single pass. */
export function countLeanMetrics(content: string): LeanMetrics {
  const sorries = countSorryInLean(content).length;

  let loc = 0;
  let locNoComments = 0;
  let defs = 0;
  let lemmas = 0;
  let axioms = 0;

  const lines = content.split('\n');
  let inBlockComment = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    loc++;

    // Track block comments (/-  -/) for locNoComments.
    // We do a simplified pass: toggle inBlockComment on /- and -/.
    let isCommentLine = inBlockComment;

    // Check if this line opens or closes a block comment (simplified: just
    // detect if the non-whitespace content is entirely within block comments).
    const tempLine = line;
    let tempInBlock: boolean = inBlockComment;
    let contentOutsideComment = false;
    let ci = 0;
    while (ci < tempLine.length) {
      if (tempInBlock) {
        if (tempLine[ci] === '-' && tempLine[ci + 1] === '/') {
          tempInBlock = false;
          ci += 2;
        } else {
          ci++;
        }
      } else {
        if (tempLine[ci] === '/' && tempLine[ci + 1] === '-') {
          tempInBlock = true;
          ci += 2;
        } else if (tempLine[ci] === '-' && tempLine[ci + 1] === '-') {
          // Rest is line comment
          break;
        } else {
          contentOutsideComment = true;
          ci++;
        }
      }
    }
    inBlockComment = tempInBlock;
    isCommentLine = !contentOutsideComment;

    if (!isCommentLine) {
      locNoComments++;
      // Count declarations (only on non-comment lines).
      if (/^(?:private\s+|protected\s+|noncomputable\s+)*def\s/.test(line)) defs++;
      else if (/^(?:private\s+|protected\s+)*(?:lemma|theorem|proposition|corollary)\s/.test(line)) lemmas++;
      else if (/^(?:private\s+)?axiom\s/.test(line)) axioms++;
    }
  }

  return { sorries, loc, locNoComments, defs, lemmas, axioms };
}

export interface SorryOccurrence {
  line: number;
  column: number;
}

export interface SorryCountFile {
  file: string;
  count: number;
  lines: number[];
  occurrences: SorryOccurrence[];
}

export interface SorryCountResult {
  files: SorryCountFile[];
  total: number;
  partial: boolean;
  warnings: string[];
}

function isIdentChar(ch: string | undefined) {
  return !!ch && /[A-Za-z0-9_!?']/.test(ch);
}

export function countSorryInLean(content: string): SorryOccurrence[] {
  const out: SorryOccurrence[] = [];
  let i = 0;
  let line = 1;
  let col = 1;
  let blockDepth = 0;
  let inString = false;
  let inLineComment = false;

  const bump = (ch: string) => {
    if (ch === '\n') {
      line += 1;
      col = 1;
      inLineComment = false;
    } else {
      col += 1;
    }
  };

  while (i < content.length) {
    const ch = content[i];
    const next = content[i + 1];

    if (inLineComment) {
      bump(ch);
      i += 1;
      continue;
    }

    if (blockDepth > 0) {
      if (ch === '/' && next === '-') {
        blockDepth += 1;
        bump(ch); bump(next);
        i += 2;
        continue;
      }
      if (ch === '-' && next === '/') {
        blockDepth -= 1;
        bump(ch); bump(next);
        i += 2;
        continue;
      }
      bump(ch);
      i += 1;
      continue;
    }

    if (inString) {
      if (ch === '\\' && next) {
        bump(ch); bump(next);
        i += 2;
        continue;
      }
      if (ch === '"') {
        inString = false;
      }
      bump(ch);
      i += 1;
      continue;
    }

    if (ch === '-' && next === '-') {
      inLineComment = true;
      bump(ch); bump(next);
      i += 2;
      continue;
    }

    if (ch === '/' && next === '-') {
      blockDepth = 1;
      bump(ch); bump(next);
      i += 2;
      continue;
    }

    if (ch === '"') {
      inString = true;
      bump(ch);
      i += 1;
      continue;
    }

    if (content.startsWith('sorry', i)) {
      const prev = content[i - 1];
      const after = content[i + 5];
      if (!isIdentChar(prev) && !isIdentChar(after)) {
        out.push({ line, column: col });
        for (const c of 'sorry') bump(c);
        i += 5;
        continue;
      }
    }

    bump(ch);
    i += 1;
  }

  return out;
}

export function countSorriesInProject(projectPath: string): SorryCountResult {
  const files: SorryCountFile[] = [];
  const warnings: string[] = [];
  let partial = false;

  function walk(d: string) {
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch (err) {
      partial = true;
      warnings.push(`Failed to read directory: ${path.relative(projectPath, d) || '.'}`);
      return;
    }

    for (const entry of entries) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) {
        if (['_lake', '.lake', '.archon', 'node_modules'].includes(entry.name)) continue;
        walk(full);
        continue;
      }
      if (!entry.isFile() || !entry.name.endsWith('.lean')) continue;

      let content = '';
      try {
        content = fs.readFileSync(full, 'utf-8');
      } catch {
        partial = true;
        warnings.push(`Failed to read file: ${path.relative(projectPath, full)}`);
        continue;
      }

      const occurrences = countSorryInLean(content);
      if (occurrences.length === 0) continue;

      files.push({
        file: path.relative(projectPath, full),
        count: occurrences.length,
        lines: Array.from(new Set(occurrences.map(o => o.line))),
        occurrences,
      });
    }
  }

  walk(projectPath);
  const total = files.reduce((sum, f) => sum + f.count, 0);
  return { files, total, partial, warnings };
}
