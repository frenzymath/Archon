import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
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

interface HistoryEntry {
  iterId: string;
  iterNum: number;
  metrics: LeanMetrics;
  hasSteps: boolean;
}

interface Props {
  iterId: string;
  proverSlug: string;
  isLive: boolean;
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ values, values2, color, color2 = '#93c5fd', currentIdx, w = 120, h = 32 }: {
  values: number[];
  values2?: number[];
  color: string;
  color2?: string;
  currentIdx: number;
  w?: number;
  h?: number;
}) {
  if (values.length === 0) return <svg width={w} height={h} />;

  // Shared Y range across both series so they're directly comparable
  const allVals = values2 ? [...values, ...values2] : values;
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const range = max - min || 1;
  const pad = 3;

  const px = (i: number) => pad + (i / Math.max(values.length - 1, 1)) * (w - pad * 2);
  const py = (v: number) => pad + (1 - (v - min) / range) * (h - pad * 2);

  const pts1 = values.map((v, i) => `${px(i)},${py(v)}`).join(' ');
  const pts2 = values2?.map((v, i) => `${px(i)},${py(v)}`).join(' ');

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ overflow: 'visible' }}>
      <line x1={pad} y1={py(min)} x2={w - pad} y2={py(min)} stroke="var(--border)" strokeWidth="0.5" />
      {/* secondary series: dashed, drawn first so primary sits on top */}
      {pts2 && (
        <polyline points={pts2} fill="none" stroke={color2}
          strokeWidth="1.5" strokeDasharray="3,2"
          strokeLinejoin="round" strokeLinecap="round" />
      )}
      <polyline points={pts1} fill="none" stroke={color}
        strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      {/* dots for primary */}
      {values.map((v, i) => (
        <circle key={i} cx={px(i)} cy={py(v)}
          r={i === currentIdx ? 3.5 : 1.5}
          fill={i === currentIdx ? color : 'var(--bg-primary)'}
          stroke={color} strokeWidth={i === currentIdx ? 0 : 1} />
      ))}
      {/* dot for secondary at current iter */}
      {values2 && currentIdx >= 0 && currentIdx < values2.length && (
        <circle cx={px(currentIdx)} cy={py(values2[currentIdx])}
          r={3} fill={color2} stroke="none" />
      )}
    </svg>
  );
}

// ── MetricsChart popover ───────────────────────────────────────────────────────

const CHART_METRICS: Array<{
  key: keyof LeanMetrics;
  key2?: keyof LeanMetrics;   // secondary series overlaid as dashed line
  label: string;
  color: string;
  color2?: string;
  lowerBetter: boolean;
}> = [
  { key: 'sorries',  label: 'sorry',  color: '#f85149', lowerBetter: true  },
  { key: 'loc', key2: 'locNoComments', label: 'LOC', color: '#58a6ff', color2: '#93c5fd', lowerBetter: false },
  { key: 'lemmas',   label: 'lemmas', color: '#3fb950', lowerBetter: false },
  { key: 'defs',     label: 'defs',   color: '#d2a8ff', lowerBetter: false },
];

function MetricsChart({ history, currentIterId, currentMetrics }: {
  history: HistoryEntry[];
  currentIterId: string;
  currentMetrics: LeanMetrics;
}) {
  const currentIterNum = parseInt(currentIterId.replace('iter-', ''), 10);
  const rawIdx = history.findIndex(h => h.iterId === currentIterId);

  // Always show live metrics for the current iter — snapshot steps may lag
  // behind the live file that the bar already reflects. If the current iter
  // has no history entry at all (no snapshots yet), append a synthetic point.
  let effectiveHistory: HistoryEntry[];
  let currentIdx: number;
  if (rawIdx >= 0) {
    effectiveHistory = history.map((h, i) =>
      i === rawIdx ? { ...h, metrics: currentMetrics } : h
    );
    currentIdx = rawIdx;
  } else {
    effectiveHistory = [
      ...history,
      { iterId: currentIterId, iterNum: currentIterNum, metrics: currentMetrics, hasSteps: false },
    ];
    currentIdx = effectiveHistory.length - 1;
  }

  return (
    <div className={styles.chartGrid}>
      {CHART_METRICS.map(({ key, key2, label, color, color2, lowerBetter }) => {
        const values  = effectiveHistory.map(h => h.metrics[key]);
        const values2 = key2 ? effectiveHistory.map(h => h.metrics[key2]) : undefined;
        const current  = values[currentIdx]  ?? 0;
        const current2 = values2 ? (values2[currentIdx] ?? 0) : undefined;
        const first = values[0] ?? 0;
        const trend = current - first;
        const trendColor = trend === 0 ? undefined
          : (lowerBetter ? (trend < 0 ? '#3fb950' : '#f85149')
                         : (trend > 0 ? '#3fb950' : '#f85149'));
        return (
          <div key={key} className={styles.chartCell}>
            <div className={styles.chartCellHeader}>
              <span className={styles.chartMetricLabel} style={{ color }}>{label}</span>
              <span className={styles.chartMetricVal}>{current}</span>
              {current2 !== undefined && (
                <span className={styles.chartMetricVal2} style={{ color: color2 }}>/{current2}</span>
              )}
              {trend !== 0 && (
                <span className={styles.chartTrend} style={{ color: trendColor }}>
                  {trend > 0 ? `+${trend}` : trend}
                </span>
              )}
            </div>
            <Sparkline values={values} values2={values2} color={color} color2={color2} currentIdx={currentIdx} />
          </div>
        );
      })}
      <div className={styles.chartFooter}>
        {effectiveHistory.length} iter{effectiveHistory.length !== 1 ? 's' : ''} ·
        iter {effectiveHistory[0]?.iterNum}–{effectiveHistory[effectiveHistory.length - 1]?.iterNum}
      </div>
    </div>
  );
}

// ── MetricDelta ───────────────────────────────────────────────────────────────

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

// ── Main component ─────────────────────────────────────────────────────────────

export default function ProverMetaHeader({ iterId, proverSlug, isLive }: Props) {
  const [summary, setSummary] = useState<MetaSummary | null>(null);
  const [showComments, setShowComments] = useState(true);
  const [showDiff, setShowDiff] = useState(false);

  // Hover chart state
  const [showChart, setShowChart] = useState(false);
  const [chartPos, setChartPos] = useState<{ top: number; left: number } | null>(null);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const metricsRowRef = useRef<HTMLDivElement>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Reset cached history whenever the target prover changes so we never show
  // stale data from a previously-selected log file.
  useEffect(() => {
    setHistory(null);
    setShowChart(false);
    if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
  }, [iterId, proverSlug]);

  const fetchSummary = useCallback(() => {
    fetch(`/api/iterations/${encodeURIComponent(iterId)}/snapshots/${encodeURIComponent(proverSlug)}/meta-summary`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setSummary(data); })
      .catch(() => {});
  }, [iterId, proverSlug]);

  useEffect(() => {
    fetchSummary();
    if (isLive) {
      pollRef.current = setInterval(fetchSummary, 15000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [fetchSummary, isLive]);

  // Hover: schedule chart open after delay
  const scheduleOpen = useCallback(() => {
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
    if (hoverTimerRef.current) return;
    hoverTimerRef.current = setTimeout(() => {
      hoverTimerRef.current = null;
      if (metricsRowRef.current) {
        const rect = metricsRowRef.current.getBoundingClientRect();
        setChartPos({ top: rect.bottom + 6, left: rect.left });
      }
      setShowChart(true);
      if (!history) {
        fetch(`/api/snapshot-files/${encodeURIComponent(proverSlug)}/metrics-history`)
          .then(r => r.ok ? r.json() : null)
          .then(data => { if (data) setHistory(data); })
          .catch(() => {});
      }
    }, 350);
  }, [history, proverSlug]);

  const scheduleClose = useCallback(() => {
    if (hoverTimerRef.current) { clearTimeout(hoverTimerRef.current); hoverTimerRef.current = null; }
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      setShowChart(false);
    }, 120);
  }, []);

  const cancelClose = useCallback(() => {
    if (closeTimerRef.current) { clearTimeout(closeTimerRef.current); closeTimerRef.current = null; }
  }, []);

  useEffect(() => () => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
  }, []);

  if (!summary) return null;

  const { baseline, latest, diff, diffAddedLines, diffRemovedLines, objective, totalSteps } = summary;
  const locBefore = showComments ? baseline.loc : baseline.locNoComments;
  const locAfter  = showComments ? latest.loc  : latest.locNoComments;
  const hasDiffContent = summary.hasBeforeContent || summary.hasAfterContent;

  return (
    <div className={styles.header}>
      {/* Sticky metrics row */}
      <div
        ref={metricsRowRef}
        className={styles.metricsRow}
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
      >
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

      {/* Objective — scrolls with content */}
      {objective && (
        <div className={styles.objectiveBlock}>
          <span className={styles.objectiveLabel}>Objective</span>
          <MarkdownBlock content={objective} className={styles.objectiveContent} />
        </div>
      )}

      {/* Diff — separate collapsible block */}
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

      {/* Hover sparkline chart — rendered in a portal so it isn't clipped */}
      {showChart && chartPos && summary && createPortal(
        <div
          className={styles.chartPopover}
          style={{ top: chartPos.top, left: chartPos.left }}
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
        >
          {history
            ? <MetricsChart history={history} currentIterId={iterId} currentMetrics={summary.latest} />
            : <span className={styles.chartLoading}>loading…</span>}
        </div>,
        document.body,
      )}
    </div>
  );
}
