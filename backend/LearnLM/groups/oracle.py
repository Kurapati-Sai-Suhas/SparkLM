"""
Trusted oracle execution (M2 P2.5, Phase 7).

The only sanctioned source of `expected_output`. An LLM may propose candidate
INPUTS; it must never decide what the answer is. Every stored expected output
must come from executing the canonical `ReferenceSolution` through this
module.

Two properties make it trustworthy rather than merely convenient:

**It uses the learner's own execution path.** `GradingService._build_executable`
is the single source of truth for "what this source actually executes as" —
Java import stripping, the per-question `hidden_wrapper_code`, the generic
reflection harness for python/java/javascript, and the self-contained model
for c/cpp. Wrapping the reference differently would produce outputs that no
learner submission could ever reproduce, which is a subtler version of the bug
this whole phase exists to fix.

**It refuses rather than guesses.** A reference that fails to compile, crashes,
times out or returns different output on two identical runs raises. The caller
BLOCKS the problem; it does not fall back to a stored value, an LLM, or the
first of two disagreeing runs.

Trusted contexts only: seed, validation, reconciliation and generation
commands. Nothing here is imported by a view, and
`test_reference_solution_secrecy` fails if a module both reads `source_code`
and builds an HTTP response.
"""

from groups.models import ReferenceSolution
from groups.services import GradingService
from groups.utils import normalize_output

#: Judge0 status_id 3 is "Accepted" — the process ran to completion. Anything
#: else means the reference itself is broken, not that the answer is unusual.
JUDGE0_ACCEPTED = 3


class OracleError(Exception):
    """Base: the oracle could not produce a trustworthy answer."""


class OracleUnavailable(OracleError):
    """The execution service could not be reached. Transient; retry later."""


class OracleFailed(OracleError):
    """
    The reference solution did not run cleanly — compile error, crash, or
    timeout. The problem must be BLOCKED: a reference that cannot execute
    cannot define the answer, and generating tests from it would bake its
    failure into grading truth.
    """


class OracleNondeterministic(OracleError):
    """
    Two identical runs disagreed. Whatever this problem's answer is, it is not
    a function of its input alone, so no single stored expected_output can be
    correct. Blocking is the only honest response — picking either run would
    make grading a coin flip for every learner.
    """


class OracleUnapproved(OracleError):
    """
    The reference offered is not a canonical, human-approved implementation
    (M2 P2.7d).

    Raised BEFORE any execution. A reference that nobody has read cannot
    define the right answer, however cleanly it runs — a plausible wrong
    implementation executes perfectly and produces a confidently wrong answer
    key. `OracleFailed` catches references that break; this catches references
    that were never authorised, which is the more dangerous case precisely
    because nothing looks broken.
    """


class OracleService:
    """
    Executes a question's canonical reference solution.

    `runner` is the same callable GradingService takes — (source, language,
    stdin) -> Judge0 verdict dict — so the oracle and the grader cannot drift
    apart, and tests can drive both through one stub.
    """

    def __init__(self, runner):
        self._runner = runner

    def run(self, question, reference, stdin, verify_determinism=True):
        """
        The normalized stdout of the reference solution for one input.

        Normalization is the EXISTING `normalize_output` — whitespace and line
        endings only. Deliberately not extended here: a comparator that is
        looser during generation than during grading would mint expected
        outputs that the grader then rejects.

        ── verify_determinism, and the P2.7d/P2.7g boundary ─────────────────

        The default is True and P2.7d does not change that. `False` is
        legitimate for bulk regeneration, where a caller re-runs a reference
        it has already verified and is paying one Judge0 call per input rather
        than two.

        It is NOT legitimate on a trust-promotion path, because a single run
        is not evidence that the output is a function of the input alone.
        Nothing here can enforce that today: no trust-promotion path exists to
        constrain — `trust_state` has no writer, by P2.7c's design. Making
        this method carry a "was determinism verified?" flag in its return
        value would change its signature and break `reconcile_hidden_tests`,
        for a caller that does not yet exist.

        So the enforcement is deferred, deliberately, and stated here as the
        contract P2.7g inherits: THE ORACLE_VERIFIED TRANSITION MUST NOT BE
        REACHABLE FROM A CALL THAT PASSED verify_determinism=False. P2.7d
        pins the default (see test_reference_lifecycle) so that a future
        caller which simply omits the argument is correct by default; a
        caller that passes False explicitly is making a claim P2.7g must
        refuse.
        """
        first = self._execute(question, reference, stdin)
        if not verify_determinism:
            return first

        second = self._execute(question, reference, stdin)
        if first != second:
            raise OracleNondeterministic(
                f"question {question.pk} produced different output on two "
                f"identical runs: {first!r} then {second!r}"
            )
        return first

    def run_many(self, question, reference, stdins, verify_determinism=True):
        """
        (stdin, output) for each input, in order.

        Each input costs one Judge0 call — two with determinism verification —
        and Judge0 is a blocking external call on a single-worker instance, so
        callers are expected to be operator-run commands, never requests.
        """
        return [
            (stdin, self.run(question, reference, stdin, verify_determinism))
            for stdin in stdins
        ]

    # ── internals ────────────────────────────────────────────

    def _execute(self, question, reference, stdin):
        # The lifecycle gate (M2 P2.7d), checked on every execution rather
        # than once per batch: `run_many` loops through here, a reference can
        # stop being canonical part-way through a batch, and a caller that
        # assembled the reference itself never passed `canonical_reference` at
        # all. The service takes two model instances as arguments, so this is
        # the only place that can refuse the pair.
        #
        # OWNERSHIP FIRST. `canonical_reference(question)` reads the related
        # manager and therefore cannot return a foreign row, but this method is
        # public API and does not require that caller. Executing question A's
        # wrapper around question B's approved reference produces a perfectly
        # well-formed answer — which is worse than a crash, not better: it is
        # the confidently-wrong answer key this whole milestone exists to
        # prevent, and nothing about it looks broken.
        if reference.question_id != question.pk:
            raise OracleUnapproved(
                f"reference {reference.pk} belongs to question "
                f"{reference.question_id}, not question {question.pk}; a "
                f"reference may only define the answers of its own problem"
            )

        if not reference.is_canonical:
            raise OracleUnapproved(
                f"reference {reference.pk} for question {question.pk} is not a "
                f"canonical approved implementation "
                f"(review_state={reference.review_state!r}, "
                f"is_active={reference.is_active}, "
                f"provenance_intact={reference.has_valid_approval_provenance})"
            )

        executable, _stored = GradingService._build_executable(
            question, reference.language, reference.source_code
        )
        # The same literal-\n conversion GradingService applies, so an input
        # means the same thing to the oracle as it will to the grader.
        verdict = self._runner(
            executable, reference.language, (stdin or "").replace("\\n", "\n")
        )

        if "error" in verdict:
            raise OracleUnavailable(verdict["error"])

        status_id = verdict.get("status_id")
        if status_id != JUDGE0_ACCEPTED:
            raise OracleFailed(
                f"reference solution for question {question.pk} did not run "
                f"cleanly on {stdin!r}: status={verdict.get('status')!r} "
                f"stderr={(verdict.get('stderr') or '')[:200]!r} "
                f"compile={(verdict.get('compile_output') or '')[:200]!r}"
            )

        return normalize_output(verdict.get("stdout") or "")


def _canonical_candidates(question):
    """
    Rows that are active AND approved AND unmodified since approval
    (M2 P2.7d).

    Selecting on `is_active` alone would be sufficient given the
    `reference_active_requires_approval` database constraint — but the oracle
    must not depend on a constraint declared in another module for a
    correctness property this severe, and the constraint cannot see an
    in-memory instance whose `source_code` was reassigned after loading.
    Checking here costs nothing and removes both assumptions.
    """
    return [s for s in question.reference_solutions.all() if s.is_canonical]


def canonical_reference(question):
    """
    The one canonical reference solution for a question, or None.

    Canonical means approved by a human, currently selected, and byte-identical
    to what was approved. The schema permits one active row per language; the
    CURRENT product contract is exactly one canonical oracle per problem. More
    than one is therefore a configuration error the caller must surface rather
    than resolve by picking — which oracle was chosen would silently determine
    every expected output the problem ever gets.
    """
    candidates = _canonical_candidates(question)
    if len(candidates) == 1:
        return candidates[0]
    return None


def canonical_reference_problem(question):
    """Why `canonical_reference` returned None, for reporting. None if fine."""
    candidates = _canonical_candidates(question)
    if len(candidates) > 1:
        langs = ", ".join(sorted(s.language for s in candidates))
        return (
            f"{len(candidates)} active reference solutions ({langs}); the current "
            f"contract allows exactly one canonical oracle per problem"
        )
    if not candidates:
        # Distinguish "none exists" from "one exists but is not usable as
        # grading truth" — they need completely different operator responses,
        # and reporting both as "no active reference solution" is how a
        # pending review looks identical to missing work.
        unusable = [
            s for s in question.reference_solutions.all()
            if s.is_active and not s.has_valid_approval_provenance
        ]
        if unusable:
            return (
                f"{len(unusable)} active reference solution(s) are not usable "
                f"as grading truth: "
                + "; ".join(
                    f"{s.language} is {s.review_state}" if
                    s.review_state != ReferenceSolution.REVIEW_APPROVED
                    else f"{s.language} was modified after approval"
                    for s in unusable
                )
            )
        pending = [
            s for s in question.reference_solutions.all()
            if not s.is_active
            and s.review_state != ReferenceSolution.REVIEW_REJECTED
        ]
        if pending:
            return (
                f"no ACTIVE reference solution; {len(pending)} exist(s) but "
                f"none is activated ("
                + ", ".join(f"{s.language}={s.review_state}" for s in pending)
                + ")"
            )
        return "no active reference solution"
    return None
