/**
 * DiffPlayback — file-centric code change viewer
 *
 * Layout:
 *   Left sidebar: file list (aggregated across iterations)
 *   Center main:
 *     - Toolbar: step navigation + view toggle + log jump
 *     - Hint bar: lightweight clarity / provenance note
 *     - Scrubber: clickable timeline
 *     - Content: DiffView or full file source view
 *   Right sidebar: structure index for long diff / file navigation
 */
import { useEffect, useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSnapshotFiles, useFileTimeline, useSnapshotFileContent } from '../hooks/useSnapshots';
import { useGitLog } from '../hooks/useGitLog';
import { useDiffUrlState, type DiffCompareMode } from '../hooks/useDiffUrlState';
import { useDiffStructureNavigation } from '../hooks/useDiffStructureNavigation';
import DiffView from '../components/DiffView';
import DiffStructurePanel from '../components/DiffStructurePanel';
import LeanCodeLine from '../components/LeanCodeLine';
import { parseDiffWithStructure } from '../utils/diffStructure';
import { extractLeanStructureFromLines } from '../utils/leanStructure';
import { highlightLeanLines } from '../utils/leanHighlight';
import { createUnifiedDiff } from '../utils/unifiedDiff';
import styles from './DiffPlayback.module.css';
import type { FileSnapshotSummary } from '../types';

function formatSlug(slug?: string) {
  return slug ? slug.replace(/_/g, '/') : '';
}

function formatCompareLabel(entry?: { iteration: string; step: number }) {
  if (!entry) return '';
  return entry.step === 0 ? `${entry.iteration} · baseline` : `${entry.iteration} · step-${String(entry.step).padStart(3, '0')}`;
}

type FileTreeNode = {
  name: string;
  path: string;
  children: FileTreeNode[];
  file?: FileSnapshotSummary;
};

function getFilePath(summary: FileSnapshotSummary) {
  return summary.file ?? formatSlug(summary.slug);
}

function buildFileTree(files: FileSnapshotSummary[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', path: '', children: [] };

  for (const file of files) {
    const filePath = getFilePath(file);
    const parts = filePath.split('/').filter(Boolean);
    let current = root;
    let currentPath = '';

    parts.forEach((part, idx) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      let child = current.children.find(node => node.name === part);
      if (!child) {
        child = { name: part, path: currentPath, children: [] };
        current.children.push(child);
      }
      if (idx === parts.length - 1) child.file = file;
      current = child;
    });
  }

  const sortNodes = (nodes: FileTreeNode[]) => {
    nodes.sort((a, b) => {
      const aIsDir = !a.file;
      const bIsDir = !b.file;
      if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach(node => sortNodes(node.children));
  };

  sortNodes(root.children);
  return root.children;
}

function collectFolderPaths(nodes: FileTreeNode[]): string[] {
  const paths: string[] = [];
  const walk = (items: FileTreeNode[]) => {
    for (const item of items) {
      if (item.children.length > 0) {
        paths.push(item.path);
        walk(item.children);
      }
    }
  };
  walk(nodes);
  return paths;
}

function Scrubber({ timeline, currentIdx, compareIdx, compareLabel, onSeek }: {
  timeline: { iteration: string; step: number }[];
  currentIdx: number;
  compareIdx?: number;
  compareLabel?: string;
  onSeek: (idx: number) => void;
}) {
  const total = timeline.length;
  if (total <= 1) return null;

  const iterBoundaries: number[] = [];
  for (let i = 1; i < total; i++) {
    if (timeline[i].iteration !== timeline[i - 1].iteration) iterBoundaries.push(i);
  }

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onSeek(Math.round(ratio * (total - 1)));
  };

  const pct = (currentIdx / (total - 1)) * 100;
  const comparePct = compareIdx != null ? (compareIdx / (total - 1)) * 100 : null;

  return (
    <div className={styles.scrubber}>
      <span className={styles.scrubberLabel}>{timeline[currentIdx]?.iteration}</span>
      <div className={styles.scrubberTrack} onClick={handleClick}>
        <div className={styles.scrubberBar}>
          <div className={styles.scrubberFill} style={{ width: `${pct}%` }} />
        </div>
        <div className={styles.scrubberMarkers}>
          {iterBoundaries.map(idx => (
            <div
              key={idx}
              className={`${styles.scrubberTick} ${styles.scrubberTickIter}`}
              style={{ left: `${(idx / (total - 1)) * 100}%` }}
              title={timeline[idx].iteration}
            />
          ))}
        </div>
        {comparePct != null && compareIdx !== currentIdx && (
          <div
            className={styles.scrubberCompareHandle}
            style={{ left: `${comparePct}%` }}
            title={compareLabel || 'Compare target'}
          />
        )}
        <div className={styles.scrubberHandle} style={{ left: `${pct}%` }} />
      </div>
    </div>
  );
}

function FileTree({
  nodes,
  selectedSlug,
  expandedFolders,
  onToggleFolder,
  onSelectFile,
}: {
  nodes: FileTreeNode[];
  selectedSlug?: string;
  expandedFolders: Set<string>;
  onToggleFolder: (path: string) => void;
  onSelectFile: (file: FileSnapshotSummary) => void;
}) {
  return (
    <div className={styles.tree}>
      {nodes.map(node => {
        const isFolder = !node.file;
        const isExpanded = expandedFolders.has(node.path);
        const file = node.file;
        return (
          <div key={node.path} className={styles.treeNode}>
            {isFolder ? (
              <>
                <button
                  type="button"
                  className={styles.folderRow}
                  onClick={() => onToggleFolder(node.path)}
                >
                  <span className={styles.folderChevron}>{isExpanded ? '▾' : '▸'}</span>
                  <span className={styles.folderName}>{node.name}</span>
                </button>
                {isExpanded && node.children.length > 0 && (
                  <div className={styles.folderChildren}>
                    <FileTree
                      nodes={node.children}
                      selectedSlug={selectedSlug}
                      expandedFolders={expandedFolders}
                      onToggleFolder={onToggleFolder}
                      onSelectFile={onSelectFile}
                    />
                  </div>
                )}
              </>
            ) : file ? (
              <button
                type="button"
                className={`${styles.fileItem} ${file.slug === selectedSlug ? styles.fileItemActive : ''}`}
                onClick={() => onSelectFile(file)}
              >
                <div className={styles.fileName}>{node.name}</div>
                <div className={styles.fileMeta}>
                  {file.totalSteps} edit{file.totalSteps !== 1 ? 's' : ''} · {file.iterations.length} iter{file.iterations.length !== 1 ? 's' : ''}
                </div>
              </button>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function FileSourceView({ content, fileName, activeId }: { content: string; fileName: string; activeId?: string }) {
  const lines = useMemo(() => content.split('\n'), [content]);
  const highlightedLines = useMemo(() => highlightLeanLines(lines), [lines]);
  const structure = useMemo(() => extractLeanStructureFromLines(lines), [lines]);
  const idByLine = useMemo(() => new Map(structure.map(item => [item.line, item.id])), [structure]);

  return (
    <div className={styles.fileView}>
      <div className={styles.fileViewHeader}>{fileName}</div>
      <div className={styles.fileViewBody}>
        <table className={styles.fileTable}>
          <tbody>
            {lines.map((line, i) => {
              const id = idByLine.get(i + 1);
              return (
                <tr key={i} id={id} className={activeId && id === activeId ? styles.fileLineActive : ''}>
                  <td className={styles.fileLineNum}>{i + 1}</td>
                  <td className={styles.fileLineContent}><LeanCodeLine text={line} tokens={highlightedLines[i]} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function DiffPlayback() {
  const { data: files } = useSnapshotFiles();
  const { data: gitData } = useGitLog();
  const navigate = useNavigate();
  const fileTree = useMemo(() => buildFileTree(files ?? []), [files]);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => new Set());
  const {
    selectedSlug,
    setSelectedSlug,
    currentIdx,
    setCurrentIdx,
    viewMode,
    setViewMode,
    compareMode,
    setCompareMode,
    customCompareIter,
    setCustomCompareIter,
    customCompareStep,
    setCustomCompareStep,
    selectFile,
    syncUrl,
    resolveInitialPosition,
  } = useDiffUrlState();
  const { activeId, jumpTo } = useDiffStructureNavigation();

  useEffect(() => {
    if (fileTree.length === 0) return;
    const folderPaths = collectFolderPaths(fileTree);
    setExpandedFolders(prev => {
      if (prev.size > 0) return prev;
      return new Set(folderPaths);
    });
  }, [fileTree]);

  useEffect(() => {
    if (!selectedSlug && files && files.length > 0) setSelectedSlug(files[0].slug);
  }, [files, selectedSlug, setSelectedSlug]);

  const { data: timeline, isLoading: timelineLoading } = useFileTimeline(selectedSlug);

  useEffect(() => {
    if (!timeline || timeline.length === 0) return;
    const idx = resolveInitialPosition(timeline);
    if (idx != null) setCurrentIdx(idx);
  }, [timeline, resolveInitialPosition, setCurrentIdx]);

  const totalEntries = timeline?.length ?? 0;
  const currentEntry = timeline?.[currentIdx];
  const selectedSummary = files?.find(f => f.slug === selectedSlug);
  const baselineEntry = timeline?.find(entry => entry.step === 0) ?? timeline?.[0];

  const customOptions = useMemo(() => (timeline ?? []).filter(entry => {
    if (!currentEntry) return false;
    return !(entry.iteration === currentEntry.iteration && entry.step === currentEntry.step);
  }), [timeline, currentEntry]);

  const customCompareEntry = useMemo(() => {
    if (!timeline || !customCompareIter || !customCompareStep) return undefined;
    const step = parseInt(customCompareStep, 10);
    if (Number.isNaN(step)) return undefined;
    return timeline.find(entry => entry.iteration === customCompareIter && entry.step === step);
  }, [timeline, customCompareIter, customCompareStep]);

  const compareEntry = useMemo(() => {
    if (!timeline || !currentEntry) return undefined;
    if (compareMode === 'baseline') return baselineEntry;
    if (compareMode === 'custom') return customCompareEntry;
    return currentIdx > 0 ? timeline[currentIdx - 1] : undefined;
  }, [timeline, currentEntry, compareMode, baselineEntry, customCompareEntry, currentIdx]);

  const compareIdx = useMemo(() => {
    if (!timeline || !compareEntry) return undefined;
    const idx = timeline.findIndex(entry => entry.iteration === compareEntry.iteration && entry.step === compareEntry.step);
    return idx >= 0 ? idx : undefined;
  }, [timeline, compareEntry]);

  const toggleFolder = useCallback((path: string) => {
    setExpandedFolders(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const handleSelectFile = useCallback((file: FileSnapshotSummary) => {
    selectFile(file.slug);
  }, [selectFile]);

  useEffect(() => {
    if (compareMode !== 'custom') return;
    if (customCompareEntry) return;
    const fallback = customOptions[customOptions.length - 1];
    if (fallback) {
      setCustomCompareIter(fallback.iteration);
      setCustomCompareStep(String(fallback.step));
      return;
    }
    setCompareMode('previous');
    setCustomCompareIter('');
    setCustomCompareStep('');
  }, [compareMode, customCompareEntry, customOptions, setCompareMode, setCustomCompareIter, setCustomCompareStep]);

  const handleCompareModeChange = useCallback((mode: DiffCompareMode) => {
    setCompareMode(mode);
    if (mode === 'custom' && !customCompareEntry) {
      const fallback = customOptions[customOptions.length - 1];
      if (fallback) {
        setCustomCompareIter(fallback.iteration);
        setCustomCompareStep(String(fallback.step));
      }
    }
  }, [customCompareEntry, customOptions, setCompareMode, setCustomCompareIter, setCustomCompareStep]);

  const selectedFileLabel = selectedSummary?.file ?? formatSlug(selectedSlug);
  const sourceFileLabel = currentEntry?.sourceFile ?? selectedFileLabel;
  const hasSourceMismatch = !!(currentEntry?.sourceFile && currentEntry.sourceFile !== selectedFileLabel);
  const hintText = currentEntry?.step === 0
    ? 'Diffs is a snapshot replay for this file view. Use View log context to inspect the related run history.'
    : compareMode === 'previous'
      ? 'Diffs is a snapshot replay for this file view. Use View log context to inspect the log history around this step.'
      : `Diffs is comparing the current snapshot against ${compareMode === 'baseline' ? 'baseline' : 'a custom snapshot'} from this file timeline.`;

  useEffect(() => {
    if (!selectedSlug || !currentEntry) return;
    syncUrl(selectedSlug, currentEntry, viewMode, {
      mode: compareMode,
      iteration: compareMode === 'custom' ? customCompareIter : undefined,
      step: compareMode === 'custom' ? customCompareStep : undefined,
    });
  }, [selectedSlug, currentEntry, viewMode, compareMode, customCompareIter, customCompareStep, syncUrl]);

  const { data: fileContent } = useSnapshotFileContent(
    selectedSlug,
    currentEntry?.iteration ?? '',
    currentEntry?.file ?? '',
  );

  const { data: compareFileContent } = useSnapshotFileContent(
    selectedSlug,
    compareEntry?.iteration ?? '',
    compareEntry?.file ?? '',
  );

  const displayDiff = useMemo(() => {
    if (!currentEntry) return { diff: '', addedLines: 0, removedLines: 0 };
    if (compareMode === 'previous') {
      return {
        diff: currentEntry.diff || '',
        addedLines: currentEntry.addedLines || 0,
        removedLines: currentEntry.removedLines || 0,
      };
    }
    if (!compareEntry || !fileContent?.content || !compareFileContent?.content) {
      return { diff: '', addedLines: 0, removedLines: 0 };
    }
    return createUnifiedDiff(compareEntry.file, currentEntry.file, compareFileContent.content, fileContent.content);
  }, [currentEntry, compareMode, compareEntry, fileContent?.content, compareFileContent?.content]);

  const diffStructure = useMemo(
    () => displayDiff.diff ? parseDiffWithStructure(displayDiff.diff) : { lines: [], items: [] },
    [displayDiff.diff],
  );

  const fileStructureItems = useMemo(() => {
    if (!fileContent) return [];
    return extractLeanStructureFromLines(fileContent.content.split('\n')).map(item => ({
      id: item.id,
      kind: item.kind,
      label: item.label,
      lineLabel: `line ${item.line}`,
    }));
  }, [fileContent]);

  const structureItems = viewMode === 'diff' ? diffStructure.items : fileStructureItems;

  const goNext = useCallback(() => {
    setCurrentIdx(i => Math.min(i + 1, totalEntries - 1));
  }, [totalEntries, setCurrentIdx]);

  const goPrev = useCallback(() => {
    setCurrentIdx(i => Math.max(i - 1, 0));
  }, [setCurrentIdx]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === 'ArrowRight' || e.key === 'j') goNext();
      else if (e.key === 'ArrowLeft' || e.key === 'k') goPrev();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [goNext, goPrev]);

  const goToLog = useCallback(() => {
    if (!currentEntry?.ts || !currentEntry?.proverLog || !currentEntry?.iteration) return;
    const params = new URLSearchParams({
      iter: currentEntry.iteration,
      file: currentEntry.proverLog,
      ts: currentEntry.ts,
    });
    const backSearch = new URLSearchParams({
      file: selectedSlug,
      iter: currentEntry.iteration,
      step: String(currentEntry.step),
      view: viewMode,
    }).toString();
    navigate(`/logs?${params.toString()}`, {
      state: { from: { pathname: '/diffs', search: `?${backSearch}` } },
    });
  }, [currentEntry, selectedSlug, viewMode, navigate]);

  // Latest commit for the iteration currently being diffed — hook must stay
  // above the early return below to keep hook order stable across renders.
  const iterCommit = useMemo(() => {
    if (!currentEntry?.iteration || !gitData?.commits) return undefined;
    return gitData.commits.find(c => c.iteration === currentEntry.iteration);
  }, [currentEntry?.iteration, gitData]);

  if (!files || files.length === 0) {
    return (
      <div className={styles.page}>
        <div className={styles.empty}>
          <h3>No snapshots found</h3>
          <p>Run archon-loop to generate code snapshots</p>
        </div>
      </div>
    );
  }

  const stepLabel = currentEntry
    ? currentEntry.step === 0 ? 'baseline' : `step ${currentEntry.step}`
    : '';

  return (
    <div className={styles.page}>
      <div className={styles.sidebar}>
        <div className={styles.sidebarTitle}>Files</div>
        <FileTree
          nodes={fileTree}
          selectedSlug={selectedSlug}
          expandedFolders={expandedFolders}
          onToggleFolder={toggleFolder}
          onSelectFile={handleSelectFile}
        />
      </div>

      <div className={styles.main}>
        {totalEntries > 0 && (
          <div className={styles.toolbar}>
            <div className={styles.stepNav}>
              <button className={styles.btn} onClick={goPrev} disabled={currentIdx <= 0}>◀</button>
              <span className={styles.stepInfo}>{currentIdx + 1} / {totalEntries}</span>
              <button className={styles.btn} onClick={goNext} disabled={currentIdx >= totalEntries - 1}>▶</button>
            </div>
            {currentEntry && (
              <span className={styles.iterLabel}>
                {currentEntry.iteration} · {stepLabel}
                {currentEntry.synthetic && (
                  <span
                    className={styles.iterSynthetic}
                    title="No prover snapshot at this iteration — content reconstructed from the inner archon git (or empty if the file did not exist yet)."
                  >
                    synthetic
                  </span>
                )}
                {iterCommit && (
                  <span className={styles.iterCommit} title={iterCommit.subject}>
                    {iterCommit.shortSha}
                  </span>
                )}
              </span>
            )}
            <div className={styles.compareControls}>
              <label className={styles.compareLabel}>
                <span>Compare to</span>
                <select className={styles.compareSelect} value={compareMode} onChange={e => handleCompareModeChange(e.target.value as DiffCompareMode)}>
                  <option value="previous">Previous</option>
                  <option value="baseline">Baseline</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              {compareMode === 'custom' && customOptions.length > 0 && (
                <label className={styles.compareLabel}>
                  <span>Snapshot</span>
                  <select
                    className={styles.compareSelectWide}
                    value={`${customCompareIter}::${customCompareStep}`}
                    onChange={e => {
                      const [iter, step] = e.target.value.split('::');
                      setCustomCompareIter(iter);
                      setCustomCompareStep(step);
                    }}
                  >
                    {customOptions.map(entry => (
                      <option key={`${entry.iteration}-${entry.step}`} value={`${entry.iteration}::${entry.step}`}>
                        {formatCompareLabel(entry)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <div className={styles.viewToggle}>
              <button className={`${styles.viewBtn} ${viewMode === 'diff' ? styles.viewBtnActive : ''}`} onClick={() => setViewMode('diff')}>Diff</button>
              <button className={`${styles.viewBtn} ${viewMode === 'file' ? styles.viewBtnActive : ''}`} onClick={() => setViewMode('file')}>File</button>
            </div>
            {currentEntry?.ts && (
              <button className={styles.btn} onClick={goToLog} title="View the log context for this edit">
                View log context →
              </button>
            )}
          </div>
        )}

        {currentEntry && (
          <div className={styles.inlineHintBar}>
            <span className={styles.inlineHintText}>
              {hintText.split('View log context')[0]}<strong>View log context</strong>{hintText.split('View log context')[1]}
            </span>
            {hasSourceMismatch && sourceFileLabel && (
              <span className={styles.inlineHintWarn}>Log-recorded edited file: {sourceFileLabel}</span>
            )}
          </div>
        )}

        {timeline && totalEntries > 1 && (
          <Scrubber
            timeline={timeline}
            currentIdx={currentIdx}
            compareIdx={compareIdx}
            compareLabel={compareEntry ? formatCompareLabel(compareEntry) : undefined}
            onSeek={setCurrentIdx}
          />
        )}

        <div className={styles.content}>
          {timelineLoading ? (
            <div className={styles.loading}>Loading timeline…</div>
          ) : totalEntries === 0 ? (
            <div className={styles.empty}>
              <h3>No snapshots</h3>
              <p>Select a file with edit history</p>
            </div>
          ) : viewMode === 'diff' ? (
            displayDiff.diff ? (
              <DiffView
                diff={displayDiff.diff}
                fromFile={compareMode === 'previous'
                  ? (currentIdx === 0 ? '(initial)' : timeline![currentIdx - 1]?.file)
                  : (compareEntry?.file || '(initial)')}
                toFile={currentEntry.file}
                addedLines={displayDiff.addedLines}
                removedLines={displayDiff.removedLines}
                activeId={activeId}
              />
            ) : (
              <div className={styles.empty}>
                <h3>{currentEntry?.step === 0 ? 'Baseline' : 'No changes'}</h3>
                <p>{currentEntry?.step === 0
                  ? 'This is the initial file state — switch to File view to see content'
                  : compareMode === 'previous'
                    ? 'This step is identical to the previous one'
                    : compareMode === 'baseline'
                      ? 'This step is identical to the baseline snapshot'
                      : 'This step is identical to the selected custom snapshot'}</p>
              </div>
            )
          ) : (
            fileContent ? (
              <FileSourceView
                content={fileContent.content}
                fileName={`${currentEntry?.iteration}/${currentEntry?.file}`}
                activeId={activeId}
              />
            ) : (
              <div className={styles.loading}>Loading file…</div>
            )
          )}
        </div>
      </div>

      {totalEntries > 0 && (
        <DiffStructurePanel
          title={viewMode === 'diff' ? 'Diff structure' : 'File structure'}
          items={structureItems}
          activeId={activeId}
          onJump={jumpTo}
        />
      )}
    </div>
  );
}
