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


def canonical_reference(question):
    """
    The one active reference solution for a question, or None.

    The schema permits one active row per language; the CURRENT product
    contract is exactly one canonical oracle per problem. More than one active
    row is therefore a configuration error the caller must surface rather than
    resolve by picking — which oracle was chosen would silently determine
    every expected output the problem ever gets.
    """
    active = [s for s in question.reference_solutions.all() if s.is_active]
    if len(active) == 1:
        return active[0]
    return None


def canonical_reference_problem(question):
    """Why `canonical_reference` returned None, for reporting. None if fine."""
    active = [s for s in question.reference_solutions.all() if s.is_active]
    if not active:
        return "no active reference solution"
    if len(active) > 1:
        langs = ", ".join(sorted(s.language for s in active))
        return (
            f"{len(active)} active reference solutions ({langs}); the current "
            f"contract allows exactly one canonical oracle per problem"
        )
    return None
