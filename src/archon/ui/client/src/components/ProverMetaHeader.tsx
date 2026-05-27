import { useState, useEffect, useRef } from 'react';
import MarkdownBlock from './MarkdownBlock';
import DiffView from './DiffView';
import styles from './ProverMetaHeader.module.css';

interface LeanMetrics {
  sorries: number;
  loc: number;
  locNoComments: number;
  defs: number;
  lemmas: number;
  axioms: number;
}

interface MetaSummary {
  leanFile: string;
  baseline: LeanMetrics;
  latest: LeanMetrics;
  totalSteps: number;
  diff: string;
  diffAddedLines: number;
  diffRemovedLines: number;
  objective: string;
  hasBeforeContent: boolean;
  hasAfterContent: boolean;
}

interface Props {
  iterId: string;
  proverSlug: string;
  isLive: boolean;
}

function MetricDelta({ label, before, after, lowerIsBetter = false }: {
  label: string; before: number; after: number; lowerIsBetter?: boolean;
}) {
  const delta = after - before;
  let deltaClass = styles.deltaNeutral;
  if (delta !== 0) {
    const isGood = lowerIsBetter ? delta < 0 : delta > 0;
    deltaClass = isGood ? styles.deltaGood : styles.deltaBad;
  }
  return (
    <span className={styles.metric}>
      <span className={styles.metricLabel}>{label}:</span>
      <span className={styles.metricValue}>
        {before}→{after}
        {delta !== 0 && (
          <span className={`${styles.delta} ${deltaClass}`}>
            ({delta > 0 ? '+' : ''}{delta})
          </span>
        )}
      </span>
    </span>
  );
}

export default function ProverMetaHeader({ iterId, proverSlug, isLive }: Props) {
  const [summary, setSummary] = useState<MetaSummary | null>(null);
  const [showComments, setShowComments] = useState(true);
  const [showDiff, setShowDiff] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchSummary = () => {
    fetch(`/api/iterations/${encodeURIComponent(iterId)}/snapshots/${encodeURIComponent(proverSlug)}/meta-summary`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setSummary(data); })
      .catch(() => {});
  };

  useEffect(() => {
    fetchSummary();
    if (isLive) {
      intervalRef.current = setInterval(fetchSummary, 15000);
    }
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [iterId, proverSlug, isLive]);

  if (!summary) return null;

  const { baseline, latest, diff, diffAddedLines, diffRemovedLines, objective, totalSteps } = summary;
  const locBefore = showComments ? baseline.loc : baseline.locNoComments;
  const locAfter  = showComments ? latest.loc  : latest.locNoComments;
  const hasDiffContent = summary.hasBeforeContent || summary.hasAfterContent;

  return (
    <div className={styles.header}>
      <div className={styles.metricsRow}>
        <MetricDelta label="sorry" before={baseline.sorries} after={latest.sorries} lowerIsBetter />
        <MetricDelta label="LOC"   before={locBefore}        after={locAfter} />
        <MetricDelta label="lemmas" before={baseline.lemmas}  after={latest.lemmas} />
        <MetricDelta label="defs"  before={baseline.defs}    after={latest.defs} />
        {(baseline.axioms > 0 || latest.axioms > 0) && (
          <MetricDelta label="axioms" before={baseline.axioms} after={latest.axioms} lowerIsBetter />
        )}
        <span
          className={styles.commentToggle}
          onClick={() => setShowComments(v => !v)}
          title={showComments ? 'Exclude comment lines from LOC' : 'Include comment lines in LOC'}
        >
          {showComments ? 'excl. comments' : 'incl. comments'}
        </span>
        {hasDiffContent && (
          <button
            className={styles.toggleBtn}
            onClick={() => setShowDiff(v => !v)}
          >
            {showDiff
              ? 'hide diff'
              : diff
                ? `show diff (+${diffAddedLines} -${diffRemovedLines})`
                : 'show diff (no changes yet)'}
          </button>
        )}
        {totalSteps > 0 && (
          <span className={styles.snapCount}>
            {totalSteps} snapshot{totalSteps !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {objective && (
        <div className={styles.objectiveBlock}>
          <span className={styles.objectiveLabel}>Objective</span>
          <MarkdownBlock content={objective} className={styles.objectiveContent} />
        </div>
      )}

      {showDiff && hasDiffContent && (
        <div className={styles.diffBlock}>
          <DiffView
            diff={diff}
            fromFile={`prev/${summary.leanFile}`}
            toFile={`curr/${summary.leanFile}`}
            addedLines={diffAddedLines}
            removedLines={diffRemovedLines}
          />
        </div>
      )}
    </div>
  );
}
