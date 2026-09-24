"""
Questions no learner can pass, whatever they write (Phase 1 M17).

READ-ONLY. Nothing here writes.

── The defect ──────────────────────────────────────────────────────────────

A question whose signature declares a structure — `TreeNode`, `ListNode`,
`Node`, `ImmutableListNode` — is only gradable under a contract that BUILDS
that structure. Only v2 builds any, and only `TreeNode` and `ListNode`. Under
v1 the harness hands the method the raw stdin line: `isSameTree` receives the
string `"[1,2,3]"` where it expects a node, and a correct solution crashes or
answers wrong and is graded Wrong Answer. The M14 q100 audit executed exactly
that. Serving such a question is not practice; it is a guaranteed false fail.

`_servable_questions()` excluded only placeholder statements and empty suites,
so 127 of these were in the served bank — q100 among them.

── The rule ───────────────────────────────────────────────────────────────

A question is undeliverable when its PYTHON readiness, under the question's
own declared contract, is `STRUCTURAL_TYPE` or `STRUCTURAL_UNSUPPORTED`.

  * One classifier, not a second one. The verdict is
    `language_readiness.assess_source`, the function every readiness report
    and the trusted-content worklist already use. A serving rule with its own
    idea of "structural" is how two paths disagree.

  * The Python starter is the declared contract of record. The signature
    grading, the migration classifier and the reference are all read from it;
    the other languages' starters are translations of it.

  * NOT a status filter and NOT a trust filter. A deliverable DRAFT question
    stays servable as practice, exactly as before; PUBLISHED questions are
    judged by the same rule and none of the six is structural. Adaptive
    eligibility, Elo and mastery are not read or changed here.

  * A migrated question returns on its own. Under v2 a `TreeNode` signature is
    READY, so the rule stops firing the moment `migrate_contract_v2` lands —
    no second switch to remember.

── Why it is computed live, and why that is affordable ────────────────────

Nothing is cached across requests. A cached ID set would go stale the moment
an operator migrated a question or edited a starter, and a stale quarantine
fails in the unsafe direction for new content. Instead:

  1. SQL narrows the bank to rows whose Python starter MENTIONS a structural
     name. The rule can only fire on an annotation naming one, and annotations
     are read from that text, so a starter mentioning none cannot be blocked.
     ~165 of 1,788 rows, one query.
  2. The AST decides for those rows. The verdict is a pure function of
     (source, version), so it is memoised on exactly that key — a changed
     starter or contract is a different key, never a stale answer.

The prefilter's one blind spot is a name assembled so that no structural name
appears in the text at all — a string escape inside a quoted forward
reference (`"Tree\\x4eode"`). No row in the bank does that (checked across all
2,926 in M17), and the test suite pins the forms that do occur.
"""

import functools
import operator

from django.db.models import Q
from django.db.models.fields.json import KT

from groups import execution_contract, language_readiness

#: The readiness causes that mean "no learner code can pass this".
#:
#: Deliberately only the structural pair. UNPARSEABLE, NO_SOLUTION_CLASS and
#: UNDEFINED_ANNOTATION are real starter defects, but the learner replaces the
#: starter — the harness never executes it — so they are not proof the
#: question cannot be passed. The structural pair is: the HARNESS, not the
#: learner, decides what the method receives.
BLOCKING_CAUSES = language_readiness.STRUCTURAL_CAUSES

#: Every structural name the rule can fire on. Read from the readiness module
#: rather than listed, so registering a structure widens the prefilter too.
STRUCTURAL_NAMES = tuple(sorted(language_readiness.STRUCTURAL_TYPES))

#: The language whose starter declares the contract of record.
_CONTRACT_LANGUAGE = "python"


@functools.lru_cache(maxsize=4096)
def _python_verdict(source, version):
    """Pure: the same (source, version) always yields the same verdict."""
    return language_readiness.assess_source(source, _CONTRACT_LANGUAGE, version)


def _declared_version(question):
    """
    The contract the question declares, without raising.

    A version no harness knows is passed through as written: it is not v2, so
    a structural signature under it is refused exactly like v1, and a
    non-structural one is left to the grader, which already refuses it. The
    serving rule must not turn one bad row into a 500 for every learner.
    """
    try:
        return execution_contract.contract_version(question)
    except execution_contract.UnknownExecutionContract:
        return question.execution_contract_version


def blocker(question):
    """
    The readiness verdict proving `question` undeliverable, or None.

    Reads only the Python starter and the declared contract — the two inputs
    the rule is about — so it can be asked of a row, or of a lightweight
    stand-in carrying just those two fields.
    """
    source = language_readiness.boilerplate_for(question, _CONTRACT_LANGUAGE)
    verdict = _python_verdict(source or "", _declared_version(question))
    return verdict if verdict.cause in BLOCKING_CAUSES else None


class _Row:
    """The two fields `blocker` reads, without instantiating a model."""

    __slots__ = ("pk", "execution_contract_version", "boilerplate_code")

    def __init__(self, pk, version, source):
        self.pk = pk
        self.execution_contract_version = version
        self.boilerplate_code = {_CONTRACT_LANGUAGE: source}


def undeliverable_ids(queryset):
    """
    The primary keys in `queryset` that `blocker` refuses. One query.

    Evaluated immediately, so the caller's queryset stays a plain queryset.
    """
    mentions = functools.reduce(
        operator.or_,
        (Q(_contract_starter__contains=name) for name in STRUCTURAL_NAMES))
    rows = (queryset
            .annotate(_contract_starter=KT(f"boilerplate_code__{_CONTRACT_LANGUAGE}"))
            .filter(mentions)
            .values_list("pk", "execution_contract_version", "_contract_starter"))
    return [pk for pk, version, source in rows
            if blocker(_Row(pk, version, source)) is not None]
