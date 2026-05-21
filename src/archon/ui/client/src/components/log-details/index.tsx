/**
 * DetailRenderer — dispatches to the right detail component based on event type + tool
 *
 * Used by LogEntryLine when a log entry is expanded.
 */
import type { LogEntry } from '../../types';
import EditDetail from './EditDetail';
import BashDetail from './BashDetail';
import SnapshotDetail from './SnapshotDetail';
import ToolResultDetail from './ToolResultDetail';
import ThinkingDetail from './ThinkingDetail';
import JsonDetail from './JsonDetail';
import PromptDetail from './PromptDetail';

interface Props {
  entry: LogEntry;
}

export default function DetailRenderer({ entry }: Props) {
  switch (entry.event) {
    case 'tool_call': {
      const tool = entry.tool || '';
      const input = (entry.input || {}) as Record<string, unknown>;

      if (tool === 'Edit' || tool === 'Write') {
        return <EditDetail input={input} tool={tool} />;
      }
      if (tool === 'Bash' || tool === 'bash') {
        return <BashDetail input={input} />;
      }
      // Other tools: show as collapsible JSON
      return <JsonDetail data={input} />;
    }

    case 'tool_result': {
      const content = entry.content || entry.message || '';
      return <ToolResultDetail content={content} structuredJson />;
    }

    case 'shell': {
      const content = entry.content || entry.message || '';
      return <ToolResultDetail content={content} structuredJson={false} />;
    }

    case 'thinking':
    case 'text': {
      const content = entry.content || entry.message || '';
      return <ThinkingDetail content={content} event={entry.event} />;
    }

    case 'code_snapshot': {
      return <SnapshotDetail entry={entry} />;
    }

    case 'prompt': {
      const promptText = entry.prompt || '';
      return (
        <PromptDetail
          prompt={promptText}
          length={entry.length}
          attempt={entry.attempt}
          resumeSessionId={entry.resume_session_id}
        />
      );
    }

    case 'session_end': {
      if (entry.summary) {
        return <ThinkingDetail content={entry.summary} event="text" />;
      }
      return null;
    }

    default:
      return null;
  }
}
