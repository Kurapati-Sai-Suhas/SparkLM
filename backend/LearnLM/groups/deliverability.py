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

── The escape condition (Phase 1 M17.1) ────────────────────────────────────

M17's rule reads a structure only where the Python starter DECLARES one. The
binding census found 44 tree and list problems it could not see, and served
them: q617 is `mergeTrees`, yet `blocker(q617)` was None. They fall in two
shapes, and a question is now also undeliverable (cause
`STRUCTURAL_UNDECLARED`) when either holds:

  * UNPARSEABLE — the Python starter does not parse, and the question's own
    starters name a structural type: the Python text, or another language's
    signature. No contract can read a declaration it cannot parse.

  * UNDECLARED PARAMETER — the graded method has an unannotated parameter
    whose name is the bank's convention for a structure
    (`structural_types.CONVENTIONAL_PARAMETERS`: `root`, `head`, `list1`, ...).
    No contract builds a structure for an undeclared parameter — v2 included,
    which builds from the declared kinds — so the method receives the stored
    text.

Evidence is the question's own signature and starters, never its title or
topic. The rule catches all 44 the census listed and 16 more of the same
shape that the census could not: they were never refused, because v1 happily
binds an unannotated parameter to the raw text (q24 `swapPairs(head)`, q965
`isUnivalTree(root)`). Deliberately NOT covered: design classes with no
`Solution`, and a structure only an unannotated RETURN carries — neither has
evidence this rule can tie to the graded signature.

Unlike the declared case, a migration alone does not bring these back: the
starter must first declare the structure (`remediate_boilerplate`), and then
M17's own rule takes over.
"""

import ast
import functools
import operator
import re

from django.db.models import BooleanField, ExpressionWrapper, Q
from django.db.models.fields.json import KT

from common import languages
from groups import execution_adapter, execution_contract, language_readiness
from groups import structural_types

#: The readiness causes that mean "no learner code can pass this".
#:
#: Deliberately only the structural pair. UNPARSEABLE, NO_SOLUTION_CLASS and
#: UNDEFINED_ANNOTATION are real starter defects, but the learner replaces the
#: starter — the harness never executes it — so they are not proof the
#: question cannot be passed. The structural pair is: the HARNESS, not the
#: learner, decides what the method receives.
BLOCKING_CAUSES = language_readiness.STRUCTURAL_CAUSES

#: A structure the question carries but its Python starter does not declare
#: (M17.1). Serving's own cause: `language_readiness` still reports these
#: starters as it always has, so the census buckets and migration tooling
#: keep their meaning.
STRUCTURAL_UNDECLARED = "structural_undeclared"

#: Every structural name the rule can fire on. Read from the readiness module
#: rather than listed, so registering a structure widens the prefilter too.
STRUCTURAL_NAMES = tuple(sorted(language_readiness.STRUCTURAL_TYPES))

#: The same names as whole words — in Python, and in Postgres (`\y`).
_NAMED = re.compile(r"\b(" + "|".join(STRUCTURAL_NAMES) + r")\b")
_NAMED_SQL = r"\y(" + "|".join(STRUCTURAL_NAMES) + r")\y"

#: The language whose starter declares the contract of record.
_CONTRACT_LANGUAGE = "python"

#: Every key another language's starter may be stored under.
_OTHER_SPELLINGS = tuple(
    spelling for lang in languages.REGISTRY if lang.key != _CONTRACT_LANGUAGE
    for spelling in languages.wrapper_spellings(lang.key))


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


def _named_elsewhere(question):
    """Whether another language's starter names a structural type."""
    stored = question.boilerplate_code or {}
    return any(isinstance(stored.get(spelling), str)
               and _NAMED.search(stored[spelling]) is not None
               for spelling in _OTHER_SPELLINGS)


def blocker(question):
    """
    The readiness verdict proving `question` undeliverable, or None.

    Reads only the starters and the declared contract — the inputs the rule
    is about — so it can be asked of a row or of a lightweight stand-in.
    """
    source = language_readiness.boilerplate_for(question, _CONTRACT_LANGUAGE)
    return _verdict(source or "", _declared_version(question),
                    _named_elsewhere(question))


@functools.lru_cache(maxsize=4096)
def _verdict(source, version, named_elsewhere):
    """Pure: the same inputs always yield the same verdict."""
    verdict = language_readiness.assess_source(source, _CONTRACT_LANGUAGE,
                                               version)
    if verdict.cause in BLOCKING_CAUSES:
        return verdict
    return _undeclared_structure(source, verdict, named_elsewhere)


def _refusal(reason):
    return language_readiness.Readiness(
        _CONTRACT_LANGUAGE, language_readiness.NOT_READY, reason,
        STRUCTURAL_UNDECLARED)


def _undeclared_structure(source, verdict, named_elsewhere):
    """The M17.1 escape condition, or None. See the module docstring."""
    try:
        ast.parse(source)
    except SyntaxError:
        # Parsed directly rather than read from `verdict`: readiness reports a
        # missing `Solution` before it parses, which would hide exactly these.
        named = sorted(set(_NAMED.findall(source)))
        if not (named or named_elsewhere):
            return None
        evidence = (f"its text names {', '.join(named)}" if named
                    else "another language's starter declares one")
        return _refusal(
            f"the Python starter does not parse, so no contract can read the "
            f"structure this question carries ({evidence}); the method would "
            f"receive the stored text")

    if verdict.cause == language_readiness.NO_SOLUTION_CLASS:
        return None                  # a design problem, not an escaped signature
    node, is_method = execution_adapter.chosen_function(source)
    if node is None or not is_method:
        return None
    for argument in list(node.args.posonlyargs) + list(node.args.args):
        if argument.annotation is not None or argument.arg in ("self", "cls"):
            continue
        kind = structural_types.by_parameter_name(argument.arg)
        if kind is not None:
            return _refusal(
                f"parameter {argument.arg!r} is unannotated, but its name is "
                f"this bank's convention for a {kind.replace('_', ' ')}; no "
                f"contract builds a structure for an undeclared parameter, so "
                f"it would receive the stored text")
    return None


class _Row:
    """The two fields `_declared_version` reads, without a model."""

    __slots__ = ("pk", "execution_contract_version")

    def __init__(self, pk, version):
        self.pk = pk
        self.execution_contract_version = version


def undeliverable_ids(queryset):
    """
    The primary keys in `queryset` that `blocker` refuses. One query.

    Narrowed in SQL to rows the rule CAN fire on: a Python starter that
    mentions a structural name or a conventional structural parameter name,
    or another language's starter that names a structural type. Each is a
    necessary condition of one branch of the rule, read from the same text
    the rule reads, so nothing the rule would refuse is filtered out.

    Evaluated immediately, so the caller's queryset stays a plain queryset.
    """
    others = {f"_other_{index}": KT(f"boilerplate_code__{spelling}")
              for index, spelling in enumerate(_OTHER_SPELLINGS)}
    named_elsewhere = functools.reduce(
        operator.or_, (Q(**{f"{alias}__regex": _NAMED_SQL}) for alias in others))
    mentions = functools.reduce(
        operator.or_,
        [Q(_contract_starter__contains=name) for name in STRUCTURAL_NAMES]
        + [Q(_contract_starter__icontains=name)
           for name in structural_types.CONVENTIONAL_PARAMETERS]
        + [named_elsewhere])
    rows = (queryset
            .annotate(_contract_starter=KT(f"boilerplate_code__{_CONTRACT_LANGUAGE}"),
                      **others)
            .annotate(_named_elsewhere=ExpressionWrapper(
                named_elsewhere, output_field=BooleanField()))
            .filter(mentions)
            .values_list("pk", "execution_contract_version",
                         "_contract_starter", "_named_elsewhere"))
    return [pk for pk, version, source, elsewhere in rows
            if _verdict(source or "", _declared_version(_Row(pk, version)),
                        bool(elsewhere)) is not None]
