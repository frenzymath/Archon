import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLogs } from '../hooks/useApi';
import { useLogDeepLink } from '../hooks/useLogDeepLink';
import { useLogStream } from '../hooks/useLogStream';
import type { LogEntry, LogGroup } from '../types';
import { fmtDuration, primaryModel, truncateSubject } from '../utils/format';
import LogEntryLine from '../components/LogEntryLine';
import MarkdownBlock from '../components/MarkdownBlock';
import styles from './LogViewer.module.css';

// --- Sidebar components ---

function PhaseTag({ label, status, secs }: { label: string; status?: string; secs?: number }) {
  if (!status) return null;
  const color = status === 'done' ? 'var(--green)' : status === 'running' ? 'var(--blue)' : status === 'error' ? 'var(--red)' : 'var(--text-muted)';
  return (
    <span className={styles.phase}>
      <span className={styles.phaseDot} style={{ background: color }} />
      {label}
      {secs != null && <span className={styles.phaseSecs}>{fmtDuration(secs * 1000)}</span>}
    </span>
  );
}

function ProverStatusBar({ provers }: { provers?: Record<string, { file: string; status: string }> }) {
  if (!provers) return null;
  const entries = Object.values(provers);
  const done = entries.filter(p => p.status === 'done').length;
  const error = entries.filter(p => p.status === 'error').length;
  const running = entries.filter(p => p.status === 'running').length;
  return (
    <div className={styles.proverBar}>
      {done > 0 && <span style={{ color: 'var(--green)' }}>✓{done}</span>}
      {running > 0 && <span style={{ color: 'var(--blue)' }}>●{running}</span>}
      {error > 0 && <span style={{ color: 'var(--red)' }}>✗{error}</span>}
      <span className={styles.proverTotal}>/{entries.length}</span>
    </div>
  );
}

function fmtElapsedMinutes(startedAt?: string, nowMs?: number): string {
  if (!startedAt || nowMs == null) return '';
  const startedMs = new Date(startedAt).getTime();
  if (Number.isNaN(startedMs)) return '';
  const elapsedMs = Math.max(0, nowMs - startedMs);
  const elapsedMin = elapsedMs / 60000;
  if (elapsedMin < 1) return '<1 min';
  return `${Math.floor(elapsedMin)} min`;
}

function IterGroup({ group, selectedFile, onSelect, isLatest, nowMs }: {
  group: LogGroup;
  selectedFile: string;
  onSelect: (path: string) => void;
  isLatest: boolean;
  nowMs: number;
}) {
  const hasSelected = group.files.some(f => f.path === selectedFile);
  const [expanded, setExpanded] = useState(hasSelected);
  const meta = group.meta;

  useEffect(() => { if (hasSelected) setExpanded(true); }, [hasSelected]);

  const isComplete = !!meta?.completedAt;
  const canShowRunning = isLatest && !isComplete;
  const isAnyRunning =
    meta?.prover?.status === 'running'
    || meta?.plan?.status === 'running'
    || meta?.review?.status === 'running'
    || meta?.refactor?.status === 'running';
  const runningElapsed = canShowRunning && isAnyRunning
    ? fmtElapsedMinutes(meta?.startedAt, nowMs)
    : '';

  // Active phase detection — order reflects loop execution (plan → refactor → prover → review)
  const activePhase = canShowRunning && meta
    ? (meta.review?.status === 'running' ? 'review'
      : meta.prover?.status === 'running' ? 'prover'
      : meta.refactor?.status === 'running' ? 'refactor'
      : meta.plan?.status === 'running' ? 'plan'
      : meta.stage)
    : undefined;
  // ``meta.stage`` is read literally from PROGRESS.md's "## Current
  // Stage" line, which is sometimes a single word (`prover`, `polish`)
  // and sometimes a free-form sentence. Cap the visible width so the
  // sidebar header doesn't grow to the whole subject when the stage
  // line is verbose. Full text remains in the title attribute below.
  const activePhaseDisplay = activePhase
    ? truncateSubject(activePhase, 14)
    : '';

  return (
    <div className={styles.group}>
      <div className={styles.groupHeader} onClick={() => setExpanded(!expanded)}>
        <span className={styles.toggle}>{expanded ? '▾' : '▸'}</span>
        <span className={styles.groupTitle}>
          {meta?.iteration != null ? `Iter #${meta.iteration}` : group.id}
        </span>
        {meta?.mode === 'parallel' && <span className={styles.groupMode}>∥</span>}
        {isComplete && <span className={styles.groupDone}>✓</span>}
        {canShowRunning && activePhase && (
          <span className={styles.groupStage} title={activePhase}>
            {activePhaseDisplay}
          </span>
        )}
        {canShowRunning && isAnyRunning && <span className={styles.groupLive}>●</span>}
        {runningElapsed && <span className={styles.groupElapsed}>{runningElapsed}</span>}
        {meta?.commit && (
          <span
            className={styles.commitBadge}
            title={meta.commit.subject}
          >
            {meta.commit.shortSha}
          </span>
        )}
      </div>

      {expanded && (
        <div className={styles.groupBody}>
          {meta?.commit && (
            <div className={styles.commitRow} title={meta.commit.subject}>
              <span className={styles.commitSha}>{meta.commit.shortSha}</span>
              <span className={styles.commitSubject}>
                {truncateSubject(meta.commit.subject, 32)}
              </span>
            </div>
          )}
          {meta && (
            <div className={styles.metaBar}>
              <PhaseTag label="plan" status={canShowRunning ? meta.plan?.status : (meta.plan?.status === 'done' ? 'done' : undefined)} secs={meta.plan?.durationSecs} />
              <PhaseTag label="refactor" status={canShowRunning ? meta.refactor?.status : (meta.refactor?.status === 'done' ? 'done' : undefined)} secs={meta.refactor?.durationSecs} />
              <PhaseTag label="prover" status={canShowRunning ? meta.prover?.status : (meta.prover?.status === 'done' ? 'done' : undefined)} secs={meta.prover?.durationSecs} />
              <PhaseTag label="review" status={canShowRunning ? meta.review?.status : (meta.review?.status === 'done' ? 'done' : undefined)} secs={meta.review?.durationSecs} />
              <ProverStatusBar provers={canShowRunning ? meta.provers : Object.fromEntries(Object.entries(meta.provers || {}).map(([k, v]) => [k, { ...v, status: v.status === 'done' ? 'done' : 'stale' }]))} />
            </div>
          )}

          {(() => {
            // A subagent report is shown INSIDE its parent stream's
            // view (rendered at the top of the main panel when the
            // stream is selected). Hide the report's own sidebar row
            // when a matching stream exists, so the sidebar lists one
            // entry per dispatch, not two. Orphan reports (no parent
            // stream — legacy archives from older Archon versions)
            // still render as standalone rows.
            const streamKeys = new Set<string>();
            for (const f of group.files) {
              const role = f.role || '';
              if (!isSubagentStreamRole(role)) continue;
              const inner = role.slice('subagent-'.length);
              streamKeys.add(subagentPairKey(inner, f.subagentSlug || ''));
            }
            return group.files.filter(f => {
              const role = f.role || '';
              if (!isSubagentReportRole(role, f.subagentSlug)) return true;
              const inner = role.slice(0, -'-report'.length);
              return !streamKeys.has(subagentPairKey(inner, f.subagentSlug || ''));
            });
          })().map(f => {
            const isProver = f.role === 'prover' && f.path.includes('/provers/');
            const isArtifact = f.name.endsWith('.md');
            const isSubagentStream = isSubagentStreamRole(f.role || '');
            const isSubagentReport = isSubagentReportRole(f.role || '', f.subagentSlug);
            const isSubagent = isSubagentStream || isSubagentReport;
            // Server attaches `subagentSlug` for subagent files; fall
            // back to the legacy filename match so the sidebar still
            // labels archived reports written by older Archon versions.
            let subagentSlug = f.subagentSlug ?? '';
            if (!subagentSlug && isArtifact) {
              const m = f.name.replace(/\.md$/, '').match(SUBAGENT_REPORT_RE);
              if (m) subagentSlug = m[2];
            }
            // The iter number is already in the group header above —
            // repeating it in every row is noise. Strip a trailing
            // ``[-]iter\d+(-<hex>)*`` so ``isiso-routes-iter139-0eb8963``
            // collapses to ``isiso-routes`` and a slug that's *only*
            // ``iter140`` renders as the empty string (just the role
            // chip stays).
            const displaySlug = isSubagent
              ? subagentSlug.replace(/(?:^|-)iter\d+(?:-[^-]+)*$/, '')
              : subagentSlug;

            let displayName: string;
            if (isProver) {
              // Slug shape:
              //   non-multilane:   "<dir>_<dir>_<filename>"
              //   multilane lane:  "<file_slug>__<lane>"
              //   multilane merge: "<file_slug>__merge"
              // Display: "<lane>//<filename>.lean" for multilane,
              // "<filename>.lean" otherwise. Full slug stays in the
              // tooltip via the existing `title` attribute.
              const base = f.name.replace('.jsonl', '');
              const splitIdx = base.lastIndexOf('__');
              let lane: string | null = null;
              let fileSlug: string;
              if (splitIdx >= 0) {
                lane = base.slice(splitIdx + 2);
                fileSlug = base.slice(0, splitIdx);
              } else {
                fileSlug = base;
              }
              const lastUnderscore = fileSlug.lastIndexOf('_');
              const fileBase = lastUnderscore >= 0
                ? fileSlug.slice(lastUnderscore + 1)
                : fileSlug;
              const fileName = fileBase ? `${fileBase}.lean` : '';
              displayName = lane ? `${lane}//${fileName}` : fileName;
            } else if (isSubagent) {
              displayName = displaySlug;
            } else if (isArtifact) {
              displayName = displaySlug;
            } else {
              displayName = f.role || f.name.replace('.jsonl', '');
            }

            if (f.name === 'provers-combined.jsonl') return null;

            const proverSlug = f.name.replace('.jsonl', '');
            const baseProverSlug = proverSlug.includes('__') ? proverSlug.slice(0, proverSlug.lastIndexOf('__')) : proverSlug;
            const proverStatus = isProver && meta?.provers?.[baseProverSlug]?.status;
            const proverMode = isProver ? (meta?.provers?.[baseProverSlug]?.mode ?? null) : null;
            const subagentRoleLabel = isSubagent
              ? subagentDisplayRole(f.role || '')
              : '';
            // Subagent stream icon: ▶ to read as "running invocation"
            // versus ◆ for the archived report (same family color).
            const subagentColor = ROLE_COLORS[f.role || ''] || '#888';

            return (
              <div
                key={f.path}
                className={`${styles.fileItem} ${f.path === selectedFile ? styles.fileItemActive : ''} ${isSubagent ? styles.fileItemSubagent : ''}`}
                onClick={() => onSelect(f.path)}
                title={f.commit ? `${f.name}\n${f.commit.shortSha} · ${f.commit.subject}` : f.name}
              >
                {isProver && (
                  <span className={styles.fileStatus} style={{
                    color: proverStatus === 'done' ? 'var(--green)' : proverStatus === 'running' ? 'var(--blue)' : proverStatus === 'error' ? 'var(--red)' : 'var(--text-muted)'
                  }}>●</span>
                )}
                {isSubagentStream && (
                  <span className={styles.fileStatus} style={{ color: subagentColor }}>▶</span>
                )}
                {isSubagentReport && (
                  <span className={styles.fileStatus} style={{ color: subagentColor }}>◆</span>
                )}
                {isArtifact && !isSubagent && (
                  <span
                    className={styles.fileStatus}
                    style={{ color: ROLE_COLORS[f.role || ''] || '#e36209' }}
                  >◆</span>
                )}
                {isSubagent ? (
                  <span
                    className={styles.fileSubagentRole}
                    style={{ color: subagentColor }}
                  >{subagentRoleLabel}</span>
                ) : (
                  !isProver && <span className={styles.fileRole}>{f.role}</span>
                )}
                <span className={styles.fileName}>
                  {isProver ? displayName : (displaySlug || '')}
                </span>
                {proverMode && <span className={styles.proverMode}>{proverMode}</span>}
                {f.commit && <span className={styles.fileCommit}>{f.commit.shortSha}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Role tag colors ---
const ROLE_COLORS: Record<string, string> = {
  plan: 'var(--blue)',
  'plan-post-refactor': 'var(--blue)',
  refactor: '#e36209',
  'refactor-manual': '#e36209',
  'refactor-directive': '#e36209',
  'refactor-report': '#e36209',
  // Subagent JSONL streams (one entry per `archon subagent <role>` run).
  // Same colors as the corresponding `<role>-report` so a stream and
  // its archived report read as the same family.
  'subagent-refactor': '#e36209',
  'subagent-analogy': '#a371f7',
  'subagent-challenger': '#cf222e',
  // Subagent reports archived by the plan agent.
  'analogy-report': '#a371f7',     // Mathlib-precedent analogies.
  'challenger-report': '#cf222e',  // Challenges/<Name>.lean sanity checks.
  prover: 'var(--purple)',
  review: 'var(--orange)',
};

// Files generated by a subagent invocation: a JSONL stream + a final
// report. Detected generically so any new subagent — blueprint-reviewer,
// strategy-critic, progress-critic, mathlib-analogist, … — surfaces
// without UI changes. The relationship to the parent dispatch is
// inferred from the role+subagentSlug pair attached by the server.
function isSubagentStreamRole(role: string): boolean {
  return role.startsWith('subagent-');
}
// A `-report` role is a subagent report only when the server tagged
// it with a subagentSlug. The legacy refactor-phase ``refactor-report.md``
// artifact (no slug, no parent dispatch) is NOT a subagent report.
function isSubagentReportRole(role: string, subagentSlug: string | undefined): boolean {
  return role.endsWith('-report') && !!subagentSlug;
}

function subagentDisplayRole(role: string): string {
  // "subagent-refactor" → "refactor"; "analogy-report" → "analogy".
  if (role.startsWith('subagent-')) return role.slice('subagent-'.length);
  if (role.endsWith('-report')) return role.slice(0, -'-report'.length);
  return role;
}

// Pair a subagent stream with its report: both share the inner role
// name (the slice between ``subagent-`` and ``-report``) plus the
// same subagentSlug. The map key encodes both.
function subagentPairKey(innerName: string, slug: string): string {
  return `${innerName}|${slug}`;
}

const SUBAGENT_REPORT_RE = /^([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(.+)-report$/;

const FILTER_OPTIONS = [
  { value: 'prompt', label: 'prompt' },
  { value: 'shell', label: 'shell' },
  { value: 'thinking', label: 'thinking' },
  { value: 'tool_call', label: 'tool call' },
  { value: 'tool_result', label: 'tool result' },
  { value: 'text', label: 'text' },
  { value: 'code_snapshot', label: 'snapshot' },
  { value: 'session_end', label: 'session end' },
] as const;

type FilterEvent = typeof FILTER_OPTIONS[number]['value'];

const DEFAULT_FILTERS: FilterEvent[] = FILTER_OPTIONS.map(option => option.value);

// --- Run summary bar (from session_end entry) ---
function RunSummaryBar({ entries }: { entries: LogEntry[] }) {
  const sessionEnd = entries.find(e => e.event === 'session_end');
  if (!sessionEnd) return null;
  const model = primaryModel(sessionEnd.model_usage);
  const parts: string[] = [];
  if (model) parts.push(model);
  if (sessionEnd.duration_ms) parts.push(fmtDuration(sessionEnd.duration_ms));
  if (sessionEnd.num_turns) parts.push(`${sessionEnd.num_turns} turns`);
  if (sessionEnd.total_cost_usd) parts.push(`$${sessionEnd.total_cost_usd.toFixed(2)}`);
  if (sessionEnd.input_tokens) parts.push(`${(sessionEnd.input_tokens / 1000).toFixed(0)}K in`);
  if (sessionEnd.output_tokens) parts.push(`${(sessionEnd.output_tokens / 1000).toFixed(0)}K out`);
  if (!parts.length) return null;
  return <div className={styles.sessionSummary}>{parts.join(' · ')}</div>;
}

// --- Main LogViewer ---

export default function LogViewer() {
  const [selectedFile, setSelectedFile] = useState('');
  const [selectedFilters, setSelectedFilters] = useState<FilterEvent[]>(DEFAULT_FILTERS);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const navigate = useNavigate();
  const highlightRef = useRef<HTMLDivElement>(null);

  const { data: logsData } = useLogs();
  const { initialSelectedFile, initialHighlightTs, backTarget } = useLogDeepLink(logsData);
  const { entries, streaming } = useLogStream(selectedFile);
  const highlightConsumedRef = useRef(false);

  const selectedIsArtifact = selectedFile.endsWith('.md');

  const goBackToDiffs = () => {
    if (!backTarget) return;
    navigate(`${backTarget.pathname}${backTarget.search || ''}`);
  };

  const toggleFilter = (event: FilterEvent) => {
    setSelectedFilters(current => (
      current.includes(event)
        ? current.filter(value => value !== event)
        : [...current, event]
    ));
  };

  const resetFilters = () => {
    setSelectedFilters(DEFAULT_FILTERS);
  };

  const allFiltersSelected = selectedFilters.length === DEFAULT_FILTERS.length;
  const selectedFilterSet = useMemo(() => new Set<FilterEvent>(selectedFilters), [selectedFilters]);

  useEffect(() => {
    const interval = window.setInterval(() => setNowMs(Date.now()), 30000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!selectedFile && initialSelectedFile) {
      setSelectedFile(initialSelectedFile);
    }
  }, [selectedFile, initialSelectedFile]);

  useEffect(() => {
    if (highlightConsumedRef.current) return;
    if (!selectedFile || !initialSelectedFile || selectedFile !== initialSelectedFile) return;
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
      highlightConsumedRef.current = true;
    }
  }, [selectedFile, initialSelectedFile, entries]);

  const closestHighlightTs = useMemo(() => {
    if (selectedFile !== initialSelectedFile) return '';
    if (!initialHighlightTs || entries.length === 0) return '';
    const targetTime = new Date(initialHighlightTs).getTime();
    if (isNaN(targetTime)) return '';
    let minDist = Infinity;
    let bestTs = '';
    for (const e of entries) {
      if (!e.ts || e.event === 'session_end') continue;
      const dist = Math.abs(new Date(e.ts).getTime() - targetTime);
      if (dist < minDist) { minDist = dist; bestTs = e.ts; }
    }
    return bestTs;
  }, [entries, initialHighlightTs, selectedFile, initialSelectedFile]);

  const selectedRole = useMemo(() => {
    if (!logsData || !selectedFile) return '';
    for (const g of logsData.groups) {
      const f = g.files.find(f => f.path === selectedFile);
      if (f?.role) return f.role;
    }
    return '';
  }, [logsData, selectedFile]);

  const selectedSubagentSlug = useMemo(() => {
    if (!logsData || !selectedFile) return '';
    for (const g of logsData.groups) {
      const f = g.files.find(f => f.path === selectedFile);
      if (f) return f.subagentSlug || '';
    }
    return '';
  }, [logsData, selectedFile]);

  // When a subagent STREAM is selected, locate the matching report
  // file in the same iter group so we can render its markdown at the
  // top of the main panel (cleaner than a separate sidebar row).
  // The pairing key is the inner role name + the shared subagentSlug.
  const matchingReportPath = useMemo(() => {
    if (!logsData || !selectedFile || !isSubagentStreamRole(selectedRole)) return '';
    const inner = selectedRole.slice('subagent-'.length);
    for (const g of logsData.groups) {
      const stream = g.files.find(f => f.path === selectedFile);
      if (!stream) continue;
      const report = g.files.find(f => {
        const r = f.role || '';
        return isSubagentReportRole(r, f.subagentSlug)
          && f.subagentSlug === stream.subagentSlug
          && r.slice(0, -'-report'.length) === inner;
      });
      return report?.path || '';
    }
    return '';
  }, [logsData, selectedFile, selectedRole]);

  // One-shot fetch of the report markdown. The report is final once
  // the subagent finishes; no need for a WebSocket. The state stays
  // empty until the report file exists on disk — at which point the
  // top-of-panel block appears, exactly the "shows up when the agent
  // finishes" behavior we want.
  const [reportContent, setReportContent] = useState<string>('');
  useEffect(() => {
    setReportContent('');
    if (!matchingReportPath) return;
    let cancelled = false;
    fetch(`/api/logs/${matchingReportPath}`)
      .then(r => r.ok ? r.json() : [])
      .then((rows: LogEntry[]) => {
        if (cancelled) return;
        const first = Array.isArray(rows) ? rows[0] : null;
        const content = (first && first.content) || '';
        setReportContent(content);
      })
      .catch(() => { /* report not landed yet — show nothing */ });
    return () => { cancelled = true; };
  }, [matchingReportPath]);

  const selectedRoleLabel = useMemo(() => {
    if (!selectedRole) return '';
    const isSubagent = isSubagentStreamRole(selectedRole)
      || selectedRole.endsWith('-report');
    if (!isSubagent) return selectedRole;
    const role = subagentDisplayRole(selectedRole);
    const tag = isSubagentStreamRole(selectedRole)
      ? 'subagent'
      : 'report';
    return selectedSubagentSlug
      ? `${role} ${tag} · ${selectedSubagentSlug}`
      : `${role} ${tag}`;
  }, [selectedRole, selectedSubagentSlug]);

  const selectedCommit = useMemo(() => {
    if (!logsData || !selectedFile) return undefined;
    for (const g of logsData.groups) {
      const f = g.files.find(f => f.path === selectedFile);
      if (f?.commit) return f.commit;
    }
    return undefined;
  }, [logsData, selectedFile]);

  useEffect(() => {
    if (!logsData || selectedFile || initialSelectedFile) return;
    if (logsData.groups.length > 0) {
      const lastGroup = logsData.groups[logsData.groups.length - 1];
      if (lastGroup.files.length > 0) { setSelectedFile(lastGroup.files[0].path); return; }
    }
    if (logsData.flat.length > 0) setSelectedFile(logsData.flat[0].path);
  }, [logsData, selectedFile, initialSelectedFile]);

  const filtered = useMemo(() => {
    if (selectedIsArtifact) return entries;
    return entries.filter(e => selectedFilterSet.has(e.event as FilterEvent));
  }, [entries, selectedFilterSet, selectedIsArtifact]);

  const visibleEntries = useMemo(() => filtered.filter(e => e.event !== 'session_end'), [filtered]);

  const sessionEnd = useMemo(() => entries.find(e => e.event === 'session_end'), [entries]);
  const showSessionSummary = !selectedIsArtifact && !!sessionEnd && selectedFilterSet.has('session_end');
  // Prefer the LAST assistant text emitted over the session_end's
  // ``summary`` field. When the agent dispatches subagents in parallel,
  // it often emits a "Waiting for the N subagents…" text and *that*
  // gets frozen into the session_end summary; the agent's real final
  // message lands later. Walking the entries gives the user the most
  // recent assistant output regardless of when session_end was sealed.
  const summaryText = useMemo(() => {
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (e.event !== 'text') continue;
      const t = (e.content || '').trim();
      if (t) return e.content as string;
    }
    return sessionEnd?.summary || '';
  }, [entries, sessionEnd]);

  const selectedLabel = selectedFile.replace(/\.jsonl$/, '').replace(/\.md$/, '').replace(/\//g, ' / ');
  const latestGroupId = useMemo(() => {
    if (!logsData?.groups?.length) return '';
    return logsData.groups.reduce((latest, group) => {
      const latestIter = latest.meta?.iteration ?? Number.NEGATIVE_INFINITY;
      const groupIter = group.meta?.iteration ?? Number.NEGATIVE_INFINITY;
      if (groupIter > latestIter) return group;
      if (groupIter === latestIter && group.id > latest.id) return group;
      return latest;
    }).id;
  }, [logsData]);

  // For .md artifacts the server returns a single entry with event="text"
  const artifactContent = selectedIsArtifact && entries.length > 0 ? (entries[0].content || '') : '';

  return (
    <div className={styles.root}>
      {/* Sidebar */}
      <div className={styles.sidebar}>
        {logsData?.groups.slice().reverse().map(g => (
          <IterGroup
            key={g.id}
            group={g}
            selectedFile={selectedFile}
            onSelect={setSelectedFile}
            isLatest={g.id === latestGroupId}
            nowMs={nowMs}
          />
        ))}

        {logsData?.flat && logsData.flat.length > 0 && (
          <div className={styles.group}>
            <div className={styles.groupHeader}>
              <span className={styles.groupTitle}>Legacy logs</span>
            </div>
            <div className={styles.groupBody}>
              {logsData.flat.map(f => (
                <div
                  key={f.path}
                  className={`${styles.fileItem} ${f.path === selectedFile ? styles.fileItemActive : ''}`}
                  onClick={() => setSelectedFile(f.path)}
                >
                  <span className={styles.fileName}>{f.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {!logsData?.groups.length && !logsData?.flat.length && (
          <div className={styles.emptyHint}>No logs yet</div>
        )}
      </div>

      {/* Main content */}
      <div className={styles.main}>
        <div className={styles.toolbar}>
          {backTarget && (
            <button className={styles.backBtn} onClick={goBackToDiffs} title="Back to Diffs view">
              ← Diffs
            </button>
          )}
          {selectedRole && (
            <span className={styles.roleTag} style={{ color: ROLE_COLORS[selectedRole] || 'var(--text-muted)' }}>
              {selectedRoleLabel}
            </span>
          )}
          <span className={styles.selectedLabel}>{selectedLabel || 'Select a log'}</span>
          {selectedCommit && (
            <span
              className={styles.selectedCommit}
              title={`${selectedCommit.shortSha} · ${selectedCommit.subject}`}
            >
              {selectedCommit.shortSha}
              <span className={styles.selectedCommitSubject}>
                {truncateSubject(selectedCommit.subject, 80)}
              </span>
            </span>
          )}
          {!selectedIsArtifact && (
            <div className={styles.filterBar} aria-label="Event type filters">
              <span className={styles.filterLabel}>Show</span>
              <div className={styles.filterChips}>
                {FILTER_OPTIONS.map(option => {
                  const active = selectedFilterSet.has(option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      className={`${styles.filterChip} ${active ? styles.filterChipActive : ''}`}
                      onClick={() => toggleFilter(option.value)}
                      aria-pressed={active}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
              {!allFiltersSelected && (
                <button type="button" className={styles.resetFiltersBtn} onClick={resetFilters}>
                  Reset
                </button>
              )}
            </div>
          )}
          {streaming && !selectedIsArtifact && <span className={styles.live}>● live</span>}
          <span className={styles.count}>
            {selectedIsArtifact ? `${artifactContent.length.toLocaleString()} chars` : `${filtered.length} entries`}
          </span>
        </div>

        {showSessionSummary && <RunSummaryBar entries={entries} />}

        <div className={styles.container}>
          {/* Render markdown artifacts inline */}
          {selectedIsArtifact && artifactContent && (
            <div className={styles.summaryBlock}>
              <MarkdownBlock content={artifactContent} className={styles.summaryText} />
            </div>
          )}

          {/* JSONL logs: summary block + entries */}
          {/* Subagent report inlined on top of its stream's view —
              users read the dispatch's output without leaving the
              stream's page. Empty until the report .md lands on
              disk, which is exactly when the subagent finishes. */}
          {!selectedIsArtifact && reportContent && (
            <div className={styles.summaryBlock}>
              <span className={styles.summaryLabel}>Report</span>
              <MarkdownBlock content={reportContent} className={styles.summaryText} />
            </div>
          )}

          {!selectedIsArtifact && showSessionSummary && summaryText && (
            <div className={styles.summaryBlock}>
              <span className={styles.summaryLabel}>Summary</span>
              <MarkdownBlock content={summaryText} className={styles.summaryText} />
            </div>
          )}

          {!selectedIsArtifact && (() => {
            let highlightAttached = false;
            return visibleEntries.slice().reverse().map((e, i) => {
              const isHighlighted = !!(closestHighlightTs && e.ts === closestHighlightTs);
              const attachRef = isHighlighted && !highlightAttached;
              if (attachRef) highlightAttached = true;
              return (
                <div key={e.ts ? `${e.ts}-${e.event}-${i}` : `entry-${i}`}
                     ref={attachRef ? highlightRef : undefined}
                     style={isHighlighted ? { background: 'rgba(3,102,214,0.08)', borderLeft: '3px solid var(--blue)' } : undefined}>
                  <LogEntryLine entry={e} />
                </div>
              );
            });
          })()}

          {selectedFile && !selectedIsArtifact && filtered.length === 0 && (
            <div className={styles.emptyContent}>No entries match the current filters.</div>
          )}

          {entries.length === 0 && selectedFile && (
            <div className={styles.emptyContent}>
              {selectedIsArtifact ? 'Artifact is empty.' : 'No entries in this log file yet.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}