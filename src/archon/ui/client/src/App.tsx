import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { useProject, useScope } from './hooks/useApi';
import Overview from './views/Overview';
import LogViewer from './views/LogViewer';
import Journal from './views/Journal';
import DiffPlayback from './views/DiffPlayback';
import DagView from './views/DagView';
import Blueprint from './views/Blueprint';
import CodeView from './views/CodeView';
import ScopeHome from './views/ScopeHome';
import { ProjectSwitcher } from './components/ProjectSwitcher';
import { isStaticDashboard } from './lib/staticMode';
import { getProjectScope, isStaticScope } from './lib/projectScope';
// Vite's resolveJsonModule (enabled by default) lets us import the
// version from package.json so the badge stays in sync with releases
// without manual updates. If you move package.json or the build setup
// changes, adjust this import path.
import { version as APP_VERSION } from '../../package.json';

function ConnectionBanner({ isError }: { isError: boolean }) {
  if (isStaticDashboard()) return null;
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
  const { data: scope } = useScope();
  const isStatic = isStaticDashboard();
  const staticScope = isStaticScope();
  const inScopeMode = !!scope?.inScope;
  const showScopeHome = inScopeMode;
  const showCode = isStatic;
  const projectScope = getProjectScope();
  // In live mode the project switcher always shows. In static mode it only
  // shows when the snapshot was built for a scope (and thus has per-member
  // JSON files behind the switcher).
  const showProjectSwitcher = !isStatic || staticScope;

  return (
    <div className="app">
      <ConnectionBanner isError={isError} />
      <header className="header">
        <h1>Archon</h1>
        <span className="version-badge" title={`Archon dashboard v${APP_VERSION}`}>
          v{APP_VERSION}
        </span>
        {project && <span className="project-badge" title={project.path}>{project.name}</span>}
        {isStatic && <span className="project-badge" title={window.__ARCHON_STATIC__?.generatedAt}>static</span>}
        {showProjectSwitcher && <ProjectSwitcher />}
        <nav className="header-nav">
          {showScopeHome && (
            <NavLink to="/scope" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
              Scope Home
            </NavLink>
          )}
          <NavLink to={inScopeMode ? '/overview' : '/'} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} end>Overview</NavLink>
          <NavLink to="/dag" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>DAG</NavLink>
          <NavLink to="/blueprint" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Blueprint</NavLink>
          {showCode && <NavLink to="/code" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Code</NavLink>}
          {!isStatic && <NavLink to="/logs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Logs</NavLink>}
          {!isStatic && <NavLink to="/diffs" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Diffs</NavLink>}
          <NavLink to="/journal" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>Journal</NavLink>
        </nav>
      </header>
      <main className="main-content">
        <Routes>
          <Route
            path="/"
            element={inScopeMode && !projectScope ? <Navigate to="/scope" replace /> : <Overview />}
          />
          {/* Explicit Overview route so the host/current project's Overview is
              reachable in scope mode. Selecting the host clears the project
              scope to null, which makes "/" redirect to Scope Home; the nav
              link targets this route instead so Overview opens for every
              project (host included), not just peers. */}
          <Route path="/overview" element={<Overview />} />
          {/* The old proof-graph view is superseded by the DAG. */}
          <Route path="/graph" element={<Navigate to="/dag" replace />} />
          <Route path="/dag" element={<DagView />} />
          <Route path="/blueprint" element={<Blueprint />} />
          <Route path="/code" element={showCode ? <CodeView /> : <Navigate to="/diffs" replace />} />
          <Route path="/scope" element={showScopeHome ? <ScopeHome /> : <Navigate to="/" replace />} />
          <Route path="/logs" element={isStatic ? <Navigate to="/" replace /> : <LogViewer />} />
          <Route path="/diffs" element={isStatic ? <Navigate to="/" replace /> : <DiffPlayback />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
