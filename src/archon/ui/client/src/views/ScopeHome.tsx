import { useScope } from '../hooks/useApi';
import { setProjectScope, getProjectScope } from '../lib/projectScope';
import { apiUrl } from '../utils/constants';
import MarkdownBlock from '../components/MarkdownBlock';
import styles from './ScopeHome.module.css';

interface ScopeMember {
  name: string;
  path: string;
  has_dag: boolean;
}

function pathParts(path: string): string[] {
  return path.split(/[\\/]+/).filter(Boolean);
}

function isStrictPathAncestor(parent: string, child: string): boolean {
  const pp = pathParts(parent);
  const cp = pathParts(child);
  return pp.length < cp.length && pp.every((part, i) => cp[i] === part);
}

function dropContainerMembers(members: ScopeMember[]): ScopeMember[] {
  return members.filter((m) => m.has_dag || !members.some((n) => isStrictPathAncestor(m.path, n.path)));
}

function commonPrefixLen(items: string[][]): number {
  if (items.length === 0) return 0;
  let n = items[0].length;
  for (const parts of items.slice(1)) {
    let i = 0;
    while (i < n && i < parts.length && parts[i] === items[0][i]) i += 1;
    n = i;
  }
  return n;
}

function groupForMember(member: ScopeMember, commonLen: number): string {
  const rel = pathParts(member.path).slice(commonLen);
  return rel.length > 1 ? rel[0] : 'Projects';
}

type MemberRow =
  | { type: 'folder'; key: string; label: string; depth: number }
  | { type: 'member'; member: ScopeMember; tail: string[] };

function tailForMember(member: ScopeMember, commonLen: number): string[] {
  const rel = pathParts(member.path).slice(commonLen);
  return rel.length > 1 ? rel.slice(1) : [member.name];
}

function rowsForMembers(members: ScopeMember[], commonLen: number): MemberRow[] {
  const rows: MemberRow[] = [];
  const seenFolders = new Set<string>();
  for (const member of members) {
    const tail = tailForMember(member, commonLen);
    for (let i = 0; i < tail.length - 1; i += 1) {
      const key = tail.slice(0, i + 1).join('/');
      if (seenFolders.has(key)) continue;
      seenFolders.add(key);
      rows.push({ type: 'folder', key, label: tail[i], depth: i });
    }
    rows.push({ type: 'member', member, tail });
  }
  return rows;
}

function labelForMember(tail: string[]): string {
  return tail[tail.length - 1];
}

export default function ScopeHome() {
  const { data: scope, isLoading } = useScope();

  if (isLoading) {
    return <div style={{ padding: 24, color: 'var(--text-muted)' }}>Loading scope status...</div>;
  }

  if (!scope || !scope.inScope) {
    return (
      <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>
        <p>This dashboard is not running in scope mode.</p>
        <p>To run in scope mode, initialize a scope with <code>archon scope init</code> and launch with <code>archon scope dashboard</code>.</p>
      </div>
    );
  }

  const activeScope = getProjectScope();
  const members = dropContainerMembers((scope.members ?? []) as ScopeMember[])
    .sort((a, b) => a.path.localeCompare(b.path));
  const commonLen = commonPrefixLen(members.map((m) => pathParts(m.path)));
  const groups = members.reduce<Map<string, ScopeMember[]>>((acc, m) => {
    const group = groupForMember(m, commonLen);
    const groupMembers = acc.get(group) ?? [];
    groupMembers.push(m);
    acc.set(group, groupMembers);
    return acc;
  }, new Map());

  const handleSwitchProject = (path: string | null) => {
    setProjectScope(path);
    // Stay on Scope Home so the README/Roadmap on the right is unaffected by
    // the left-panel selection. The reload clears react-query's cache so any
    // subsequent navigation (DAG, Blueprint…) refetches with the new scope.
    // HashRouter (static export) and BrowserRouter (live) need different URL
    // forms; pick whichever style is currently in use.
    if (window.location.hash.startsWith('#/')) {
      window.location.hash = '#/scope';
    } else {
      // apiUrl keeps the URL under BASE_URL, so a path-prefix reverse proxy
      // (and the router basename) is preserved rather than jumping to origin root.
      window.history.replaceState(null, '', apiUrl('/scope'));
    }
    window.location.reload();
  };

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar}>
        <h2 className={styles.sidebarTitle}>Member Projects</h2>
        <div className={styles.projectList}>
          {[...groups.entries()].map(([group, groupMembers]) => (
            <div key={group} className={styles.projectGroup}>
              <div className={styles.projectGroupTitle}>{group}</div>
              {rowsForMembers(groupMembers, commonLen).map((row) => row.type === 'folder' ? (
                <div
                  key={`folder-${row.key}`}
                  className={styles.projectFolderTitle}
                >
                  {row.label}
                </div>
              ) : (
                <button
                  key={row.member.path}
                  className={`${styles.projectButton} ${activeScope === row.member.path ? styles.active : ''}`}
                  onClick={() => handleSwitchProject(row.member.path)}
                >
                  <div className={styles.projectName}>
                    {labelForMember(row.tail)} {!row.member.has_dag && <span className={styles.noDagBadge}>no DAG</span>}
                  </div>
                </button>
              ))}
            </div>
          ))}
        </div>
      </aside>

      <section className={styles.content}>
        {scope.roadmap ? (
          <div className={styles.card}>
            <h2 className={styles.cardTitle}>Scope Roadmap</h2>
            <div className={styles.markdownWrapper}>
              <MarkdownBlock content={scope.roadmap} />
            </div>
          </div>
        ) : (
          <div className={`${styles.card} ${styles.placeholderCard}`}>
            <h2 className={styles.cardTitle}>Scope Roadmap</h2>
            <p className={styles.placeholderText}>No roadmap generated yet. Run <code>archon scope roadmap</code> in the scope directory to create it.</p>
          </div>
        )}


        {scope.readme ? (
          <div className={styles.card}>
            <h2 className={styles.cardTitle}>Scope README</h2>
            <div className={styles.markdownWrapper}>
              <MarkdownBlock content={scope.readme} />
            </div>
          </div>
        ) : (
          <div className={`${styles.card} ${styles.placeholderCard}`}>
            <h2 className={styles.cardTitle}>Scope README</h2>
            <p className={styles.placeholderText}>No README.md found in the scope root folder.</p>
          </div>
        )}
      </section>
    </div>
  );
}
