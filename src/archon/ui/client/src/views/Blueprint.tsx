/**
 * Blueprint — a leanblueprint-quality reading view of the project's blueprint.
 *
 * Renders the whole blueprint with chapter/section/theorem numbering, clickable
 * numbered cross-references, environment headers (`[name]` italic, `\lean` code
 * chips, `\uses` dependency tags), lists, and KaTeX (incl. titles). A title page
 * leads; `% SOURCE`/`% NOTE` comments surface as expandable chips. The resizable
 * git timeline at the bottom time-travels the whole view to any inner-git commit.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  useBlueprintChapters,
  useGitLog,
  type GitCommit,
} from '../hooks/useGitLog';
import { useDag } from '../hooks/useDag';
import BlueprintDoc, { TitleInline } from '../components/BlueprintDoc';
import { GitTimeline } from '../components/GitTimeline';
import styles from './Blueprint.module.css';

/** Drag-to-resize along the y axis (the git panel grows upward as you drag up). */
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
  // Lean source for the `\lean{}` code chips comes from the cached leandag graph.
  const { data: dag } = useDag(selectedSha || undefined);

  const leanSource = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of dag?.nodes ?? []) {
      if (n.lean_name && n.lean_source) {
        for (const nm of n.lean_name.split(',').map(s => s.trim()).filter(Boolean)) m.set(nm, n.lean_source);
      }
    }
    return m;
  }, [dag]);

  const git = useDragResize(160, 70, 520);
  const [gitW, setGitW] = useState(800);
  const gitPanelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = gitPanelRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setGitW(el.clientWidth));
    ro.observe(el);
    setGitW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const chapters = data?.chapters ?? [];
  const macros = data?.macros ?? {};

  return (
    <div className={styles.root}>
      <div className={styles.body}>
        <aside className={styles.toc}>
          <div className={styles.tocHead}>
            Contents
            {selectedSha && <span className={styles.histTag}>@{selectedSha.slice(0, 7)}</span>}
          </div>
          {chapters.length === 0 && <div className={styles.tocEmpty}>—</div>}
          {chapters.map((c, i) => (
            <a key={c.slug} href={`#ch-${c.slug}`} className={styles.tocItem}>
              <span className={styles.tocNum}>{i + 1}</span>
              <span className={styles.tocTitle}><TitleInline tex={c.title} macros={macros} /></span>
            </a>
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
              {data.docAuthor && (
                <div className={styles.docAuthor}><TitleInline tex={data.docAuthor} macros={macros} /></div>
              )}
              <div className={styles.docMeta}>
                {chapters.length} chapter{chapters.length === 1 ? '' : 's'}
                {selectedSha ? ` · historical @${selectedSha.slice(0, 7)}` : ''}
              </div>
            </header>
          )}

          {chapters.length > 0 && (
            <BlueprintDoc chapters={chapters} macros={macros} leanSource={leanSource} showComments />
          )}
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
