/**
 * BlueprintRendered — render a leanblueprint LaTeX snippet as HTML.
 *
 * Covers the subset of LaTeX that appears in blueprint chapters, which is
 * essentially:
 *   • amsthm-style environments (theorem, lemma, definition, proof, …)
 *   • inline and display math ($…$, \(…\), $$…$$, \[…\])
 *   • metadata commands (\lean{}, \label{}, \uses{}, \leanok, \notready)
 *   • basic text markup (\textbf, \emph, \textit, \texttt)
 *   • references (\ref{}, \cref{}) rendered as inline keys
 *
 * This isn't a full LaTeX parser — it's the minimum viable plasTeX output
 * for a sidebar preview, mirroring what `blueprint serve` shows but without
 * requiring the Python plasTeX toolchain to run.
 */
import { useMemo, type ReactNode } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import styles from './BlueprintRendered.module.css';

const ENV_LABELS: Record<string, string> = {
  theorem: 'Theorem',
  lemma: 'Lemma',
  proposition: 'Proposition',
  corollary: 'Corollary',
  definition: 'Definition',
  remark: 'Remark',
  example: 'Example',
  proof: 'Proof',
  notation: 'Notation',
  convention: 'Convention',
};

type Node =
  | { type: 'text'; value: string }
  | { type: 'math'; value: string; display: boolean }
  | { type: 'strong'; children: Node[] }
  | { type: 'em'; children: Node[] }
  | { type: 'code'; value: string }
  | { type: 'ref'; value: string }
  | { type: 'env'; name: string; meta: Meta; children: Node[] };

interface Meta {
  lean?: string;
  label?: string;
  uses: string[];
  leanok: boolean;
  notready: boolean;
}

/** Strip `%` line comments — leanblueprint allows LaTeX comments inside envs. */
function stripComments(src: string): string {
  return src
    .split('\n')
    .map(line => {
      // A % starts a comment unless escaped. We don't need full tokenisation here.
      let out = '';
      for (let i = 0; i < line.length; i++) {
        if (line[i] === '%' && (i === 0 || line[i - 1] !== '\\')) break;
        out += line[i];
      }
      return out;
    })
    .join('\n');
}

/**
 * Find the matching `\end{name}` for a `\begin{name}` starting at `startIdx`.
 * Returns the index right after `\end{name}`, or -1 if unbalanced.
 */
function findEndOfEnv(src: string, startIdx: number, name: string): number {
  const beginTag = `\\begin{${name}}`;
  const endTag = `\\end{${name}}`;
  let depth = 1;
  let i = startIdx;
  while (i < src.length) {
    const nextBegin = src.indexOf(beginTag, i);
    const nextEnd = src.indexOf(endTag, i);
    if (nextEnd === -1) return -1;
    if (nextBegin !== -1 && nextBegin < nextEnd) {
      depth++;
      i = nextBegin + beginTag.length;
    } else {
      depth--;
      if (depth === 0) return nextEnd + endTag.length;
      i = nextEnd + endTag.length;
    }
  }
  return -1;
}

/** Extract + strip leanblueprint metadata commands (\lean, \label, …) from a body. */
function extractMeta(body: string): { body: string; meta: Meta } {
  const meta: Meta = { uses: [], leanok: false, notready: false };

  body = body.replace(/\\lean\s*\{([^{}]*)\}/g, (_, v) => {
    meta.lean = v.trim();
    return '';
  });
  body = body.replace(/\\label\s*\{([^{}]*)\}/g, (_, v) => {
    meta.label = v.trim();
    return '';
  });
  body = body.replace(/\\uses\s*\{([^{}]*)\}/g, (_, v) => {
    for (const tok of v.split(',').map((s: string) => s.trim()).filter(Boolean)) meta.uses.push(tok);
    return '';
  });
  body = body.replace(/\\leanok\b/g, () => {
    meta.leanok = true;
    return '';
  });
  body = body.replace(/\\notready\b/g, () => {
    meta.notready = true;
    return '';
  });

  return { body, meta };
}

/** Parse inline text with text-mode commands, math, and nested envs. */
function parseInline(src: string): Node[] {
  const out: Node[] = [];
  let i = 0;

  const pushText = (s: string) => {
    if (!s) return;
    const last = out[out.length - 1];
    if (last && last.type === 'text') last.value += s;
    else out.push({ type: 'text', value: s });
  };

  while (i < src.length) {
    // Display math: $$…$$ or \[…\]
    if (src.startsWith('$$', i)) {
      const end = src.indexOf('$$', i + 2);
      if (end !== -1) {
        out.push({ type: 'math', value: src.slice(i + 2, end), display: true });
        i = end + 2;
        continue;
      }
    }
    if (src.startsWith('\\[', i)) {
      const end = src.indexOf('\\]', i + 2);
      if (end !== -1) {
        out.push({ type: 'math', value: src.slice(i + 2, end), display: true });
        i = end + 2;
        continue;
      }
    }

    // Inline math: $…$ or \(…\)
    if (src[i] === '$') {
      const end = src.indexOf('$', i + 1);
      if (end !== -1) {
        out.push({ type: 'math', value: src.slice(i + 1, end), display: false });
        i = end + 1;
        continue;
      }
    }
    if (src.startsWith('\\(', i)) {
      const end = src.indexOf('\\)', i + 2);
      if (end !== -1) {
        out.push({ type: 'math', value: src.slice(i + 2, end), display: false });
        i = end + 2;
        continue;
      }
    }

    // Nested environment
    if (src.startsWith('\\begin{', i)) {
      const tagEnd = src.indexOf('}', i + 7);
      if (tagEnd !== -1) {
        const name = src.slice(i + 7, tagEnd);
        const afterBegin = tagEnd + 1;
        const endIdx = findEndOfEnv(src, afterBegin, name);
        if (endIdx !== -1) {
          const inner = src.slice(afterBegin, endIdx - `\\end{${name}}`.length);
          const { body, meta } = extractMeta(inner);
          out.push({ type: 'env', name, meta, children: parseInline(body) });
          i = endIdx;
          continue;
        }
      }
    }

    // Text markup: \textbf{...}, \emph{...}, \textit{...}, \texttt{...}
    const markupMatch = /^\\(textbf|emph|textit|texttt)\s*\{/.exec(src.slice(i));
    if (markupMatch) {
      const cmd = markupMatch[1];
      const braceStart = i + markupMatch[0].length;
      const close = findMatchingBrace(src, braceStart - 1);
      if (close !== -1) {
        const inner = src.slice(braceStart, close);
        if (cmd === 'textbf') out.push({ type: 'strong', children: parseInline(inner) });
        else if (cmd === 'texttt') out.push({ type: 'code', value: inner });
        else out.push({ type: 'em', children: parseInline(inner) });
        i = close + 1;
        continue;
      }
    }

    // References: \ref{X}, \cref{X}, \Cref{X}, \eqref{X}
    const refMatch = /^\\(ref|cref|Cref|eqref)\s*\{([^{}]*)\}/.exec(src.slice(i));
    if (refMatch) {
      out.push({ type: 'ref', value: refMatch[2].trim() });
      i += refMatch[0].length;
      continue;
    }

    // A stray backslash command we don't recognise — drop the command,
    // keep the braces' content (common-case degradation for unknown macros).
    if (src[i] === '\\') {
      const m = /^\\([A-Za-z]+)\s*/.exec(src.slice(i));
      if (m) {
        i += m[0].length;
        if (src[i] === '{') {
          const close = findMatchingBrace(src, i);
          if (close !== -1) {
            out.push(...parseInline(src.slice(i + 1, close)));
            i = close + 1;
            continue;
          }
        }
        continue;
      }
      // Escaped char: \$ \% \{ \} — just the literal
      if (i + 1 < src.length) {
        pushText(src[i + 1]);
        i += 2;
        continue;
      }
    }

    pushText(src[i]);
    i++;
  }

  return out;
}

function findMatchingBrace(src: string, openIdx: number): number {
  if (src[openIdx] !== '{') return -1;
  let depth = 1;
  for (let i = openIdx + 1; i < src.length; i++) {
    if (src[i] === '\\' && i + 1 < src.length) { i++; continue; }
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) return i; }
  }
  return -1;
}

function renderMath(tex: string, display: boolean, macros?: Record<string, string>): string {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      strict: 'ignore',
      // Pass the user's custom macros (\newcommand / \DeclareMathOperator
      // etc. from blueprint/src/macros/*.tex). KaTeX reads #1, #2, … from
      // the body so we don't need to declare arity.
      macros: macros ?? {},
      trust: false,
    });
  } catch {
    return `<code>${escapeHtml(`$${display ? '$' : ''}${tex}${display ? '$' : ''}$`)}</code>`;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Split plain text into paragraphs on blank lines. */
function paragraphise(nodes: Node[]): Node[][] {
  const paras: Node[][] = [[]];
  for (const n of nodes) {
    if (n.type === 'text') {
      const parts = n.value.split(/\n\s*\n/);
      parts.forEach((part, idx) => {
        if (idx > 0) paras.push([]);
        if (part) paras[paras.length - 1].push({ type: 'text', value: part });
      });
    } else {
      paras[paras.length - 1].push(n);
    }
  }
  return paras.filter(p => p.length > 0);
}

function renderNode(n: Node, key: string, macros?: Record<string, string>): JSX.Element {
  if (n.type === 'text') {
    return <span key={key}>{n.value.replace(/\s+/g, ' ')}</span>;
  }
  if (n.type === 'math') {
    return (
      <span
        key={key}
        className={n.display ? styles.displayMath : styles.inlineMath}
        dangerouslySetInnerHTML={{ __html: renderMath(n.value, n.display, macros) }}
      />
    );
  }
  if (n.type === 'strong') {
    return <strong key={key}>{n.children.map((c, i) => renderNode(c, `${key}-${i}`, macros))}</strong>;
  }
  if (n.type === 'em') {
    return <em key={key}>{n.children.map((c, i) => renderNode(c, `${key}-${i}`, macros))}</em>;
  }
  if (n.type === 'code') {
    return <code key={key} className={styles.texttt}>{n.value}</code>;
  }
  if (n.type === 'ref') {
    return <span key={key} className={styles.ref}>{n.value}</span>;
  }
  // n.type === 'env'
  const label = ENV_LABELS[n.name] ?? n.name[0].toUpperCase() + n.name.slice(1);
  const paras = paragraphise(n.children);
  return (
    <div key={key} className={`${styles.env} ${styles[`env-${n.name}`] ?? ''}`}>
      <div className={styles.envHead}>
        <span className={styles.envLabel}>{label}.</span>
        {n.meta.leanok && <span className={styles.badgeOk} title="Formalisation complete (\leanok)">✓ leanok</span>}
        {n.meta.notready && <span className={styles.badgeNotReady} title="\notready">not ready</span>}
        {n.meta.lean && <span className={styles.badgeLean} title="Lean declaration">{n.meta.lean}</span>}
        {n.meta.uses.length > 0 && (
          <span className={styles.uses}>
            uses: {n.meta.uses.map((u, i) => (
              <span key={i} className={styles.ref}>{u}</span>
            )).reduce<ReactNode[]>((acc, el, i) => {
              if (i > 0) acc.push(', ');
              acc.push(el);
              return acc;
            }, [])}
          </span>
        )}
      </div>
      <div className={styles.envBody}>
        {paras.map((p, pi) => (
          <p key={pi}>{p.map((c, ci) => renderNode(c, `p${pi}-${ci}`, macros))}</p>
        ))}
      </div>
    </div>
  );
}

export default function BlueprintRendered({
  tex,
  macros,
}: {
  tex: string;
  macros?: Record<string, string>;
}) {
  const nodes = useMemo(() => parseInline(stripComments(tex)), [tex]);
  const paras = paragraphise(nodes);
  return (
    <div className={styles.root}>
      {paras.map((p, pi) => {
        // If a paragraph is a single env, render it directly; else wrap in <p>.
        if (p.length === 1 && p[0].type === 'env') return renderNode(p[0], `top-${pi}`, macros);
        return <p key={pi}>{p.map((c, ci) => renderNode(c, `top-${pi}-${ci}`, macros))}</p>;
      })}
    </div>
  );
}
