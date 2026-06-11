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
      <option value={data.current.path}>{data.current.name} (this project)</option>
      {data.peers.map(p => (
        <option key={p.path} value={p.path}>
          {p.name}
          {p.has_dag ? '' : ' — no DAG'}
        </option>
      ))}
    </select>
  );
}
