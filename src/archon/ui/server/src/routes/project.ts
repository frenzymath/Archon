/** Project, progress, tasks, sorry count — core project state routes */
import fs from 'fs';
import path from 'path';
import type { FastifyInstance } from 'fastify';
import type { ProgressData, Task } from '../types.js';
import { readFileOr } from '../utils.js';
import { countSorriesInProject } from '../utils/sorryCount.js';

export interface ProjectPaths {
  projectPath: string;
  archonPath: string;
  logsPath: string;
}

function parseProgressMarkdown(content: string): ProgressData {
  const stageMatch = content.match(/## Current Stage\s*\n\s*(\S+)/);
  const stage = stageMatch?.[1] || 'init';

  const objectives: string[] = [];
  const objSection = content.match(/## Current Objectives\s*\n([\s\S]*?)(?=\n## |\n# |$)/);
  if (objSection) {
    for (const line of objSection[1].split('\n')) {
      const m = line.match(/^\s*\d+\.\s+(.+)/);
      if (m) objectives.push(m[1].trim());
    }
  }

  const checklist: { label: string; done: boolean }[] = [];
  const stagesSection = content.match(/## Stages\s*\n([\s\S]*?)(?=\n## |\n# |$)/);
  if (stagesSection) {
    for (const line of stagesSection[1].split('\n')) {
      const m = line.match(/^\s*-\s*\[([ xX])\]\s*(.+)/);
      if (m) checklist.push({ label: m[2].trim(), done: m[1] !== ' ' });
    }
  }
  return { stage, objectives, checklist };
}

function parseTasksMarkdown(content: string, status: 'pending' | 'done'): Task[] {
  const tasks: Task[] = [];
  let currentFile = '';
  for (const line of content.split('\n')) {
    const fileMatch = line.match(/^## (.+\.lean)/);
    if (fileMatch) {
      currentFile = fileMatch[1].trim();
      continue;
    }
    const thMatch = line.match(/^### (.+)/);
    if (thMatch) {
      tasks.push({ id: '', theorem: thMatch[1].trim(), file: currentFile, status, proofSketch: '' });
    }
  }
  return tasks.map((t, i) => ({ ...t, id: `task-${status}-${i}` }));
}

export function register(fastify: FastifyInstance, paths: ProjectPaths) {
  const { projectPath, archonPath } = paths;
  const taskResultsPath = path.join(archonPath, 'task_results');

  fastify.get('/api/project', async () => ({
    name: path.basename(projectPath),
    path: projectPath,
    archonPath,
  }));

  fastify.get('/api/progress', async () => {
    const content = readFileOr(path.join(archonPath, 'PROGRESS.md'), '');
    return parseProgressMarkdown(content);
  });

  // The plan agent's long-arc strategy doc. Shape: just the raw markdown.
  // The dashboard renders it as-is in the Overview pane so the user can
  // see the planner's current sketch of how the whole project lands. We
  // intentionally don't parse the file here — the schema is loose by
  // design, and the planner may add or rename sections at any iteration.
  fastify.get('/api/strategy', async () => {
    return { content: readFileOr(path.join(archonPath, 'STRATEGY.md'), '') };
  });

  fastify.get('/api/tasks', async () => {
    const pending = parseTasksMarkdown(readFileOr(path.join(archonPath, 'task_pending.md'), ''), 'pending');
    const done = parseTasksMarkdown(readFileOr(path.join(archonPath, 'task_done.md'), ''), 'done');
    return [...pending, ...done];
  });

  fastify.get('/api/sorry-count', async () => {
    return countSorriesInProject(projectPath);
  });

  fastify.get('/api/task-results', async () => {
    if (!fs.existsSync(taskResultsPath)) return [];
    return fs.readdirSync(taskResultsPath).filter(f => f.endsWith('.md')).map(f => ({
      name: f,
      content: fs.readFileSync(path.join(taskResultsPath, f), 'utf-8'),
    }));
  });
}
