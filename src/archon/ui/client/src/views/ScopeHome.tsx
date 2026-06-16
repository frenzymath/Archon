import { useScope } from '../hooks/useApi';
import { setProjectScope, getProjectScope } from '../lib/projectScope';
import MarkdownBlock from '../components/MarkdownBlock';
import styles from './ScopeHome.module.css';

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

  const handleSwitchProject = (path: string | null) => {
    setProjectScope(path);
    window.location.reload();
  };

  return (
    <div className={styles.root}>
      <aside className={styles.sidebar}>
        <h2 className={styles.sidebarTitle}>Member Projects</h2>
        <div className={styles.projectList}>
          <button
            className={`${styles.projectButton} ${!activeScope ? styles.active : ''}`}
            onClick={() => handleSwitchProject(null)}
          >
            <div className={styles.projectName}>Meta Scope View (Union)</div>
            <div className={styles.projectPath}>{scope.scopePath}</div>
          </button>
          {scope.members?.map((m) => (
            <button
              key={m.path}
              className={`${styles.projectButton} ${activeScope === m.path ? styles.active : ''}`}
              onClick={() => handleSwitchProject(m.path)}
            >
              <div className={styles.projectName}>
                {m.name} {!m.has_dag && <span className={styles.noDagBadge}>no DAG</span>}
              </div>
              <div className={styles.projectPath}>{m.path}</div>
            </button>
          ))}
        </div>
      </aside>

      <section className={styles.content}>
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
      </section>
    </div>
  );
}
