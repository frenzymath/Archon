import katex from 'katex';

/** Minimal markdown → HTML converter.
 *
 * Soft newlines inside a paragraph collapse to a single space (CommonMark
 * behavior), so wrapped prose isn't broken mid-sentence. Blank lines start
 * a new block. HTML comments (`<!-- ... -->`) are stripped — they exist in
 * the templates as authoring guidance, not user-facing content. Math is
 * rendered with KaTeX: `$$...$$` for display, `$...$` for inline.
 *
 * Supports: ATX headings (# through ######), bullet and ordered lists,
 * blockquotes, horizontal rules, fenced code blocks, inline code, tables,
 * bold, italic, strikethrough, links, images, and math. */
export function markdownToHtml(content: string): string {
  if (!content) return '';

  // 0. Strip HTML comments — authoring guidance only.
  let result = content.replace(/<!--[\s\S]*?-->/g, '');

  // 1. Pull out fenced code blocks, math, and tables and replace them with
  //    placeholders so block-level paragraph splitting doesn't touch them
  //    and so their contents are immune to inline markdown transforms.
  const blocks: string[] = [];
  const stash = (html: string) => {
    blocks.push(html);
    return `\x00BLOCK${blocks.length - 1}\x00`;
  };

  result = result.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const cls = lang ? ` class="language-${escapeHtml(lang)}"` : '';
    return stash(`<pre><code${cls}>${escapeHtml(code)}</code></pre>`);
  });

  // Block math: `$$...$$` may span multiple lines, so extract before
  // paragraph splitting.
  result = result.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) =>
    stash(renderMath(tex.trim(), true)),
  );

  // Inline math: `$...$` on a single line, no empty body, not preceded by
  // a backslash (escaped). Stash so `_`/`*` inside don't get treated as
  // markdown emphasis.
  result = result.replace(
    /(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g,
    (_, tex) => stash(renderMath(tex, false)),
  );

  // Tables: header row, separator row, then one or more body rows.
  result = result.replace(
    /(^(\|.+)\n)(^\|[\s:|-]+\n)((?:^\|.+\n?)+)/gm,
    (
      _match: string,
      headerLine: string,
      _h: string,
      sepLine: string,
      bodyBlock: string,
    ) => {
      const parseRow = (row: string) => {
        const trimmed = row.trim();
        const inner = trimmed.startsWith('|') ? trimmed.slice(1) : trimmed;
        const stripped = inner.endsWith('|') ? inner.slice(0, -1) : inner;
        return stripped.split('|').map((c) => c.trim());
      };
      // Parse the separator for column alignment (`:---`, `:---:`, `---:`).
      const aligns = parseRow(sepLine).map((c) => {
        const left = c.startsWith(':');
        const right = c.endsWith(':');
        if (left && right) return ' style="text-align:center"';
        if (right) return ' style="text-align:right"';
        if (left) return ' style="text-align:left"';
        return '';
      });
      const headers = parseRow(headerLine);
      const bodyRows = bodyBlock.trim().split('\n').map(parseRow);
      let table = '<table><thead><tr>';
      headers.forEach((h, i) => {
        table += `<th${aligns[i] || ''}>${inline(h)}</th>`;
      });
      table += '</tr></thead><tbody>';
      for (const row of bodyRows) {
        table += '<tr>';
        row.forEach((cell, i) => {
          table += `<td${aligns[i] || ''}>${inline(cell)}</td>`;
        });
        table += '</tr>';
      }
      table += '</tbody></table>';
      return stash(table);
    },
  );

  // 2. Split into paragraph-level blocks separated by blank lines, then
  //    sub-split each block on lines that are unambiguously their own
  //    block (headings, horizontal rules) so back-to-back headings
  //    without intervening blank lines still render correctly. This is
  //    the practical compromise: real CommonMark requires the blank line
  //    before a heading, but LLM-generated reports routinely omit it.
  const paragraphs = result
    .split(/\n[\t ]*\n/)
    .flatMap(splitOnBlockBoundaries);
  result = paragraphs.map(renderBlock).filter(Boolean).join('\n');

  // 3. Restore extracted blocks.
  result = result.replace(/\x00BLOCK(\d+)\x00/g, (_, i) => blocks[parseInt(i)]);

  return result;
}

/** Split a paragraph block on lines that should always start a new block,
 * even without a preceding blank line. ATX headings, horizontal rules, and
 * stashed-block placeholders (tables / code fences / block math) qualify.
 * Everything else stays adjacent to its neighbors and is handled by
 * renderBlock — so a list, blockquote, or paragraph that already lives on
 * its own won't be fragmented. */
function splitOnBlockBoundaries(block: string): string[] {
  const lines = block.split('\n');
  const out: string[] = [];
  let current: string[] = [];
  const flush = () => {
    if (current.length) {
      out.push(current.join('\n'));
      current = [];
    }
  };
  const isBoundary = (line: string) => {
    const t = line.trim();
    if (/^#{1,6}\s+/.test(t)) return true;
    if (/^(?:\s*[-*_]\s*){3,}$/.test(t)) return true;
    if (/^\x00BLOCK\d+\x00$/.test(t)) return true;
    return false;
  };
  for (const line of lines) {
    if (isBoundary(line)) {
      flush();
      out.push(line);
    } else {
      current.push(line);
    }
  }
  flush();
  return out;
}

function renderBlock(block: string): string {
  const trimmed = block.trim();
  if (!trimmed) return '';

  // A pre-extracted block (table, code, or block math) on its own line.
  if (/^\x00BLOCK\d+\x00$/.test(trimmed)) return trimmed;
  // Horizontal rule: a line of three or more `-`, `*`, or `_`.
  if (/^(?:\s*[-*_]\s*){3,}$/.test(trimmed) && !trimmed.includes('\n')) {
    return '<hr>';
  }

  // ATX heading on a single line: # → h1, ## → h2, ... ###### → h6.
  const heading = trimmed.match(/^(#{1,6})\s+(.+?)\s*#*$/);
  if (heading && !heading[2].includes('\n')) {
    const level = heading[1].length;
    return `<h${level}>${inline(heading[2])}</h${level}>`;
  }

  // Blockquote: every line starts with `>`. Strip the marker and recurse
  // so nested headings, lists, etc. work inside the quote.
  const lines = trimmed.split('\n');
  if (lines.every((l) => /^\s*>\s?/.test(l))) {
    const stripped = lines.map((l) => l.replace(/^\s*>\s?/, '')).join('\n');
    return `<blockquote>${markdownToHtml(stripped)}</blockquote>`;
  }

  // Ordered list: every line is either a `1. ` item or an indented
  // continuation. Continuations fold into the previous item with a space.
  if (lines.every((l) => /^\s*\d+\.\s+/.test(l) || /^\s+\S/.test(l))) {
    const items: string[] = [];
    let start: string | null = null;
    for (const line of lines) {
      const m = line.match(/^\s*(\d+)\.\s+(.+)/);
      if (m) {
        if (start === null) start = m[1];
        items.push(inline(m[2]));
      } else if (items.length) {
        items[items.length - 1] += ' ' + inline(line.trim());
      }
    }
    const startAttr = start && start !== '1' ? ` start="${start}"` : '';
    return `<ol${startAttr}>${items.map((x) => `<li>${x}</li>`).join('')}</ol>`;
  }

  // Bullet list — every line is either a `- ` / `* ` / `+ ` item or a
  // continuation (indented). Continuations fold into the previous item.
  if (lines.every((l) => /^\s*[-*+]\s+/.test(l) || /^\s+\S/.test(l))) {
    const items: string[] = [];
    for (const line of lines) {
      const m = line.match(/^\s*[-*+]\s+(.+)/);
      if (m) {
        items.push(inline(m[1]));
      } else if (items.length) {
        items[items.length - 1] += ' ' + inline(line.trim());
      }
    }
    return `<ul>${items.map((x) => `<li>${x}</li>`).join('')}</ul>`;
  }

  // Default: paragraph. Soft newlines collapse to a single space.
  return `<p>${inline(trimmed.replace(/\s*\n\s*/g, ' '))}</p>`;
}

/** Inline transforms.
 *
 * Order matters. Code spans are extracted first so their contents (which
 * may contain `*`, `_`, `[`, etc.) aren't treated as emphasis or links.
 * Images are matched before links because their syntax differs only by a
 * leading `!`. Then bold (longer delimiter) before italic so `**x**` isn't
 * parsed as `*` + `*x*` + `*`. Plain text between matches is HTML-escaped
 * so stray `<` and `&` don't break the output. */
function inline(text: string): string {
  // Stash inline code spans so their contents are escaped and immune to
  // further transforms.
  const codes: string[] = [];
  let s = text.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(`<code>${escapeHtml(code)}</code>`);
    return `\x01CODE${codes.length - 1}\x01`;
  });

  // Escape everything else, then re-introduce the markdown tokens we
  // recognize. This keeps stray `<` and `&` safe.
  s = escapeHtml(s);

  // Images: ![alt](src "title")
  s = s.replace(
    /!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]+)&quot;)?\)/g,
    (_, alt, src, title) => {
      const t = title ? ` title="${title}"` : '';
      return `<img src="${src}" alt="${alt}"${t}>`;
    },
  );

  // Links: [text](href "title"). `text` itself may contain emphasis, so
  // it'll get processed by the bold/italic passes below alongside other
  // prose.
  s = s.replace(
    /\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;([^&]+)&quot;)?\)/g,
    (_, label, href, title) => {
      const t = title ? ` title="${title}"` : '';
      return `<a href="${href}"${t}>${label}</a>`;
    },
  );

  // Bold + italic (must run before plain bold and plain italic).
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/___(.+?)___/g, '<strong><em>$1</em></strong>');

  // Bold.
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // Italic. The lookarounds keep `snake_case_identifiers` and stray `*`s
  // inside words from triggering emphasis.
  s = s.replace(/(?<![A-Za-z0-9*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![A-Za-z0-9*])/g, '<em>$1</em>');
  s = s.replace(/(?<![A-Za-z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-z0-9_])/g, '<em>$1</em>');

  // Strikethrough.
  s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');

  // Restore code spans.
  s = s.replace(/\x01CODE(\d+)\x01/g, (_, i) => codes[parseInt(i)]);

  return s;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderMath(tex: string, display: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      strict: 'ignore',
    });
  } catch {
    const wrap = display ? '$$' : '$';
    return `<code>${escapeHtml(wrap + tex + wrap)}</code>`;
  }
}