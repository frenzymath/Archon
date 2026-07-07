import { useState } from 'react';
import type { LogEntry } from '../types';
import { fmtTime, primaryModel, truncate } from '../utils/format';
import DetailRenderer from './log-details';
import styles from './LogEntryLine.module.css';

function splitToolHeadline(headline: string): { toolLabel: string; rest: string } {
  const match = headline.match(/^([^:\s]+:)(\s.*)?$/);
  if (!match) return { toolLabel: '', rest: headline };
  return {
    toolLabel: match[1],
    rest: (match[2] || '').trimStart(),
  };
}

const EVENT_COLORS: Record<string, string> = {
  shell: 'var(--blue)', thinking: 'var(--text-muted)', tool_call: 'var(--purple)',
  tool_result: 'var(--orange)', text: 'var(--green)', session_end: 'var(--green)',
  code_snapshot: 'var(--blue)', prompt: 'var(--purple)', turn_usage: 'var(--blue)',
};

interface Props { entry: LogEntry; }

export default function LogEntryLine({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);

  let headline = '';
  let hasDetail = false;
  let toolLabel = '';
  let toolRest = '';

  switch (entry.event) {
    case 'shell':
      headline = entry.message || '';
      break;
    case 'thinking': {
      const thinkContent = entry.content || '';
      const thinkTrunc = truncate(thinkContent, 200);
      headline = thinkTrunc.text;
      if (thinkTrunc.truncated || thinkContent.includes('\n')) hasDetail = true;
      break;
    }
    case 'text': {
      const textContent = entry.content || entry.message || '';
      const textTrunc = truncate(textContent, 200);
      headline = textTrunc.text;
      if (textTrunc.truncated || textContent.includes('\n')) hasDetail = true;
      break;
    }
    case 'tool_call': {
      const toolName = entry.tool || '?';
      const inp = (entry.input || {}) as Record<string, unknown>;
      let argSummary = '';
      // Subagent invocations (Task / Agent tool) carry `subagent_type`.
      // Surface that plus the human-readable description so the
      // analogy/challenger/refactor calls are easy to spot in plan.jsonl.
      const subagentType = typeof inp.subagent_type === 'string' ? inp.subagent_type : '';
      if (subagentType) {
        const desc = typeof inp.description === 'string' ? inp.description : '';
        argSummary = desc ? `${subagentType} — ${desc}` : subagentType;
      } else if (inp.command) argSummary = String(inp.command).split('\n')[0].slice(0, 120);
      else if (toolName === 'Edit' && inp.file_path) {
        const fname = String(inp.file_path).split('/').pop() || '';
        const oldStr = String(inp.old_string || '').slice(0, 60).replace(/\n/g, '↵');
        argSummary = `${fname}: ${oldStr}`;
      }
      else if (inp.file_path) argSummary = String(inp.file_path);
      else if (inp.path) argSummary = String(inp.path);
      else if (inp.pattern) argSummary = String(inp.pattern);
      else {
        const firstVal = Object.values(inp).find(v => typeof v === 'string');
        if (firstVal) argSummary = String(firstVal).slice(0, 120);
      }
      // Relabel Task/Agent tool calls so the chip reads like "subagent:"
      // rather than the generic "Task:" — clearer at a glance.
      const displayTool = subagentType ? 'subagent' : toolName;
      headline = argSummary ? `${displayTool}: ${argSummary}` : `${displayTool}:`;
      ({ toolLabel, rest: toolRest } = splitToolHeadline(headline));
      hasDetail = true;
      break;
    }
    case 'tool_result': {
      const content = entry.content || entry.message || '';
      const t = truncate(content, 150);
      headline = t.text;
      if (t.truncated || content.includes('\n')) hasDetail = true;
      break;
    }
    case 'code_snapshot': {
      headline = `📸 Step ${entry.step ?? '?'} · ${entry.file ?? ''} (${entry.tool ?? 'Edit'})`;
      hasDetail = true;
      break;
    }
    case 'prompt': {
      // Headline summarizes the prompt at a glance. The first non-empty
      // line is usually the title/heading of the prompt, so it's the
      // most useful preview. Char count + attempt/resume info live in
      // the detail header.
      const promptText = entry.prompt || '';
      const firstLine = promptText.split('\n').find(l => l.trim().length > 0) || '';
      const charCount = (entry.length ?? promptText.length).toLocaleString();
      const kind = entry.resume_session_id ? 'continuation prompt' : 'initial prompt';
      const attemptTag = entry.attempt && entry.attempt > 1 ? ` · attempt ${entry.attempt}` : '';
      const preview = truncate(firstLine, 120).text;
      headline = `${kind} · ${charCount} chars${attemptTag}${preview ? ` — ${preview}` : ''}`;
      hasDetail = true;
      break;
    }
    case 'session_end': {
      const dur = entry.duration_ms ? `${(entry.duration_ms / 1000).toFixed(0)}s` : '';
      const model = primaryModel(entry.model_usage);
      const parts = ['Session end'];
      if (model) parts.push(model);
      if (dur) parts.push(dur);
      if (entry.num_turns) parts.push(`${entry.num_turns} turns`);
      if (entry.total_cost_usd) parts.push(`$${entry.total_cost_usd.toFixed(2)}`);
      headline = parts.join(' · ');
      if (entry.summary) hasDetail = true;
      break;
    }
    case 'turn_usage': {
      const parts: string[] = [];
      const costVal = entry.total_cost_usd || entry.cost_usd || 0;
      if (entry.input_tokens) parts.push(`${entry.input_tokens.toLocaleString()} in`);
      if (entry.cache_read_input_tokens) parts.push(`${entry.cache_read_input_tokens.toLocaleString()} cached`);
      if (entry.output_tokens) parts.push(`${entry.output_tokens.toLocaleString()} out`);
      if (costVal) parts.push(`$${costVal.toFixed(4)}`);
      headline = parts.join(' · ');
      break;
    }
  }

  return (
    <div className={styles.line}>
      <span className={styles.ts}>{fmtTime(entry.ts)}</span>
      <span className={styles.event} style={{ color: EVENT_COLORS[entry.event] || 'var(--text-muted)' }}>
        {entry.event === 'turn_usage' ? 'turn cost' : entry.event}{entry.level === 'error' ? '!' : entry.level === 'warn' ? '⚠' : ''}
      </span>
      <span
        className={`${styles.text} ${hasDetail ? styles.expandable : ''}`}
        onClick={hasDetail ? () => setExpanded(!expanded) : undefined}
      >
        {entry.event === 'tool_call' && toolLabel ? (
          <>
            <span className={styles.toolName}>{toolLabel}</span>
            {toolRest ? ` ${toolRest}` : ''}
          </>
        ) : (
          headline
        )}
        {hasDetail && <span className={styles.expandHint}>{expanded ? ' ▾' : ' ▸'}</span>}
      </span>
      {expanded && (
        <div className={styles.detail}>
          <DetailRenderer entry={entry} />
        </div>
      )}
    </div>
  );
}
