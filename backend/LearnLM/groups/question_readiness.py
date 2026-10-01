"""
Canonical question readiness (QP1).

ONE read-only answer to "how ready is this question for the SparkLM pipeline?"
For any question it reports the derived pipeline stage, the blockers that stop
the next stage, exactly one actionable category (A-I), the operation that
category allows, per-language readiness and adaptive eligibility.

── Derived, never stored ───────────────────────────────────────────────────

The stages below are a READING of facts that already live elsewhere. The only
stored lifecycle is `Question.status` x `Question.trust_state` x
`verified_language`; this module does not add a field, a table or a second
trust system. A stage is reached when every stage before it holds, so the
report can never claim more than the evidence shows.

── Every rule is borrowed ──────────────────────────────────────────────────

    servable                  coding_views._servable_questions()
    structural deliverability deliverability.blocker
    per-language readiness    language_readiness.assess
    case admission            suite_admission.check_case (and its _answer_form)
    suite well-formedness     hidden_tests.validate_suite / is_gradable
    v2 structural path        migration_readiness.classify
    v1 input repair           input_repair.plan
    quarantine                content_quarantine.is_quarantined
    quality verdict           question_artifact.QualityOutcome
    adaptive eligibility      Question.is_adaptive_eligible / adaptive_eligible_for

What this module adds is only the ORDER in which those verdicts are read: the
stage sequence and the category precedence. Nothing here writes, executes code
or calls Judge0.
"""

import ast
import types
from dataclasses import dataclass, field

from common import languages
from groups import (content_quarantine, deliverability, hidden_tests,
                    input_repair, language_readiness, migration_readiness,
                    suite_admission)

SCHEMA_VERSION = 1

# ── stages ────────────────────────────────────────────────────────────────

UNAUTHORED = "UNAUTHORED"
AUTHORED = "AUTHORED"
STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
CHECKABLE = "CHECKABLE"
REFERENCE_PRESENT = "REFERENCE_PRESENT"
ORACLE_EVIDENCE = "ORACLE_EVIDENCE"
QUALITY_PASSED = "QUALITY_PASSED"
HUMAN_APPROVED = "HUMAN_APPROVED"
ORACLE_VERIFIED = "ORACLE_VERIFIED"
PUBLISHED = "PUBLISHED"
ADAPTIVE_ELIGIBLE = "ADAPTIVE_ELIGIBLE"

#: In pipeline order. A question's stage is the LAST one whose condition holds
#: together with every condition before it.
STAGES = (UNAUTHORED, AUTHORED, STRUCTURALLY_VALID, CHECKABLE,
          REFERENCE_PRESENT, ORACLE_EVIDENCE, QUALITY_PASSED, HUMAN_APPROVED,
          ORACLE_VERIFIED, PUBLISHED, ADAPTIVE_ELIGIBLE)

# ── blocker reason codes ──────────────────────────────────────────────────

NOT_AUTHORED_PLACEHOLDER = "NOT_AUTHORED_PLACEHOLDER"
NO_HIDDEN_TESTS = "NO_HIDDEN_TESTS"
STRUCTURAL_UNDELIVERABLE = "STRUCTURAL_UNDELIVERABLE"
NO_SOLUTION_CLASS = "NO_SOLUTION_CLASS"
PYTHON_NOT_READY = "PYTHON_NOT_READY"
NO_GRADED_METHOD = "NO_GRADED_METHOD"
UNANNOTATED_PARAMETER = "UNANNOTATED_PARAMETER"
IN_PLACE_RETURN = "IN_PLACE_RETURN"
OWN_WRAPPER = "OWN_WRAPPER"
MALFORMED_SUITE = "MALFORMED_SUITE"
DUPLICATE_CASES = "DUPLICATE_CASES"
BELOW_MIN_TESTS = "BELOW_MIN_TESTS"
PYTHON_ANSWER_FORM_REFUSED = "PYTHON_ANSWER_FORM_REFUSED"
PYTHON_INPUT_REFUSED = "PYTHON_INPUT_REFUSED"
LANGUAGE_BINDING_REFUSED = "LANGUAGE_BINDING_REFUSED"
NO_APPROVED_REFERENCE = "NO_APPROVED_REFERENCE"
NO_ORACLE_EVIDENCE = "NO_ORACLE_EVIDENCE"
NO_PASSING_QUALITY_APPROVAL = "NO_PASSING_QUALITY_APPROVAL"
NOT_ORACLE_VERIFIED = "NOT_ORACLE_VERIFIED"
NOT_PUBLISHED = "NOT_PUBLISHED"
NOT_ADAPTIVE_ELIGIBLE = "NOT_ADAPTIVE_ELIGIBLE"
QUARANTINED = "QUARANTINED"
TRUST_INVARIANT_BROKEN = "TRUST_INVARIANT_BROKEN"

BLOCKER_CODES = (
    NOT_AUTHORED_PLACEHOLDER, NO_HIDDEN_TESTS, STRUCTURAL_UNDELIVERABLE,
    NO_SOLUTION_CLASS, PYTHON_NOT_READY, NO_GRADED_METHOD,
    UNANNOTATED_PARAMETER, IN_PLACE_RETURN, OWN_WRAPPER, MALFORMED_SUITE,
    DUPLICATE_CASES, BELOW_MIN_TESTS, PYTHON_ANSWER_FORM_REFUSED,
    PYTHON_INPUT_REFUSED, LANGUAGE_BINDING_REFUSED, NO_APPROVED_REFERENCE,
    NO_ORACLE_EVIDENCE, NO_PASSING_QUALITY_APPROVAL, NOT_ORACLE_VERIFIED,
    NOT_PUBLISHED, NOT_ADAPTIVE_ELIGIBLE, QUARANTINED, TRUST_INVARIANT_BROKEN)

# ── categories ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Category:
    letter: str
    name: str
    allowed_operation: str
    #: The furthest stage the allowed operation can carry a question to.
    #: Everything beyond it needs the next category's work.
    max_stage: str


CATEGORIES = {c.letter: c for c in (
    Category("A", "SAFE_AS_IS",
             "none; re-verify after any change to the question",
             ADAPTIVE_ELIGIBLE),
    Category("B", "SAFE_AUTOMATED_REPAIR",
             "proven transformation class through pre-image, plan digest, "
             "all-or-none apply and post-check (QP2)",
             CHECKABLE),
    Category("C", "HUMAN_REVIEW",
             "operator-authored change or decision through an existing "
             "command; never automated",
             ADAPTIVE_ELIGIBLE),
    Category("D", "REFERENCE_REQUIRED",
             "operator-authored reference, then oracle_execute, suite "
             "expansion, quality_gate, question_approve, question_promote, "
             "question_status",
             ADAPTIVE_ELIGIBLE),
    Category("E", "CONTRACT_REPAIR",
             "remediate_boilerplate (annotations), declare_signature, or "
             "remediate_contract (contract version; a product decision)",
             CHECKABLE),
    Category("F", "LANGUAGE_REPAIR",
             "repair the served language's starter or contract, or withdraw "
             "that language; Python trust is unaffected",
             CHECKABLE),
    Category("G", "STRUCTURAL_REPAIR",
             "migrate_contract_v2 (reviewed) once its blockers are cleared",
             CHECKABLE),
    Category("H", "QUARANTINE",
             "withhold; no repair exists until a new contract or a human "
             "resolution does",
             AUTHORED),
    Category("I", "DESIGN_FLAWED",
             "remove from serving, or redesign as an authoring task",
             AUTHORED),
)}

#: v2 migration verdicts that a reviewed migration can resolve.
_V2_REPAIRABLE = frozenset({
    migration_readiness.SAFE_TO_MIGRATE,
    migration_readiness.BLOCKED_BY_STARTER,
    migration_readiness.BLOCKED_BY_TEST_CONTRACT_INPUT,
    migration_readiness.BLOCKED_BY_TEST_CONTRACT_ARITY,
    migration_readiness.BLOCKED_BY_TEST_CONTRACT_OUTPUT,
})

#: Reflection languages whose harness binds stdin; the self-contained ones
#: (C, C++) read stdin themselves and have nothing to bind.
_REFLECTION = tuple(lang.key for lang in languages.REGISTRY
                    if not lang.self_contained)
_CONTRACT_LANGUAGE = suite_admission.CONTRACT_LANGUAGE

# ── the bank context ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Approval:
    quality_passed: bool
    promoted: bool


@dataclass
class BankContext:
    """
    Everything `assess` needs that lives in OTHER rows, read once.

    A dataclass rather than a loader so tests can supply facts directly and
    the engine stays free of queries. `load` is the one place that reads.
    """

    servable_ids: frozenset = frozenset()
    #: question id -> languages with an APPROVED and active reference
    references: dict = field(default_factory=dict)
    #: question id -> number of SUCCESS oracle executions
    oracle_success: dict = field(default_factory=dict)
    #: question id -> [Approval, ...]
    approvals: dict = field(default_factory=dict)

    @classmethod
    def load(cls, question_ids=None):
        """
        A handful of reads for any number of questions. Never writes.

        Through the default alias only, because `_servable_questions()` and
        `deliverability.undeliverable_ids` read through it: serving another
        alias's facts beside the default's servable set would mix two
        databases in one report.
        """
        from django.db.models import Count

        from groups.coding_views import _servable_questions
        from groups.models import (OracleExecution, QuestionApproval,
                                   ReferenceSolution)
        from groups.question_artifact import QualityOutcome

        def scoped(queryset, field_name):
            if question_ids is None:
                return queryset
            return queryset.filter(**{f"{field_name}__in": question_ids})

        servable_ids = frozenset(scoped(_servable_questions(), "pk")
                                 .values_list("pk", flat=True))

        references = {}
        for qid, language in scoped(
                ReferenceSolution.objects.filter(
                    review_state=ReferenceSolution.REVIEW_APPROVED,
                    is_active=True), "question_id").values_list(
                        "question_id", "language"):
            references.setdefault(qid, []).append(language)

        oracle_success = dict(
            scoped(OracleExecution.objects.filter(
                status=OracleExecution.STATUS_SUCCESS), "question_id")
            .values("question_id").annotate(n=Count("pk"))
            .values_list("question_id", "n"))

        approvals = {}
        for qid, outcome, promoted_at in scoped(
                QuestionApproval.objects, "question_id").values_list(
                    "question_id", "quality_outcome", "promoted_at"):
            approvals.setdefault(qid, []).append(Approval(
                quality_passed=QualityOutcome.from_mapping(outcome or {}).passed,
                promoted=promoted_at is not None))

        return cls(servable_ids=servable_ids,
                   references={k: tuple(sorted(v)) for k, v in references.items()},
                   oracle_success=oracle_success, approvals=approvals)


# ── the report ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Blocker:
    code: str
    #: The stage this blocker stops the question from reaching.
    stage: str
    detail: str = ""

    def as_dict(self):
        return {"code": self.code, "stage": self.stage, "detail": self.detail}


@dataclass(frozen=True)
class LanguageReadiness:
    language: str
    verdict: str
    cause: str
    served: bool
    self_contained: bool
    #: Cases this language's harness refuses, or None when not applicable.
    refused_cases: object
    adaptive_eligible: bool

    def as_dict(self):
        return {"verdict": self.verdict, "cause": self.cause,
                "served": self.served, "self_contained": self.self_contained,
                "refused_cases": self.refused_cases,
                "adaptive_eligible": self.adaptive_eligible}


@dataclass(frozen=True)
class ReadinessReport:
    question_id: int
    topic: object
    stage: str
    category: str
    reason: str
    allowed_operation: str
    blockers: tuple
    status: str
    trust_state: str
    verified_language: object
    contract_version: str
    servable: bool
    case_count: int
    language_readiness: tuple
    is_adaptive_eligible: bool
    #: Trust artifacts the question already holds, whatever its stage. A
    #: reference recorded against inputs that do not bind is still recorded.
    evidence: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def as_dict(self):
        return {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "topic": self.topic,
            "stage": self.stage,
            "category": self.category,
            "reason": self.reason,
            "allowed_operation": self.allowed_operation,
            "blockers": [b.as_dict() for b in self.blockers],
            "status": self.status,
            "trust_state": self.trust_state,
            "verified_language": self.verified_language,
            "contract_version": self.contract_version,
            "servable": self.servable,
            "case_count": self.case_count,
            "language_readiness": {lr.language: lr.as_dict()
                                   for lr in self.language_readiness},
            "evidence": dict(self.evidence),
            "adaptive_eligibility": {
                "question": self.is_adaptive_eligible,
                **{lr.language: lr.adaptive_eligible
                   for lr in self.language_readiness}},
        }


# ── facts, each read through its owner ────────────────────────────────────

@dataclass
class _Facts:
    authored_problems: list
    servable: bool
    undeliverable_cause: object
    python_readiness: object
    method: object
    unannotated: list
    returns_none: bool
    own_wrappers: list
    suite_problems: list
    gradable: bool
    duplicates: int
    below_floor: bool
    refusals: dict            # language -> [(case, LanguageCheck)] refused only
    answer_form_refusals: int
    tf_respell: bool
    verdicts: dict            # language -> language_readiness.Readiness

    def served_refusals(self):
        """Non-contract reflection languages that are served AND mis-bind."""
        return [lang for lang in _REFLECTION
                if lang != _CONTRACT_LANGUAGE and self.refusals[lang]
                and self.verdicts[lang].ready]


def _authored_problems(question):
    """The two content conditions `_servable_questions()` applies first."""
    from groups.models import Question

    problems = []
    if Question.PLACEHOLDER_MARKER.lower() in (question.content or "").lower():
        problems.append(Blocker(NOT_AUTHORED_PLACEHOLDER, AUTHORED,
                                "content carries the placeholder marker"))
    cases = question.hidden_test_cases
    if not isinstance(cases, list) or not cases:
        problems.append(Blocker(NO_HIDDEN_TESTS, AUTHORED,
                                "no hidden test cases"))
    return problems


def _facts(question, context):
    cases = question.hidden_test_cases if isinstance(
        question.hidden_test_cases, list) else []
    source = language_readiness.boilerplate_for(
        question, _CONTRACT_LANGUAGE) or ""
    blocker = deliverability.blocker(question)
    node, _problem = suite_admission._graded_method(source)
    parameters = ([a for a in node.args.args if a.arg not in ("self", "cls")]
                  if node is not None else [])

    from groups.services import wrapper_for

    suite_problems = hidden_tests.validate_suite(cases)
    verdicts = {lang.key: language_readiness.assess(question, lang.key)
                for lang in languages.REGISTRY}
    facts = _Facts(
        authored_problems=_authored_problems(question),
        servable=question.pk in context.servable_ids,
        undeliverable_cause=getattr(blocker, "cause", None) if blocker else None,
        python_readiness=verdicts[_CONTRACT_LANGUAGE],
        method=node,
        unannotated=[a.arg for a in parameters if a.annotation is None],
        returns_none=bool(node is not None and node.returns is not None
                          and ast.unparse(node.returns) == "None"),
        own_wrappers=[lang.key for lang in languages.REGISTRY
                      if wrapper_for(question, lang.key) is not None],
        suite_problems=[p for p in suite_problems if p.index is not None
                        and not p.message.startswith("duplicate")],
        gradable=hidden_tests.is_gradable(cases),
        duplicates=sum(1 for p in suite_problems
                       if p.message.startswith("duplicate")),
        below_floor=len(cases) < hidden_tests.MIN_HIDDEN_TESTS,
        refusals={lang: [] for lang in _REFLECTION},
        answer_form_refusals=0,
        tf_respell=False,
        verdicts=verdicts,
    )
    if facts.authored_problems:
        return facts

    for case in cases:
        if not isinstance(case, dict):
            continue
        for check in suite_admission.check_case(question, case):
            if check.language in facts.refusals and check.refused:
                facts.refusals[check.language].append((case, check))

    python_refused = facts.refusals[_CONTRACT_LANGUAGE]
    if node is not None:
        # `_answer_form` reads a TEXT answer; `check_case` refuses a non-text
        # one before ever asking it, so neither may this.
        answer_form = [(case, check) for case, check in python_refused
                       if isinstance(case.get("expected_output"), str)
                       and check.detail == suite_admission._answer_form(
                           node, case.get("expected_output"))]
        facts.answer_form_refusals = len(answer_form)
        declared = ast.unparse(node.returns) if node.returns is not None else ""
        facts.tf_respell = bool(
            python_refused and len(answer_form) == len(python_refused)
            and declared == "bool"
            and all(str(case.get("expected_output", "")).strip()
                    in ("True", "False") for case, _check in answer_form))
    return facts


def _python_admits_all(question, version):
    """Python admission of every case under a DIFFERENT contract version."""
    view = types.SimpleNamespace(
        pk=question.pk, execution_contract_version=version,
        boilerplate_code=question.boilerplate_code,
        hidden_wrapper_code=question.hidden_wrapper_code,
        hidden_test_cases=question.hidden_test_cases)
    for case in question.hidden_test_cases:
        if not isinstance(case, dict):
            return False
        check = next(c for c in suite_admission.check_case(view, case)
                     if c.language == _CONTRACT_LANGUAGE)
        if check.refused:
            return False
    return True


# ── stage ─────────────────────────────────────────────────────────────────

def _stage_and_blockers(question, facts, context):
    """The highest stage reached, and the blockers that stop the next one."""
    blockers = list(facts.authored_problems)
    if blockers:
        return UNAUTHORED, blockers

    structural = []
    if not facts.servable:
        structural.append(Blocker(
            STRUCTURAL_UNDELIVERABLE, STRUCTURALLY_VALID,
            f"deliverability: {facts.undeliverable_cause}"))
    readiness = facts.python_readiness
    if readiness.cause == language_readiness.NO_SOLUTION_CLASS:
        structural.append(Blocker(NO_SOLUTION_CLASS, STRUCTURALLY_VALID,
                                  readiness.reason))
    elif not readiness.ready:
        structural.append(Blocker(PYTHON_NOT_READY, STRUCTURALLY_VALID,
                                  f"{readiness.cause}: {readiness.reason}"))
    elif facts.method is None:
        structural.append(Blocker(NO_GRADED_METHOD, STRUCTURALLY_VALID,
                                  "no Solution method for any harness to call"))
    if facts.unannotated:
        structural.append(Blocker(UNANNOTATED_PARAMETER, STRUCTURALLY_VALID,
                                  ", ".join(facts.unannotated)))
    if facts.returns_none:
        structural.append(Blocker(IN_PLACE_RETURN, STRUCTURALLY_VALID,
                                  "the graded method is declared -> None"))
    if structural:
        return AUTHORED, structural

    checkable = []
    if facts.suite_problems or not facts.gradable:
        checkable.append(Blocker(MALFORMED_SUITE, CHECKABLE,
                                 f"{len(facts.suite_problems)} malformed case(s)"))
    python_refused = facts.refusals[_CONTRACT_LANGUAGE]
    if facts.own_wrappers and python_refused:
        checkable.append(Blocker(OWN_WRAPPER, CHECKABLE,
                                 ", ".join(facts.own_wrappers)))
    if python_refused:
        input_refused = len(python_refused) - facts.answer_form_refusals
        if facts.answer_form_refusals:
            checkable.append(Blocker(
                PYTHON_ANSWER_FORM_REFUSED, CHECKABLE,
                f"{facts.answer_form_refusals} case(s)"))
        if input_refused:
            checkable.append(Blocker(
                PYTHON_INPUT_REFUSED, CHECKABLE,
                f"{input_refused} case(s); first: "
                f"{python_refused[0][1].detail[:160]}"))
    if checkable:
        return STRUCTURALLY_VALID, checkable

    stage = CHECKABLE
    advisory = []
    # Python trust is not blocked by another language, so this caps nothing;
    # it is reported because a learner choosing that language is mis-graded.
    for lang in facts.served_refusals():
        advisory.append(Blocker(
            LANGUAGE_BINDING_REFUSED, CHECKABLE,
            f"{lang}: {len(facts.refusals[lang])} case(s); first: "
            f"{facts.refusals[lang][0][1].detail[:120]}"))
    if facts.duplicates:
        advisory.append(Blocker(DUPLICATE_CASES, QUALITY_PASSED,
                                f"{facts.duplicates} duplicate input(s)"))
    if facts.below_floor:
        advisory.append(Blocker(
            BELOW_MIN_TESTS, QUALITY_PASSED,
            f"{len(question.hidden_test_cases)} case(s); the quality gate "
            f"requires {hidden_tests.MIN_HIDDEN_TESTS}"))

    approvals = context.approvals.get(question.pk, [])
    ladder = (
        (REFERENCE_PRESENT, NO_APPROVED_REFERENCE,
         bool(context.references.get(question.pk))),
        (ORACLE_EVIDENCE, NO_ORACLE_EVIDENCE,
         context.oracle_success.get(question.pk, 0) > 0),
        # Both facts first appear in the database in ONE QuestionApproval row:
        # a passing quality report that nobody has approved is a file, which a
        # read-only report cannot see. So the quality stage reads the verdict
        # the approval froze, and the approval stage reads that it exists.
        (QUALITY_PASSED, NO_PASSING_QUALITY_APPROVAL,
         any(a.quality_passed for a in approvals)),
        (HUMAN_APPROVED, NO_PASSING_QUALITY_APPROVAL, bool(approvals)),
        (ORACLE_VERIFIED, NOT_ORACLE_VERIFIED,
         question.trust_state == question.TRUST_ORACLE_VERIFIED),
        (PUBLISHED, NOT_PUBLISHED,
         question.status == question.STATUS_PUBLISHED),
        (ADAPTIVE_ELIGIBLE, NOT_ADAPTIVE_ELIGIBLE,
         question.is_adaptive_eligible),
    )
    for next_stage, code, holds in ladder:
        if not holds:
            return stage, advisory + [Blocker(code, next_stage)]
        stage = next_stage
    return stage, advisory


def _invariant_violations(question, stage):
    """Stored trust that the derived evidence does not support."""
    verified = question.trust_state == question.TRUST_ORACLE_VERIFIED
    if verified and STAGES.index(stage) < STAGES.index(HUMAN_APPROVED):
        return [Blocker(TRUST_INVARIANT_BROKEN, ORACLE_VERIFIED,
                        f"stored ORACLE_VERIFIED, derived stage {stage}")]
    if question.is_adaptive_eligible and stage != ADAPTIVE_ELIGIBLE:
        return [Blocker(TRUST_INVARIANT_BROKEN, ADAPTIVE_ELIGIBLE,
                        f"adaptive-eligible, derived stage {stage}")]
    return []


# ── category ──────────────────────────────────────────────────────────────

def _category(question, facts, stage, blockers):
    """
    Exactly one category, by deterministic precedence: the first rule that
    matches wins, and the rules are ordered so a stronger claim can never be
    reached past a weaker fact (quarantine before trust, trust only when every
    stage holds, Python refusals before references).
    """
    codes = {b.code for b in blockers}
    if content_quarantine.is_quarantined(question.pk):
        return "H", "QUARANTINED"
    if TRUST_INVARIANT_BROKEN in codes:
        return "C", "TRUST_INVARIANT_BROKEN"
    if stage == ADAPTIVE_ELIGIBLE and facts.servable:
        return "A", "TRUSTED_AND_SERVED"
    if facts.authored_problems:
        return "C", "NOT_AUTHORED"
    if facts.python_readiness.cause == language_readiness.NO_SOLUTION_CLASS:
        return "I", "NO_SOLUTION_CLASS"
    if STRUCTURAL_UNDELIVERABLE in codes:
        source = language_readiness.boilerplate_for(
            question, _CONTRACT_LANGUAGE) or ""
        verdict = migration_readiness.classify(
            question.pk, source, question.hidden_test_cases,
            version=question.execution_contract_version or "")
        bucket = getattr(verdict, "bucket", None)
        if bucket in _V2_REPAIRABLE:
            return "G", f"V2:{bucket}"
        return "H", f"STRUCTURAL_NO_CONTRACT:{facts.undeliverable_cause}:{bucket}"
    if IN_PLACE_RETURN in codes:
        return "H", "IN_PLACE"
    if OWN_WRAPPER in codes:
        return "H", "OWN_WRAPPER"
    if MALFORMED_SUITE in codes:
        return "C", "MALFORMED_SUITE"
    if PYTHON_NOT_READY in codes or NO_GRADED_METHOD in codes:
        return "E", "PYTHON_STARTER_NOT_READY"
    if UNANNOTATED_PARAMETER in codes:
        return "E", "UNANNOTATED_SIGNATURE"
    if PYTHON_INPUT_REFUSED in codes or PYTHON_ANSWER_FORM_REFUSED in codes:
        return _refusal_category(question, facts, codes)
    served_refusals = facts.served_refusals()
    if served_refusals:
        return "F", "LANGUAGE_BINDING:" + "+".join(served_refusals)
    if NO_APPROVED_REFERENCE in codes:
        return "D", "REFERENCE_REQUIRED"
    if NO_ORACLE_EVIDENCE in codes:
        return "D", "ORACLE_EVIDENCE_REQUIRED"
    if NO_PASSING_QUALITY_APPROVAL in codes:
        return "D", ("SUITE_EXPANSION_AND_QUALITY_REQUIRED"
                     if BELOW_MIN_TESTS in codes else "QUALITY_AND_APPROVAL_REQUIRED")
    if NOT_ORACLE_VERIFIED in codes:
        return "C", "AWAITING_PROMOTION"
    if NOT_PUBLISHED in codes:
        return "C", "AWAITING_PUBLICATION"
    return "C", "UNCLASSIFIED"


def _refusal_category(question, facts, codes):
    if PYTHON_INPUT_REFUSED not in codes:
        if facts.tf_respell:
            return "B", "ANSWER_FORM_TRUE_FALSE"
        return "C", "ANSWER_KEY_FORM"
    try:
        input_repair.plan(question)
        return "B", "INPUT_REPRESENTATION"
    except input_repair.RepairRefused:
        pass
    if _python_admits_all(question, "v3"):
        return "E", "CONTRACT_V3_ADMITS"
    if _python_admits_all(question, "v2"):
        return "E", "CONTRACT_V2_ADMITS"
    return "C", "INPUT_SEMANTICS"


# ── per-language readiness ────────────────────────────────────────────────

def _languages(question, facts):
    out = []
    authored = not facts.authored_problems
    for lang in languages.REGISTRY:
        verdict = facts.verdicts[lang.key]
        refused = None
        if authored and lang.key in facts.refusals:
            refused = len(facts.refusals[lang.key])
        out.append(LanguageReadiness(
            language=lang.key, verdict=verdict.verdict, cause=verdict.cause,
            served=verdict.ready, self_contained=lang.self_contained,
            refused_cases=refused,
            adaptive_eligible=question.adaptive_eligible_for(lang.key)))
    return tuple(out)


# ── the entry point ───────────────────────────────────────────────────────

def assess(question, context):
    """
    The readiness report for one question. Reads only `question` and
    `context`; writes nothing and executes nothing.
    """
    facts = _facts(question, context)
    stage, blockers = _stage_and_blockers(question, facts, context)
    blockers = blockers + _invariant_violations(question, stage)
    if content_quarantine.is_quarantined(question.pk):
        entry = content_quarantine.entry_for(question.pk)
        blockers.append(Blocker(QUARANTINED, CHECKABLE,
                                getattr(entry, "finding", "")[:160]))
    letter, reason = _category(question, facts, stage, blockers)
    cases = question.hidden_test_cases
    approvals = context.approvals.get(question.pk, [])
    evidence = {
        "approved_references": list(context.references.get(question.pk, ())),
        "oracle_successes": context.oracle_success.get(question.pk, 0),
        "approvals": len(approvals),
        "passing_quality_approvals": sum(1 for a in approvals if a.quality_passed),
    }
    return ReadinessReport(
        question_id=question.pk,
        topic=getattr(getattr(question, "topic", None), "name", None),
        stage=stage,
        category=letter,
        reason=reason,
        allowed_operation=CATEGORIES[letter].allowed_operation,
        blockers=tuple(blockers),
        status=question.status,
        trust_state=question.trust_state,
        verified_language=question.verified_language,
        contract_version=question.execution_contract_version or "v1",
        servable=facts.servable,
        case_count=len(cases) if isinstance(cases, list) else 0,
        language_readiness=_languages(question, facts),
        is_adaptive_eligible=question.is_adaptive_eligible,
        evidence=evidence,
    )
