/**
 * PromptDetail — renders the initial prompt sent to claude.
 *
 * The full text is shown as preformatted content (whitespace preserved)
 * because the prompt is itself structured markdown and rendering it as
 * markdown would conflate "the prompt the agent received" with "a
 * rendered document". Keeping it preformatted makes the actual characters
 * sent to claude unambiguous — exactly what the user wants when checking
 * whether a USER_HINTS directive made it into the prompt.
 *
 * For long prompts the body is scroll-clipped via the shared
 * ``.thinkingBlock`` max-height treatment; nothing is truncated at the
 * data layer.
 */
import styles from './details.module.css';

interface Props {
  prompt: string;
  length?: number;
  attempt?: number;
  resumeSessionId?: string;
}

export default function PromptDetail({ prompt, length, attempt, resumeSessionId }: Props) {
  const charCount = length ?? prompt.length;
  const isContinuation = Boolean(resumeSessionId);
  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <span className={styles.icon}>📥</span>
        <span className={styles.label}>
          {isContinuation ? 'Continuation prompt' : 'Initial prompt'}
        </span>
        {attempt && attempt > 1 ? (
          <span className={styles.path}>attempt {attempt}</span>
        ) : null}
        {isContinuation ? (
          <span className={styles.path}>resumed {resumeSessionId?.slice(0, 8)}…</span>
        ) : null}
        <span className={styles.meta}>{charCount.toLocaleString()} chars</span>
      </div>
      <pre className={styles.promptBlock}>{prompt}</pre>
    </div>
  );
}
