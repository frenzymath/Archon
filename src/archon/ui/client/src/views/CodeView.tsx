/**
 * CodeView — browse the current working-tree Lean source.
 *
 * Left: file tree of `.lean` files; right: highlighted source of the selected
 * file. No iteration history — for the time-series diff view, see DiffPlayback
 * (live mode only). Works identically in static mode because both endpoints
 * (`/api/source/files`, `/api/source/file?path=`) are pre-snapshotted.
 */
import { useEffect, useMemo, useState } from 'react';
import { useSourceFiles, useSourceFile, type SourceFileSummary } from '../hooks/useSource';
import LeanCodeLine from '../components/LeanCodeLine';
import { highlightLeanLines } from '../utils/leanHighlight';
import styles from './CodeView.module.css';

type TreeNode = {
  name: string;
  path: string;
  children: TreeNode[];
  file?: SourceFileSummary;
};

function buildTree(files: SourceFileSummary[]): TreeNode[] {
  const root: TreeNode = { name: '', path: '', children: [] };
  for (const file of files) {
    const parts = file.path.split('/').filter(Boolean);
    let current = root;
    let prefix = '';
    parts.forEach((part, idx) => {
      prefix = prefix ? `${prefix}/${part}` : part;
      let child = current.children.find((c) => c.name === part);
      if (!child) {
        child = { name: part, path: prefix, children: [] };
        current.children.push(child);
      }
      if (idx === parts.length - 1) child.file = file;
      current = child;
    });
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      const aDir = !a.file;
      const bDir = !b.file;
      if (aDir !== bDir) return aDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach((n) => sort(n.children));
  };
  sort(root.children);
  return root.children;
}

function FileTree({
  nodes,
  selected,
  onSelect,
}: {
  nodes: TreeNode[];
  selected: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div className={styles.tree}>
      {nodes.map((node) =>
        node.file ? (
          <button
            key={node.path}
            className={`${styles.fileButton} ${selected === node.file.path ? styles.active : ''}`}
            onClick={() => onSelect(node.file!.path)}
            title={node.file.path}
          >
            {node.name}
          </button>
        ) : (
          <details key={node.path} open>
            <summary>{node.name}/</summary>
            <div>
              <FileTree nodes={node.children} selected={selected} onSelect={onSelect} />
            </div>
          </details>
        ),
      )}
    </div>
  );
}

export default function CodeView() {
  const { data, isLoading, isError } = useSourceFiles();
  const files = data?.files ?? [];
  const tree = useMemo(() => buildTree(files), [files]);

  const [selected, setSelected] = useState<string | null>(null);
  // Auto-pick the first file once the list arrives, so the right pane isn't blank.
  useEffect(() => {
    if (!selected && files.length > 0) setSelected(files[0].path);
  }, [files, selected]);

  const { data: fileContent, isLoading: contentLoading, isError: contentError } = useSourceFile(selected);

  const highlighted = useMemo(
    () => (fileContent?.content ? highlightLeanLines(fileContent.content.split('\n')) : null),
    [fileContent?.content],
  );

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar}>
        <h2 className={styles.sidebarTitle}>Lean files ({files.length})</h2>
        {isLoading && <div className={styles.placeholder}>Loading…</div>}
        {isError && <div className={styles.error}>Failed to load file list.</div>}
        {!isLoading && !isError && files.length === 0 && (
          <div className={styles.placeholder}>No .lean files found.</div>
        )}
        <FileTree nodes={tree} selected={selected} onSelect={setSelected} />
      </aside>
      <section className={styles.content}>
        <header className={styles.contentHeader}>
          <span>{selected ?? '(no file selected)'}</span>
          {fileContent && <span>{fileContent.size.toLocaleString()} bytes</span>}
        </header>
        <div className={styles.contentBody}>
          {!selected && <div className={styles.placeholder}>Select a file to view its source.</div>}
          {selected && contentLoading && <div className={styles.placeholder}>Loading file…</div>}
          {selected && contentError && (
            <div className={styles.error}>Could not load this file.</div>
          )}
          {selected && highlighted && (
            <pre className={styles.code}>
              {highlighted.map((tokens, lineIdx) => (
                <div key={lineIdx}>
                  <LeanCodeLine text={fileContent?.content.split('\n')[lineIdx] ?? ''} tokens={tokens} />
                </div>
              ))}
            </pre>
          )}
        </div>
      </section>
    </div>
  );
}
