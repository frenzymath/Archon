import { useProgress, useStrategy, useSummary, useSorryCount, useTasks, useToUserAlert } from '../hooks/useApi';
import { useGitHead } from '../hooks/useGitLog';
import { fmtDuration, fmtTime, truncateSubject } from '../utils/format';
import { STATUS_COLORS } from '../utils/constants';
import MarkdownBlock from '../components/MarkdownBlock';
import styles from './Overview.module.css';

/** Render inline markdown: **bold** and `code` */
function InlineMd({ text }: { text: string }) {
  const html = text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

const STAGES = ['init', 'autoformalize', 'prover', 'polish', 'COMPLETE'];

export default function Overview() {
  const { data: progress } = useProgress();
  const { data: strategy } = useStrategy();
  const { data: summary } = useSummary();
  const { data: sorryData } = useSorryCount();
  const { data: tasks } = useTasks();
  const { data: headData } = useGitHead();
  const { data: toUserAlert } = useToUserAlert();

  const stage = progress?.stage || 'init';
  const stageIdx = STAGES.indexOf(stage);
  const head = headData?.commit;

  return (
    <div className={styles.root}>
      {toUserAlert && (
        <div className={styles.alertBanner}>
          <strong>Message from Archon to the user</strong> <br/>
          <MarkdownBlock content={toUserAlert} className={styles.alertText} />
        </div>
      )}
      {head && (
        <div className={styles.commitBanner} title={head.subject}>
          <span className={styles.commitSha}>{head.shortSha}</span>
          <span className={styles.commitBranch}>{head.branch}</span>
          <span className={styles.commitSubject}>{truncateSubject(head.subject, 100)}</span>
        </div>
      )}
      <div className={styles.stages}>
        {STAGES.map((s, i) => (
          <span key={s} className={i === stageIdx ? styles.current : i < stageIdx ? styles.done : styles.future}>
            {i < stageIdx ? '✓ ' : ''}{s}
          </span>
        )).reduce<React.ReactNode[]>((acc, el, i) => {
          if (i > 0) acc.push(<span key={`sep-${i}`} className={styles.sep}>→</span>);
          acc.push(el);
          return acc;
        }, [])}
      </div>

      {sorryData && sorryData.total > 0 && (
        <div className={styles.sorrySection}>
          <span className={styles.sorryCount}>{sorryData.total} sorry</span>
          <span className={styles.sorryDetail}>
            {sorryData.files.map(f => `${f.file} (${f.count})`).join(', ')}
          </span>
        </div>
      )}
      {sorryData && sorryData.total === 0 && (
        <div className={styles.sorrySection}>
          <span className={styles.sorryDone}>0 sorry ✓</span>
        </div>
      )}

      {summary && summary.sessionCount > 0 && (
        <div className={styles.summaryLine}>
          {summary.sessionCount} session{summary.sessionCount > 1 ? 's' : ''}
          {' · '}{fmtDuration(summary.totalDuration)}
        </div>
      )}

      {progress?.objectives && progress.objectives.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Objectives</div>
          <ul className={styles.list}>
            {progress.objectives.map((o, i) => <li key={i}><InlineMd text={o} /></li>)}
          </ul>
        </div>
      )}

      {/* Long-arc strategy. The plan agent owns this file; we render
          whatever it has written. The shipped template is just
          headings + HTML-comment guidance, so until the planner adds
          actual content we show a placeholder rather than an empty
          collapsible. */}
      {(() => {
        const raw = strategy?.content ?? '';
        if (!raw.trim()) return null;
        const stripped = raw.replace(/<!--[\s\S]*?-->/g, '');
        const hasBody = stripped.split('\n').some(line => {
          const t = line.trim();
          return t.length > 0 && !t.startsWith('#');
        });
        return (
          <details className={styles.strategyBox} open>
            <summary className={styles.strategySummary}>Strategy</summary>
            {hasBody
              ? <MarkdownBlock content={raw} className={styles.strategyMd} />
              : <div className={styles.strategyPlaceholder}>Plan agent hasn't drafted a strategy yet.</div>}
          </details>
        );
      })()}

      {tasks && tasks.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Tasks</div>
          <div className={styles.taskList}>
            {tasks.map(t => (
              <div key={t.id} className={styles.task}>
                <span className={styles.taskStatus} style={{ color: STATUS_COLORS[t.status] || 'var(--text-muted)' }}>●</span>
                <span className={styles.taskTheorem}>{t.theorem}</span>
                {t.file && <span className={styles.taskFile}>{t.file}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {progress?.checklist && progress.checklist.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Stages</div>
          <ul className={styles.list}>
            {progress.checklist.map((c, i) => (
              <li key={i} className={c.done ? styles.checkDone : ''}>
                {c.done ? '✓' : '○'} {c.label}
              </li>
            ))}
          </ul>
        </div>
      )}

      {summary?.sessions && summary.sessions.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionLabel}>Sessions</div>
          <div className={styles.tableScroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Operation</th>
                  <th>Model</th>
                  <th>Duration</th>
                  <th>Turns</th>
                  <th style={{ textAlign: 'right' }}>In Tokens</th>
                  <th style={{ textAlign: 'right' }}>Out Tokens</th>
                  <th style={{ textAlign: 'right' }}>Cost</th>
                </tr>
              </thead>
              <tbody>
                {summary.sessions.slice().reverse().map((s, i) => (
                  <tr key={i}>
                    <td>{fmtTime(s.timestamp)}</td>
                    <td style={{ fontWeight: 'bold' }}>{s.operation || 'session'}</td>
                    <td>{s.model}</td>
                    <td>{fmtDuration(s.duration)}</td>
                    <td>{s.turns}</td>
                    <td style={{ textAlign: 'right' }}>{s.tokensIn.toLocaleString()}</td>
                    <td style={{ textAlign: 'right' }}>{s.tokensOut.toLocaleString()}</td>
                    <td style={{ textAlign: 'right', color: 'var(--green, #28a745)', fontWeight: 'bold' }}>
                      ${s.cost.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
