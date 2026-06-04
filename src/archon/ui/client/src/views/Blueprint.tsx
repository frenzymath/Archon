/**
 * Blueprint — a reading view of the project's leanblueprint, like
 * `leanblueprint serve` but without the dependency graph (that's the DAG page).
 *
 * Chapters render in `content.tex` order with KaTeX; `% SOURCE` / `% NOTE`
 * comments surface as expandable “ chips (BlueprintRendered showComments) so
 * provenance is available on demand without cluttering the prose. The git
 * timeline at the bottom time-travels the whole view to any inner-git commit.
 */
import { useEffect, useRef, useState } from 'react';
import {
  useBlueprintChapters,
  useGitLog,
  type GitCommit,
} from '../hooks/useGitLog';
import BlueprintRendered from '../components/BlueprintRendered';
import { GitTimeline } from '../components/GitTimeline';
import styles from './Blueprint.module.css';

/**
 * Readable plain text for a chapter title: chapter headings carry inline math
 * (`\(k\)-modules`, `($i=0$)`). Full KaTeX in a narrow TOC is overkill; dropping
 * the delimiters and simple commands keeps them legible (`k-modules`, `(i=0)`).
 */
function plainTitle(s: string): string {
  return s
    .replace(/\\[()[\]]/g, '')       // \( \) \[ \]
    .replace(/\$\$?/g, '')           // $  $$
    .replace(/\\[a-zA-Z]+\s*/g, '')  // \mathbb, \mathcal, …
    .replace(/[{}]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export default function Blueprint() {
  const [selectedSha, setSelectedSha] = useState('');
  const { data, isLoading, error, isFetching } = useBlueprintChapters(selectedSha || undefined);
  const { data: gitData } = useGitLog();

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
            Chapters
            {selectedSha && <span className={styles.histTag}>@{selectedSha.slice(0, 7)}</span>}
          </div>
          {chapters.length === 0 && <div className={styles.tocEmpty}>—</div>}
          {chapters.map(c => (
            <a key={c.slug} href={`#ch-${c.slug}`} className={styles.tocItem} title={plainTitle(c.title)}>
              {plainTitle(c.title)}
            </a>
          ))}
        </aside>

        <main className={styles.reading}>
          {isLoading && <div className={styles.muted}>Loading blueprint…</div>}
          {error && <div className={styles.muted}>Failed to load the blueprint.</div>}
          {data && !data.hasBlueprint && !data.error && (
            <div className={styles.muted}>
              No blueprint found under <code>blueprint/src/</code>.
            </div>
          )}
          {data?.error && <div className={styles.muted}>{data.error}</div>}
          {chapters.map(c => (
            <section key={c.slug} id={`ch-${c.slug}`} className={styles.chapter}>
              <h2 className={styles.chapterTitle}>{plainTitle(c.title)}</h2>
              <BlueprintRendered tex={c.tex} macros={macros} showComments />
            </section>
          ))}
        </main>
      </div>

      <div className={styles.gitPanel} ref={gitPanelRef}>
        <div className={styles.gitHead}>
          <span className={styles.gitTitle}>
            Git history{isFetching && selectedSha ? ' · building…' : ''}
          </span>
          {selectedSha && (
            <button className={styles.gitLive} onClick={() => setSelectedSha('')}>← Live</button>
          )}
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
