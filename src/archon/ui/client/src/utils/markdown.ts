import katex from 'katex';

/** Minimal markdown → HTML converter.
 *
 * Soft newlines inside a paragraph collapse to a single space (CommonMark
 * behavior), so wrapped prose isn't broken mid-sentence. Blank lines start
 * a new block. HTML comments (`<!-- ... -->`) are stripped — they exist in
 * the templates as authoring guidance, not user-facing content. Math is
 * rendered with KaTeX: `$$...$$` for display, `$...$` for inline. */
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

  result = result.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) =>
    stash(`<pre><code>${escapeHtml(code)}</code></pre>`),
  );

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

  result = result.replace(
    /(^(\|.+)\n)(^\|[\s:|-]+\n)((?:^\|.+\n?)+)/gm,
    (_, headerLine, _h, _sep, bodyBlock) => {
      const parseRow = (row: string) => {
        const trimmed = row.trim();
        const inner = trimmed.startsWith('|') ? trimmed.slice(1) : trimmed;
        const stripped = inner.endsWith('|') ? inner.slice(0, -1) : inner;
        return stripped.split('|').map((c) => c.trim());
      };
      const headers = parseRow(headerLine);
      const bodyRows = bodyBlock.trim().split('\n').map(parseRow);
      let table = '<table><thead><tr>';
      for (const h of headers) table += `<th>${inline(h)}</th>`;
      table += '</tr></thead><tbody>';
      for (const row of bodyRows) {
        table += '<tr>';
        for (const cell of row) table += `<td>${inline(cell)}</td>`;
        table += '</tr>';
      }
      table += '</tbody></table>';
      return stash(table);
    },
  );

  // 2. Split into paragraph-level blocks separated by blank lines, render
  //    each, then join. Blank lines (and the comment removals above) leave
  //    runs of empty whitespace — collapse them.
  const paragraphs = result.split(/\n[\t ]*\n/);
  result = paragraphs.map(renderBlock).filter(Boolean).join('\n');

  // 3. Restore extracted blocks.
  result = result.replace(/\x00BLOCK(\d+)\x00/g, (_, i) => blocks[parseInt(i)]);

  return result;
}

function renderBlock(block: string): string {
  const trimmed = block.trim();
  if (!trimmed) return '';

  // A pre-extracted block (table or code) on its own line.
  if (/^\x00BLOCK\d+\x00$/.test(trimmed)) return trimmed;

  // Heading on a single line.
  const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
  if (heading && !heading[2].includes('\n')) {
    const level = Math.min(heading[1].length + 1, 6);
    return `<h${level}>${inline(heading[2])}</h${level}>`;
  }

  // Bullet list — every line is either a `- ` item or a continuation
  // (indented). Continuations fold into the previous item with a space.
  const lines = trimmed.split('\n');
  if (lines.every((l) => /^\s*-\s+/.test(l) || /^\s+\S/.test(l))) {
    const items: string[] = [];
    for (const line of lines) {
      const m = line.match(/^\s*-\s+(.+)/);
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

function inline(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
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
