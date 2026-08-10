/**
 * Question.content is split across two real formats in the seeded bank:
 * ~1,508 rows are real HTML ("<p>...</p>", from the current generation
 * prompt), ~1,401 rows are plain text with literal newline characters
 * (seeded before that prompt, or where the LLM didn't comply). Blindly
 * rendering both via dangerouslySetInnerHTML collapses the plaintext
 * rows' newlines (HTML ignores raw \n outside <pre>), producing one
 * unbroken wall of text — that is the "not well formatted" bug.
 *
 * This component detects which format it received and renders each
 * correctly, so already-seeded content displays properly with zero
 * backend changes or content regeneration required.
 */
const LOOKS_LIKE_HTML = /<[a-z][\s\S]*>/i;

export default function ProblemDescription({
  content,
  htmlClassName,
  textClassName,
  style,
}: {
  content: string;
  htmlClassName?: string;
  textClassName?: string;
  /** Inline style applied in both branches — for callers that style via
   *  inline objects rather than Tailwind classes. The legacy /code portal
   *  was the original such caller and was retired in M1/P1.2-C. Merged with the plaintext-branch's required
   *  whiteSpace: 'pre-wrap' (never overridden, or newlines collapse again). */
  style?: React.CSSProperties;
}) {
  if (!content) return null;

  if (LOOKS_LIKE_HTML.test(content)) {
    return <div className={htmlClassName} style={style} dangerouslySetInnerHTML={{ __html: content }} />;
  }

  return (
    <div className={textClassName ?? htmlClassName} style={{ ...style, whiteSpace: 'pre-wrap' }}>
      {content}
    </div>
  );
}
