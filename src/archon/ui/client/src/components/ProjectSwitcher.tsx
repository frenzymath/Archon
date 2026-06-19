/**
 * ProjectSwitcher — dropdown in the header to view a peer project read-only.
 *
 * Lists the base project plus the peers it declares in `.archon/peers.yaml`
 * (served by /api/peer-projects). Selecting one stores the scope and reloads,
 * so every data hook refetches against the chosen project. Renders nothing
 * when the project has no peers, so single-project dashboards are unchanged.
 */
import { useEffect, useState } from 'react';
import { getProjectScope, setProjectScope } from '../lib/projectScope';

interface PeerEntry {
  name: string;
  path: string;
  has_dag: boolean;
}
interface PeerProjects {
  current: { name: string; path: string };
  peers: PeerEntry[];
}

interface ProjectOption extends PeerEntry {
  current?: boolean;
}

function pathParts(path: string): string[] {
  return path.split(/[\\/]+/).filter(Boolean);
}

function isStrictPathAncestor(parent: string, child: string): boolean {
  const pp = pathParts(parent);
  const cp = pathParts(child);
  return pp.length < cp.length && pp.every((part, i) => cp[i] === part);
}

function dropContainerProjects(projects: ProjectOption[]): ProjectOption[] {
  return projects.filter((p) => p.current || p.has_dag || !projects.some((q) => isStrictPathAncestor(p.path, q.path)));
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

function groupForProject(p: ProjectOption, commonLen: number): string {
  const rel = pathParts(p.path).slice(commonLen);
  return rel.length > 1 ? rel[0] : 'Projects';
}

type ProjectRow =
  | { type: 'folder'; key: string; label: string; depth: number }
  | { type: 'project'; project: ProjectOption; tail: string[] };

const SELECT_INDENT = '\u00a0\u00a0\u00a0\u00a0';

function tailForProject(p: ProjectOption, commonLen: number): string[] {
  const rel = pathParts(p.path).slice(commonLen);
  return rel.length > 1 ? rel.slice(1) : [p.name];
}

function rowsForProjects(projects: ProjectOption[], commonLen: number): ProjectRow[] {
  const rows: ProjectRow[] = [];
  const seenFolders = new Set<string>();
  for (const p of projects) {
    const tail = tailForProject(p, commonLen);
    for (let i = 0; i < tail.length - 1; i += 1) {
      const key = tail.slice(0, i + 1).join('/');
      if (seenFolders.has(key)) continue;
      seenFolders.add(key);
      rows.push({ type: 'folder', key, label: tail[i], depth: i });
    }
    rows.push({ type: 'project', project: p, tail });
  }
  return rows;
}

function labelForProject(p: ProjectOption, tail: string[]): string {
  const prefix = tail.length > 1 ? SELECT_INDENT.repeat(tail.length - 1) : '';
  return `${prefix}${tail[tail.length - 1]}${p.current ? ' (this project)' : ''}${p.has_dag ? '' : ' - no DAG'}`;
}

export function ProjectSwitcher() {
  const [data, setData] = useState<PeerProjects | null>(null);

  useEffect(() => {
    fetch('/api/peer-projects')
      .then(r => (r.ok ? r.json() : null))
      .then((d: PeerProjects | null) => setData(d))
      .catch(() => setData(null));
  }, []);

  if (!data || data.peers.length === 0) return null;

  const selected = getProjectScope() || data.current.path;
  const projects: ProjectOption[] = dropContainerProjects([
    { ...data.current, has_dag: true, current: true },
    ...data.peers,
  ]).sort((a, b) => a.path.localeCompare(b.path));
  const commonLen = commonPrefixLen(projects.map((p) => pathParts(p.path)));
  const groups = projects.reduce<Map<string, ProjectOption[]>>((acc, p) => {
    const group = groupForProject(p, commonLen);
    const members = acc.get(group) ?? [];
    members.push(p);
    acc.set(group, members);
    return acc;
  }, new Map());

  const onChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const path = e.target.value;
    setProjectScope(path === data.current.path ? null : path);
    // Full reload: simplest correct way to refetch every view against the
    // newly-scoped project (also resets the react-query cache).
    window.location.reload();
  };

  return (
    <select
      className="project-switcher"
      value={selected}
      onChange={onChange}
      title="View a peer project read-only (from .archon/peers.yaml)"
    >
      {[...groups.entries()].map(([group, members]) => (
        <optgroup key={group} label={group}>
          {rowsForProjects(members, commonLen).map(row => row.type === 'folder' ? (
            <option key={`folder-${row.key}`} disabled>
              {SELECT_INDENT.repeat(row.depth)}{row.label}
            </option>
          ) : (
            <option key={row.project.path} value={row.project.path} title={row.project.path}>
              {labelForProject(row.project, row.tail)}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
