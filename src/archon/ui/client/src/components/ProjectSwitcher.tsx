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

function dropContainerProjects(projects: ProjectOption[], keepPath: string): ProjectOption[] {
  // Hide pure "container" parents (a peer that only exists as an ancestor of
  // another peer and has no DAG of its own) to declutter the list — but never
  // drop the currently-scoped project, or the controlled <select value> would
  // reference an option that doesn't exist and render a blank/mismatched entry.
  return projects.filter((p) => p.current || p.has_dag || p.path === keepPath || !projects.some((q) => isStrictPathAncestor(p.path, q.path)));
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

function tailForProject(p: ProjectOption, commonLen: number): string[] {
  const rel = pathParts(p.path).slice(commonLen);
  return rel.length > 1 ? rel.slice(1) : [p.name];
}

function labelForProject(p: ProjectOption, tail: string[]): string {
  return `${tail.join('/')}${p.current ? ' (this project)' : ''}${p.has_dag ? '' : ' - no DAG'}`;
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
  ], selected).sort((a, b) => a.path.localeCompare(b.path));
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
          {members.map(project => {
            const tail = tailForProject(project, commonLen);
            return (
              <option key={project.path} value={project.path} title={project.path}>
                {labelForProject(project, tail)}
            </option>
            );
          })}
        </optgroup>
      ))}
    </select>
  );
}
