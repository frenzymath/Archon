import fs from 'fs';
import path from 'path';

interface MultiLaneRow extends Record<string, unknown> {
  lane_id?: string;
  success?: boolean;
}

export interface MultiLaneIterationLaneSummary {
  laneId: string;
  assignedCount: number;
  resultCount: number;
  successCount: number;
  proverLogCount: number;
  resultFileCount: number;
}

export interface MultiLaneIterationSummary {
  iteration: number;
  slug: string;
  assignmentsPath?: string;
  resultsPath?: string;
  reportPath?: string;
  assignmentCount: number;
  resultCount: number;
  successCount: number;
  laneCount: number;
  lanes: MultiLaneIterationLaneSummary[];
}

export interface MultiLaneSummary {
  enabled: boolean;
  configPresent: boolean;
  localConfigPresent: boolean;
  rootPath?: string;
  latestIteration?: MultiLaneIterationSummary;
  iterations: MultiLaneIterationSummary[];
}

function readJsonlRows(filePath: string): MultiLaneRow[] {
  if (!fs.existsSync(filePath)) return [];
  return fs.readFileSync(filePath, 'utf-8')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      try { return JSON.parse(line) as MultiLaneRow; }
      catch { return null; }
    })
    .filter((row): row is MultiLaneRow => row !== null);
}

function extractIteration(name: string): number | null {
  const m = name.match(/iter-(\d{3})/);
  return m ? parseInt(m[1], 10) : null;
}

function relativeOrUndefined(root: string, target: string): string | undefined {
  return fs.existsSync(target) ? path.relative(root, target) : undefined;
}

function readEnabledFlag(configPath: string, localPath: string): boolean {
  let enabled: boolean | undefined;
  for (const filePath of [configPath, localPath]) {
    if (!fs.existsSync(filePath)) continue;
    try {
      const parsed = JSON.parse(fs.readFileSync(filePath, 'utf-8')) as { enabled?: unknown };
      if (typeof parsed.enabled === 'boolean') enabled = parsed.enabled;
    } catch {
      // Ignore malformed experimental config here; dashboard should stay readable.
    }
  }
  return enabled ?? false;
}

export function readMultiLaneSummary(archonPath: string): MultiLaneSummary {
  const rootPath = path.join(archonPath, 'multilane');
  const configPath = path.join(rootPath, 'config.json');
  const localPath = path.join(rootPath, 'config.local.json');
  if (!fs.existsSync(rootPath)) {
    return { enabled: false, configPresent: false, localConfigPresent: false, iterations: [] };
  }

  const runtimeDir = path.join(rootPath, 'runtime');
  const reportsDir = path.join(rootPath, 'reports');
  const lanesDir = path.join(rootPath, 'lanes');
  const iterationIds = new Set<number>();

  for (const dir of [runtimeDir, reportsDir]) {
    if (!fs.existsSync(dir)) continue;
    for (const name of fs.readdirSync(dir)) {
      const iteration = extractIteration(name);
      if (iteration != null) iterationIds.add(iteration);
    }
  }

  if (fs.existsSync(lanesDir)) {
    for (const laneId of fs.readdirSync(lanesDir)) {
      const lanePath = path.join(lanesDir, laneId);
      if (!fs.statSync(lanePath).isDirectory()) continue;
      for (const name of fs.readdirSync(lanePath)) {
        const iteration = extractIteration(name);
        if (iteration != null) iterationIds.add(iteration);
      }
    }
  }

  const iterations = [...iterationIds].sort((a, b) => b - a).map(iteration => {
    const slug = `iter-${String(iteration).padStart(3, '0')}`;
    const assignmentsPath = path.join(runtimeDir, `${slug}-assignments.jsonl`);
    const resultsPath = path.join(runtimeDir, `${slug}-results.jsonl`);
    const reportPath = path.join(reportsDir, `${slug}-execution.md`);
    const assignments = readJsonlRows(assignmentsPath);
    const results = readJsonlRows(resultsPath);
    const laneMap = new Map<string, MultiLaneIterationLaneSummary>();

    for (const row of assignments) {
      const laneId = String(row.lane_id || 'unknown');
      const current = laneMap.get(laneId) || { laneId, assignedCount: 0, resultCount: 0, successCount: 0, proverLogCount: 0, resultFileCount: 0 };
      current.assignedCount += 1;
      laneMap.set(laneId, current);
    }

    for (const row of results) {
      const laneId = String(row.lane_id || 'unknown');
      const current = laneMap.get(laneId) || { laneId, assignedCount: 0, resultCount: 0, successCount: 0, proverLogCount: 0, resultFileCount: 0 };
      current.resultCount += 1;
      if (row.success === true) current.successCount += 1;
      laneMap.set(laneId, current);
    }

    if (fs.existsSync(lanesDir)) {
      for (const laneId of fs.readdirSync(lanesDir)) {
        const laneIterDir = path.join(lanesDir, laneId, slug);
        if (!fs.existsSync(laneIterDir)) continue;
        const current = laneMap.get(laneId) || { laneId, assignedCount: 0, resultCount: 0, successCount: 0, proverLogCount: 0, resultFileCount: 0 };
        const proversDir = path.join(laneIterDir, 'provers');
        const resultsDir = path.join(laneIterDir, 'results');
        if (fs.existsSync(proversDir)) current.proverLogCount = fs.readdirSync(proversDir).filter(name => name.endsWith('.jsonl')).length;
        if (fs.existsSync(resultsDir)) current.resultFileCount = fs.readdirSync(resultsDir).filter(name => name.endsWith('.md')).length;
        laneMap.set(laneId, current);
      }
    }

    const lanes = [...laneMap.values()].sort((a, b) => a.laneId.localeCompare(b.laneId));
    return {
      iteration,
      slug,
      assignmentsPath: relativeOrUndefined(archonPath, assignmentsPath),
      resultsPath: relativeOrUndefined(archonPath, resultsPath),
      reportPath: relativeOrUndefined(archonPath, reportPath),
      assignmentCount: assignments.length,
      resultCount: results.length,
      successCount: results.filter(row => row.success === true).length,
      laneCount: lanes.length,
      lanes,
    } satisfies MultiLaneIterationSummary;
  });

  return {
    enabled: readEnabledFlag(configPath, localPath),
    configPresent: fs.existsSync(configPath),
    localConfigPresent: fs.existsSync(localPath),
    rootPath: rootPath,
    latestIteration: iterations[0],
    iterations,
  };
}
