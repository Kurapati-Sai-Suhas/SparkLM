import { ShieldCheck, FlaskConical } from 'lucide-react';

/**
 * The backend's trust position for a served question (M2 P2.25 / P2.26).
 *
 * ── Why this exists ──────────────────────────────────────────────────────
 *
 * Serving filters on DELIVERABILITY, not trust: a question needs a real
 * statement and a non-empty hidden suite, and that is all. Roughly 1,782 of
 * the questions a learner can currently reach have never had their answer
 * key checked by an oracle. Until now the UI could not tell them apart, so a
 * learner could be marked Wrong Answer by a key nobody had verified with no
 * indication that was even possible.
 *
 * ── The backend is the only classifier ───────────────────────────────────
 *
 * Every field here is READ from `response.trust`, which the backend builds
 * with `Question.trust_summary()`. This component derives nothing: it does
 * not inspect `status`, `trust_state`, the question id, the route or which
 * endpoint answered. A second trust classifier in TypeScript would be a
 * second answer, and the two would eventually disagree.
 *
 * ── The wording is the backend's too ─────────────────────────────────────
 *
 * "Practice mode" is not invented here. `ProgressionService.apply_submission`
 * already returns exactly that phrasing on an ineligible submission:
 * "Practice mode: this problem's answers are not yet verified, so it doesn't
 * change your rating." The badge announces beforehand what the submit
 * response says afterwards, so the two cannot contradict each other.
 *
 * What is actually true for an ineligible submission, verified in the
 * backend rather than assumed: the verdict is still computed, returned and
 * stored; `rating_change` is 0.0 and the rating is unchanged; and mastery
 * and the spaced-repetition half-life are skipped. So "does not count
 * toward your rating or progress" is accurate, and the learner is NOT
 * blocked from solving.
 */

export interface TrustState {
  status?: string;
  trust_state?: string;
  adaptive_eligible?: boolean;
  servable?: boolean;
}

/**
 * Returns null unless the backend positively said the question is servable
 * but not adaptive-eligible.
 *
 * Failing closed on absence is deliberate. A missing, null or malformed
 * `trust` object renders nothing at all — the page keeps working, and the
 * learner is never TOLD a question is verified on the strength of a field
 * that did not arrive. The one thing this must never do is imply trust it
 * has no evidence for.
 */
export default function TrustBadge({ trust }: { trust?: TrustState | null }) {
  if (!trust || typeof trust !== 'object') return null;

  const eligible = trust.adaptive_eligible === true;
  const servable = trust.servable === true;

  // Verified: a quiet, positive confirmation. No alarm, nothing to explain.
  if (eligible) {
    return (
      <span
        data-testid="trust-badge-verified"
        role="status"
        aria-label="Verified problem. Your result counts toward your rating."
        className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-emerald-300 backdrop-blur"
      >
        <ShieldCheck className="h-3 w-3" aria-hidden="true" />
        Verified
      </span>
    );
  }

  // Only claim "practice mode" when the backend positively said servable.
  // Absent that, say nothing rather than guess at a state.
  if (!servable) return null;

  return (
    <span
      data-testid="trust-badge-practice"
      role="status"
      aria-label="Practice mode. This problem's answers are not yet verified, so your result does not count toward your rating or progress."
      className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-amber-300 backdrop-blur"
    >
      <FlaskConical className="h-3 w-3" aria-hidden="true" />
      Practice mode
    </span>
  );
}

/**
 * The one-line explanation that sits under the title.
 *
 * Separate from the badge so the badge can sit inline with difficulty while
 * the sentence gets its own row — and so a caller that only wants one of the
 * two is not forced to take both.
 */
export function TrustNote({ trust }: { trust?: TrustState | null }) {
  if (!trust || typeof trust !== 'object') return null;
  if (trust.adaptive_eligible === true) return null;
  if (trust.servable !== true) return null;

  return (
    <p
      data-testid="trust-note-practice"
      className="text-[11px] leading-relaxed text-amber-200/70"
    >
      This problem&apos;s answers have not been verified yet, so your result
      won&apos;t change your rating or progress. You can still solve it.
    </p>
  );
}
