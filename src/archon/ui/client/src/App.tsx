import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { useProject } from './hooks/useApi';
import Overview from './views/Overview';
import LogViewer from './views/LogViewer';
import Journal from './views/Journal';
import DiffPlayback from './views/DiffPlayback';
import ProofGraph from './views/ProofGraph';

function ConnectionBanner({ isError }: { isError: boolean }) {
  if (!isError) return null;
  return (
    <div style={{
      background: '#dc3545', color: 'white', padding: '6px 16px',
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
        {project && <span className="project-badge" title={project.path}>{project.name}</span>}
        <nav className="header-nav">
          <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} end>Overview</NavLink>
          <NavLink to="/graph" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Graph</NavLink>
          <NavLink to="/logs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Logs</NavLink>
          <NavLink to="/diffs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Diffs</NavLink>
          <NavLink to="/journal" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Journal</NavLink>
        </nav>
      </header>
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/graph" element={<ProofGraph />} />
          <Route path="/logs" element={<LogViewer />} />
          <Route path="/diffs" element={<DiffPlayback />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}