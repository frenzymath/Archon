/**
 * Blueprint — a leanblueprint-quality reading view.
 *
 * The whole blueprint is *numbered* up front (cheap, no KaTeX) so chapter /
 * section / theorem numbers and `\ref`/`\cref` cross-references stay global, but
 * only the chapters the user selects are actually *rendered* (KaTeX), so the page
 * loads a title page + table of contents first and stays fast on big projects.
 * The left panel is the navigator: pick chapters to open (several at once), drill
 * into sections. The resizable git timeline time-travels the whole view.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useBlueprintChapters,
  useGitLog,
  type GitCommit,
} from '../hooks/useGitLog';
import { useDag } from '../hooks/useDag';
import { buildBlueprintModel, ChapterView, TitleInline } from '../components/BlueprintDoc';
import { GitTimeline } from '../components/GitTimeline';
import styles from './Blueprint.module.css';

function useDragResize(initial: number, min: number, max: number) {
  const [size, setSize] = useState(initial);
  const start = useRef({ pos: 0, size: 0 });
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    start.current = { pos: e.clientY, size };
    const onMove = (ev: MouseEvent) => setSize(Math.max(min, Math.min(max, start.current.size - (ev.clientY - start.current.pos))));
    const onUp = () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [size, min, max]);
  return { size, onMouseDown };
}

export default function Blueprint() {
  const [selectedSha, setSelectedSha] = useState('');
  const { data, isLoading, error, isFetching } = useBlueprintChapters(selectedSha || undefined);
  const { data: gitData } = useGitLog();
  const { data: dag } = useDag(selectedSha || undefined);

  const macros = data?.macros ?? {};
  const chapters = useMemo(() => data?.chapters ?? [], [data]);

  // Number every chapter (no KaTeX); render only the selected ones.
  const { doc, labels } = useMemo(() => buildBlueprintModel(chapters, true), [chapters]);

  const leanSource = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of dag?.nodes ?? []) {
      if (n.lean_name && n.lean_source) for (const nm of n.lean_name.split(',').map(s => s.trim()).filter(Boolean)) m.set(nm, n.lean_source);
    }
    return m;
  }, [dag]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const pending = useRef<string | null>(null);
  useEffect(() => {
    if (!pending.current) return;
    const el = document.getElementById(pending.current);
    if (el) { el.scrollIntoView({ block: 'start' }); pending.current = null; }
  }, [open]);

  // reset selection when the doc changes (e.g. time-travel)
  useEffect(() => { setOpen(new Set()); setExpanded(new Set()); }, [selectedSha]);

  const openTo = useCallback((slug: string, anchor?: string) => {
    if (open.has(slug)) { if (anchor) document.getElementById(anchor)?.scrollIntoView({ block: 'start' }); return; }
    pending.current = anchor ?? `ch-${slug}`;
    setOpen(v => { const n = new Set(v); n.add(slug); return n; });
  }, [open]);
  const toggleOpen = useCallback((slug: string) => setOpen(v => { const n = new Set(v); n.has(slug) ? n.delete(slug) : n.add(slug); return n; }), []);
  const toggleExpand = useCallback((slug: string) => setExpanded(v => { const n = new Set(v); n.has(slug) ? n.delete(slug) : n.add(slug); return n; }), []);

  const git = useDragResize(160, 70, 520);
  const [gitW, setGitW] = useState(800);
  const gitPanelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = gitPanelRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setGitW(el.clientWidth));
    ro.observe(el); setGitW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const openChapters = doc.filter(c => open.has(c.slug));

  return (
    <div className={styles.root}>
      <div className={styles.body}>
        {/* Left navigator: chapters (toggle to open) + drill into sections */}
        <aside className={styles.toc}>
          <div className={styles.tocHead}>
            Contents
            {selectedSha && <span className={styles.histTag}>@{selectedSha.slice(0, 7)}</span>}
          </div>
          {doc.length === 0 && <div className={styles.tocEmpty}>—</div>}
          {doc.map(ch => (
            <div key={ch.slug}>
              <div className={styles.tocChapRow}>
                <button className={styles.tocCaret} onClick={() => toggleExpand(ch.slug)} title="Show sections">
                  {ch.sections.length ? (expanded.has(ch.slug) ? '▾' : '▸') : '·'}
                </button>
                <button className={`${styles.tocChap} ${open.has(ch.slug) ? styles.tocActive : ''}`}
                  onClick={() => (open.has(ch.slug) ? toggleOpen(ch.slug) : openTo(ch.slug))}>
                  <span className={styles.tocNum}>{ch.num}</span>
                  <span className={styles.tocTitle}><TitleInline nodes={ch.title} macros={macros} /></span>
                </button>
              </div>
              {expanded.has(ch.slug) && ch.sections.map(s => (
                <button key={s.anchor} className={styles.tocSec} style={{ paddingLeft: s.level === 2 ? 26 : 38 }}
                  onClick={() => openTo(ch.slug, s.anchor)}>
                  <span className={styles.tocNum}>{s.num}</span>
                  <span className={styles.tocTitle}><TitleInline nodes={s.title} macros={macros} /></span>
                </button>
              ))}
            </div>
          ))}
        </aside>

        <main className={styles.reading}>
          {isLoading && <div className={styles.muted}>Loading blueprint…</div>}
          {error && <div className={styles.muted}>Failed to load the blueprint.</div>}
          {data && !data.hasBlueprint && !data.error && (
            <div className={styles.muted}>No blueprint found under <code>blueprint/src/</code>.</div>
          )}
          {data?.error && <div className={styles.muted}>{data.error}</div>}

          {data?.hasBlueprint && (
            <header className={styles.titlePage}>
              <h1 className={styles.docTitle}>
                {data.docTitle ? <TitleInline tex={data.docTitle} macros={macros} /> : 'Blueprint'}
              </h1>
              {data.docAuthor && <div className={styles.docAuthor}><TitleInline tex={data.docAuthor} macros={macros} /></div>}
              <div className={styles.docMeta}>
                {doc.length} chapter{doc.length === 1 ? '' : 's'}
                {selectedSha ? ` · historical @${selectedSha.slice(0, 7)}` : ''}
              </div>
            </header>
          )}

          {/* Landing: a full table of contents (nothing rendered yet) */}
          {openChapters.length === 0 && doc.length > 0 && (
            <nav className={styles.bigToc}>
              <div className={styles.bigTocHead}>Table of Contents <span className={styles.bigTocHint}>— click to open a chapter</span></div>
              {doc.map(ch => (
                <div key={ch.slug} className={styles.bigTocChap}>
                  <button className={styles.bigTocChapLink} onClick={() => openTo(ch.slug)}>
                    <span className={styles.tocNum}>{ch.num}</span> <TitleInline nodes={ch.title} macros={macros} />
                  </button>
                  {ch.sections.length > 0 && (
                    <div className={styles.bigTocSecs}>
                      {ch.sections.map(s => (
                        <button key={s.anchor} className={styles.bigTocSec} style={{ marginLeft: s.level === 3 ? 16 : 0 }}
                          onClick={() => openTo(ch.slug, s.anchor)}>
                          <span className={styles.tocNum}>{s.num}</span> <TitleInline nodes={s.title} macros={macros} />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </nav>
          )}

          {/* Rendered (selected) chapters */}
          {openChapters.map(ch => (
            <div key={ch.slug} className={styles.openChapter}>
              <button className={styles.closeChap} onClick={() => toggleOpen(ch.slug)} title="Close chapter">×</button>
              <ChapterView chapter={ch} macros={macros} labels={labels} leanSource={leanSource} onNavigate={openTo} />
            </div>
          ))}
        </main>
      </div>

      <div className={styles.gitResize} onMouseDown={git.onMouseDown} title="Drag to resize" />
      <div className={styles.gitPanel} ref={gitPanelRef} style={{ height: git.size }}>
        <div className={styles.gitHead}>
          <span className={styles.gitTitle}>Git history{isFetching && selectedSha ? ' · building…' : ''}</span>
          {selectedSha && <button className={styles.gitLive} onClick={() => setSelectedSha('')}>← Live</button>}
        </div>
        <GitTimeline
          commits={gitData?.commits ?? []}
          selectedSha={selectedSha}
          onSelect={(c: GitCommit) => setSelectedSha(prev => (prev === c.sha ? '' : c.sha))}
          containerW={gitW}
        />
      </div>
    </div>
  );
}
