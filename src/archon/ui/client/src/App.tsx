import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { useProject } from './hooks/useApi';
import Overview from './views/Overview';
import LogViewer from './views/LogViewer';
import Journal from './views/Journal';
import DiffPlayback from './views/DiffPlayback';
import ProofGraph from './views/ProofGraph';
import DagView from './views/DagView';
import Blueprint from './views/Blueprint';
// Vite's resolveJsonModule (enabled by default) lets us import the
// version from package.json so the badge stays in sync with releases
// without manual updates. If you move package.json or the build setup
// changes, adjust this import path.
import { version as APP_VERSION } from '../../package.json';

function ConnectionBanner({ isError }: { isError: boolean }) {
  if (!isError) return null;
  return (
    <div style={{
      background: '#dc2626', color: 'white', padding: '6px 16px',
      fontSize: '13px', textAlign: 'center', fontWeight: 500,
    }}>
      ⚠ Cannot reach server — check that <code style={{ background: 'rgba(0,0,0,0.2)', padding: '1px 4px', borderRadius: 3 }}>
      archon dashboard &lt;project&gt;</code> is running and you're on the correct port
    </div>
  );
}

export default function App() {
  const { data: project, isError } = useProject();
  return (
    <div className="app">
      <ConnectionBanner isError={isError} />
      <header className="header">
        <h1>Archon</h1>
        <span className="version-badge" title={`Archon dashboard v${APP_VERSION}`}>
          v{APP_VERSION}
        </span>
        {project && <span className="project-badge" title={project.path}>{project.name}</span>}
        <nav className="header-nav">
          <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} end>Overview</NavLink>
          <NavLink to="/graph" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Graph</NavLink>
          <NavLink to="/dag" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>DAG</NavLink>
          <NavLink to="/blueprint" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Blueprint</NavLink>
          <NavLink to="/logs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Logs</NavLink>
          <NavLink to="/diffs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Diffs</NavLink>
          <NavLink to="/journal" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Journal</NavLink>
        </nav>
      </header>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/graph" element={<ProofGraph />} />
          <Route path="/dag" element={<DagView />} />
          <Route path="/blueprint" element={<Blueprint />} />
          <Route path="/logs" element={<LogViewer />} />
          <Route path="/diffs" element={<DiffPlayback />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}