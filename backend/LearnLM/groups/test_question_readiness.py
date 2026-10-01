"""
QP1 — canonical question readiness.

Synthetic questions, one per category and per edge case, classified through the
real validators against the local test database. Every invariant is asserted
over every fixture rather than over a hand-picked example, and the agreement
tests compare QP1 with the predicates it claims to reuse instead of trusting
that it calls them.

Local/synthetic database only. Nothing here reaches Judge0.
"""

import io
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from common import languages
from groups import (deliverability, language_readiness, provenance,
                    suite_admission)
from groups import question_readiness as qr
from groups.coding_views import _servable_questions
from groups.conftest import approved_reference
from groups.management.commands import question_readiness as command
from groups.models import (CodingPortal, OracleExecution, Question,
                           QuestionApproval, Topic)

User = get_user_model()

PY = "class Solution:\n    def total(self, nums: list[int]) -> int:\n        pass\n"
JS = ("var Solution = function() {};\nSolution.prototype.total = "
      "function(nums) {\n    return 0;\n};\n")
JAVA = ("class Solution {\n    public int total(int[] nums) {\n"
        "        return 0;\n    }\n}\n")
CANONICAL = [{"stdin": "[[1,2,3]]", "expected_output": "6"},
             {"stdin": "[[4]]", "expected_output": "4"}]
#: A trusted question passed the quality gate, whose floor is 12 cases.
TWELVE = [{"stdin": f"[[{i},{i}]]", "expected_output": str(2 * i)}
          for i in range(1, 13)]
TWO_INTS = {
    "python": "class Solution:\n    def f(self, a: int, b: int) -> int:\n        pass\n",
    "javascript": ("var Solution = function() {};\nSolution.prototype.f = "
                   "function(a, b) {\n    return 0;\n};\n"),
    "java": ("class Solution {\n    public int f(int a, int b) {\n"
             "        return 0;\n    }\n}\n"),
}
TREE = ("from typing import Optional\n\nclass TreeNode:\n"
        "    def __init__(self, val=0, left=None, right=None):\n"
        "        self.val = val\n        self.left = left\n"
        "        self.right = right\n\nclass Solution:\n"
        "    def maxDepth(self, root: Optional[TreeNode]) -> int:\n"
        "        pass\n")
NARY = ("class Node:\n    def __init__(self, val=None, children=None):\n"
        "        self.val = val\n        self.children = children\n\n"
        "class Solution:\n    def maxDepth(self, root: 'Node') -> int:\n"
        "        pass\n")
PASSING_QUALITY = {"tier1_kill_rate": 1.0, "tier2_kill_rate": 0.9,
                   "blockers": [], "mutant_identifiers": ["m1"]}

#: name -> (overrides, expected category, expected reason)
SHAPES = {
    "D_reference_required": ({}, "D", "REFERENCE_REQUIRED"),
    "B_true_false": ({
        "boilerplate_code": {
            "python": "class Solution:\n    def check(self, n: int) -> bool:\n        pass\n",
            "javascript": ("var Solution = function() {};\nSolution.prototype."
                           "check = function(n) {\n    return false;\n};\n"),
            "java": ("class Solution {\n    public boolean check(int n) {\n"
                     "        return false;\n    }\n}\n")},
        "hidden_test_cases": [{"stdin": "4", "expected_output": "True"},
                              {"stdin": "3", "expected_output": "False"}]},
        "B", "ANSWER_FORM_TRUE_FALSE"),
    "B_input_representation": ({
        "hidden_test_cases": [{"stdin": "1,2,3", "expected_output": "6"},
                              {"stdin": "4,5", "expected_output": "9"}]},
        "B", "INPUT_REPRESENTATION"),
    "C_not_authored": ({
        "content": Question.PLACEHOLDER_MARKER + " a placeholder.",
        "hidden_test_cases": []}, "C", "NOT_AUTHORED"),
    "C_malformed_suite": ({
        "hidden_test_cases": [{"stdin": "[[1]]"},
                              {"stdin": "[[2]]", "expected_output": "2"}]},
        "C", "MALFORMED_SUITE"),
    "C_input_semantics": ({
        "boilerplate_code": {"python": TWO_INTS["python"]},
        "hidden_test_cases": [{"stdin": "1", "expected_output": "1"}]},
        "C", "INPUT_SEMANTICS"),
    "C_answer_key_form": ({
        "hidden_test_cases": [{"stdin": "[[1,2,3]]", "expected_output": "six"}]},
        "C", "ANSWER_KEY_FORM"),
    "E_unannotated": ({
        "boilerplate_code": {"python": "class Solution:\n    def total(self, nums) -> int:\n        pass\n"}},
        "E", "UNANNOTATED_SIGNATURE"),
    "E_unparseable": ({
        "boilerplate_code": {"python": "class Solution:\n    def total(self, nums: list[int]) -> int\n        pass\n"}},
        "E", "PYTHON_STARTER_NOT_READY"),
    "E_missing_python_starter": ({
        "boilerplate_code": {"javascript": JS, "java": JAVA}},
        "E", "PYTHON_STARTER_NOT_READY"),
    "E_v3_admits": ({
        "boilerplate_code": {"python": TWO_INTS["python"]},
        "hidden_test_cases": [{"stdin": "1 2", "expected_output": "3"}]},
        "E", "CONTRACT_V3_ADMITS"),
    "F_java_mismatch": ({
        "boilerplate_code": TWO_INTS,
        "hidden_test_cases": [{"stdin": "[1, 2]", "expected_output": "3"}]},
        "F", "LANGUAGE_BINDING:java"),
    "G_v2_ready_tree": ({
        "boilerplate_code": {"python": TREE},
        "hidden_test_cases": [{"stdin": "[3,9,20,null,null,15,7]",
                               "expected_output": "3"}]},
        "G", "V2:SAFE_TO_MIGRATE"),
    "H_unsupported_node": ({
        "boilerplate_code": {"python": NARY},
        "hidden_test_cases": [{"stdin": "[1,null,3,2,4]", "expected_output": "2"}]},
        "H", "STRUCTURAL_NO_CONTRACT:structural_unsupported:UNSUPPORTED_NODE"),
    "H_in_place": ({
        "boilerplate_code": {"python": "class Solution:\n    def rotate(self, nums: list[int]) -> None:\n        pass\n"}},
        "H", "IN_PLACE"),
    "H_own_wrapper": ({
        "hidden_wrapper_code": {"python": "{user_code}\nprint(1)\n"},
        "hidden_test_cases": [{"stdin": "1,2,3", "expected_output": "6"}]},
        "H", "OWN_WRAPPER"),
    "I_design_class": ({
        "boilerplate_code": {"python": "class MinStack:\n    def push(self, x: int) -> None:\n        pass\n"}},
        "I", "NO_SOLUTION_CLASS"),
}
FIRST_ID = 9100
SHAPE_IDS = {name: FIRST_ID + i for i, name in enumerate(SHAPES)}
TRUSTED_ID = 9300


# ═════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def operator(db):
    return User.objects.create_user(username="qp1-op", password="pw",
                                    email="qp1@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="QP1 Portal")
    made, _ = Topic.objects.get_or_create(
        name="QP1Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make(topic, pk, **overrides):
    fields = {"id": pk, "title": f"Q{pk}", "content": "A real statement.",
              "topic": topic, "base_difficulty": 1200.0,
              "boilerplate_code": {"python": PY, "javascript": JS, "java": JAVA},
              "hidden_test_cases": CANONICAL, "hidden_wrapper_code": {},
              "execution_contract_version": "v1"}
    fields.update(overrides)
    return Question.objects.create(**fields)


def make_trusted(topic, operator, pk, *, approve=True, oracle=True,
                 promote=True, **overrides):
    """A question carried through the trust pipeline's recorded facts."""
    fields = {"status": Question.STATUS_PUBLISHED,
              "trust_state": Question.TRUST_ORACLE_VERIFIED,
              "verified_language": "python", "hidden_test_cases": TWELVE}
    fields.update(overrides)
    question = make(topic, pk, **fields)
    reference = approved_reference(question, approver=operator)
    reference.refresh_from_db()
    if oracle:
        provenance.record_execution(
            question=question, reference=reference, stdin="[[4]]",
            produced_output="4", status=OracleExecution.STATUS_SUCCESS,
            execution_contract_version="v1")
    if approve:
        QuestionApproval.objects.create(
            question=question, reference=reference,
            reference_source_hash=reference.source_hash,
            artifact_digest="a" * 64, approved_by=operator,
            approved_at=timezone.now(), quality_outcome=PASSING_QUALITY,
            promoted_at=timezone.now() if promote else None,
            promoted_by=operator if promote else None)
    return question


@pytest.fixture
def bank(db, topic, operator):
    """Every category shape, plus one trusted question."""
    for name, (overrides, _cat, _why) in SHAPES.items():
        make(topic, SHAPE_IDS[name], **overrides)
    make_trusted(topic, operator, TRUSTED_ID)
    return Question.objects.all()


def reports(question_ids=None):
    context = qr.BankContext.load(question_ids=question_ids)
    queryset = Question.objects.select_related("topic").order_by("pk")
    if question_ids is not None:
        queryset = queryset.filter(pk__in=question_ids)
    return {q.pk: qr.assess(q, context) for q in queryset}


def report_for(pk):
    return reports([pk])[pk]


# ═════════════════════════════════════════════════════════════
# Every category is reachable, and lands where it should
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_each_shape_receives_its_category(bank, name):
    _overrides, category, reason = SHAPES[name]
    report = report_for(SHAPE_IDS[name])
    assert (report.category, report.reason) == (category, reason)


@pytest.mark.django_db
def test_every_category_a_to_i_is_reachable(bank):
    assert {r.category for r in reports().values()} == set("ABCDEFGHI")


@pytest.mark.django_db
def test_a_trusted_question_is_A_and_adaptive_eligible(bank):
    report = report_for(TRUSTED_ID)
    assert report.category == "A"
    assert report.stage == qr.ADAPTIVE_ELIGIBLE
    assert report.blockers == ()
    assert report.as_dict()["adaptive_eligibility"]["python"] is True
    assert report.as_dict()["adaptive_eligibility"]["java"] is False


# ═════════════════════════════════════════════════════════════
# The trust ladder past CHECKABLE
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_reference_without_oracle_evidence_needs_evidence(topic, operator):
    question = make(topic, 9401)
    approved_reference(question, approver=operator)
    report = report_for(9401)
    assert (report.stage, report.category, report.reason) == (
        qr.REFERENCE_PRESENT, "D", "ORACLE_EVIDENCE_REQUIRED")
    assert report.evidence == {"approved_references": ["python"],
                               "oracle_successes": 0, "approvals": 0,
                               "passing_quality_approvals": 0}


@pytest.mark.django_db
def test_evidence_is_reported_even_when_the_stage_stops_earlier(topic, operator):
    # q1779's shape: a reference and oracle runs exist, but the stored inputs
    # do not bind, so the next step is the contract, not the trust chain.
    make_trusted(topic, operator, 9408, approve=False,
                 status=Question.STATUS_DRAFT,
                 trust_state=Question.TRUST_UNVERIFIED, verified_language=None,
                 boilerplate_code={"python": TWO_INTS["python"]},
                 hidden_test_cases=[{"stdin": "1 2", "expected_output": "3"}])
    report = report_for(9408)
    assert (report.stage, report.category) == (qr.STRUCTURALLY_VALID, "E")
    assert report.evidence["approved_references"] == ["python"]
    assert report.evidence["oracle_successes"] == 1


@pytest.mark.django_db
@pytest.mark.parametrize("cases, reason", [
    (CANONICAL, "SUITE_EXPANSION_AND_QUALITY_REQUIRED"),
    (TWELVE, "QUALITY_AND_APPROVAL_REQUIRED"),
])
def test_oracle_evidence_without_approval_needs_quality(topic, operator,
                                                         cases, reason):
    make_trusted(topic, operator, 9402, approve=False,
                 status=Question.STATUS_DRAFT,
                 trust_state=Question.TRUST_UNVERIFIED, verified_language=None,
                 hidden_test_cases=cases)
    report = report_for(9402)
    assert (report.stage, report.category, report.reason) == (
        qr.ORACLE_EVIDENCE, "D", reason)


@pytest.mark.django_db
def test_an_approved_unpromoted_question_awaits_promotion(topic, operator):
    make_trusted(topic, operator, 9403, promote=False,
                 status=Question.STATUS_PENDING_REVIEW,
                 trust_state=Question.TRUST_UNVERIFIED, verified_language=None)
    report = report_for(9403)
    assert (report.stage, report.category, report.reason) == (
        qr.HUMAN_APPROVED, "C", "AWAITING_PROMOTION")


@pytest.mark.django_db
def test_a_verified_unpublished_question_awaits_publication(topic, operator):
    make_trusted(topic, operator, 9404, status=Question.STATUS_PENDING_REVIEW)
    report = report_for(9404)
    assert (report.stage, report.category, report.reason) == (
        qr.ORACLE_VERIFIED, "C", "AWAITING_PUBLICATION")


@pytest.mark.django_db
def test_a_published_but_unverified_question_is_not_adaptive_eligible(topic):
    # The legacy PUBLISHED + UNVERIFIED state: available, never trusted.
    question = make(topic, 9407, status=Question.STATUS_PUBLISHED)
    report = report_for(9407)
    assert not question.is_adaptive_eligible
    assert report.is_adaptive_eligible is False
    assert report.as_dict()["adaptive_eligibility"]["question"] is False
    assert (report.stage, report.category) == (qr.CHECKABLE, "D")


@pytest.mark.django_db
def test_stored_trust_without_evidence_breaks_the_invariant(topic):
    make(topic, 9405, status=Question.STATUS_PUBLISHED,
         trust_state=Question.TRUST_ORACLE_VERIFIED, verified_language="python")
    report = report_for(9405)
    assert report.category == "C"
    assert report.reason == "TRUST_INVARIANT_BROKEN"
    assert qr.TRUST_INVARIANT_BROKEN in {b.code for b in report.blockers}


# ═════════════════════════════════════════════════════════════
# The twelve safety invariants, over every fixture
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_1_an_untrusted_question_is_never_A(bank):
    for pk, report in reports().items():
        if Question.objects.get(pk=pk).trust_state != Question.TRUST_ORACLE_VERIFIED:
            assert report.category != "A", pk


@pytest.mark.django_db
def test_2_a_python_refusal_is_never_A_or_D(bank):
    for pk, report in reports().items():
        python = next(lr for lr in report.language_readiness
                      if lr.language == "python")
        if python.refused_cases:
            assert report.category not in ("A", "D"), pk


@pytest.mark.django_db
def test_3_an_unauthored_question_is_never_servable(bank):
    for pk, report in reports().items():
        if report.stage == qr.UNAUTHORED:
            assert not report.servable, pk
            assert report.category == "C", pk


@pytest.mark.django_db
def test_4_a_quarantined_question_is_always_H_even_when_trusted(topic, operator):
    make_trusted(topic, operator, 98)
    make(topic, 9406)
    report = report_for(98)
    assert report.category == "H"
    assert report.reason == "QUARANTINED"
    assert qr.QUARANTINED in {b.code for b in report.blockers}


@pytest.mark.django_db
def test_5_no_stage_outruns_the_stored_trust_state(bank):
    order = qr.STAGES.index
    for pk, report in reports().items():
        question = Question.objects.get(pk=pk)
        if order(report.stage) >= order(qr.ORACLE_VERIFIED):
            assert question.trust_state == Question.TRUST_ORACLE_VERIFIED, pk
        if order(report.stage) >= order(qr.PUBLISHED):
            assert question.status == Question.STATUS_PUBLISHED, pk
        if report.stage == qr.ADAPTIVE_ELIGIBLE:
            assert question.is_adaptive_eligible, pk
        if report.category in ("B", "E", "G"):
            assert order(report.stage) < order(qr.CHECKABLE), pk


@pytest.mark.django_db
def test_6_adaptive_eligibility_agrees_with_the_model(bank):
    for pk, report in reports().items():
        question = Question.objects.get(pk=pk)
        assert report.is_adaptive_eligible == question.is_adaptive_eligible
        for entry in report.language_readiness:
            assert entry.adaptive_eligible == \
                question.adaptive_eligible_for(entry.language), (pk, entry.language)


@pytest.mark.django_db
def test_7_servable_agrees_with_servable_questions(bank):
    servable = set(_servable_questions().values_list("pk", flat=True))
    for pk, report in reports().items():
        assert report.servable == (pk in servable), pk


@pytest.mark.django_db
def test_8_the_structural_blocker_agrees_with_deliverability(bank):
    for pk, report in reports().items():
        if report.stage == qr.UNAUTHORED:
            continue
        undeliverable = deliverability.blocker(Question.objects.get(pk=pk)) is not None
        flagged = qr.STRUCTURAL_UNDELIVERABLE in {b.code for b in report.blockers}
        assert flagged == undeliverable, pk


@pytest.mark.django_db
def test_9_python_admission_agrees_with_suite_admission(bank):
    for pk, report in reports().items():
        if report.stage == qr.UNAUTHORED:
            continue
        question = Question.objects.get(pk=pk)
        refused = sum(
            1 for case in question.hidden_test_cases
            if next(c for c in suite_admission.check_case(question, case)
                    if c.language == "python").refused)
        python = next(lr for lr in report.language_readiness
                      if lr.language == "python")
        assert python.refused_cases == refused, pk


@pytest.mark.django_db
def test_10_precedence_is_deterministic_when_rules_overlap(topic, operator):
    # quarantine beats trust; design-class beats structure; in-place beats a
    # refusal; own wrapper beats a refusal
    make_trusted(topic, operator, 98)
    make(topic, 9501, boilerplate_code={"python": (
        "class TreeNode:\n    pass\n\nclass MinStack:\n"
        "    def push(self, x: 'TreeNode') -> None:\n        pass\n")})
    make(topic, 9502, boilerplate_code={"python": (
        "class Solution:\n    def rotate(self, nums: list[int]) -> None:\n"
        "        pass\n")}, hidden_test_cases=[{"stdin": "1,2", "expected_output": "x"}])
    make(topic, 9503, hidden_wrapper_code={"python": "{user_code}"},
         hidden_test_cases=[{"stdin": "1,2", "expected_output": "3"}])
    got = {pk: (r.category, r.reason) for pk, r in reports().items()}
    assert got[98] == ("H", "QUARANTINED")
    assert got[9501] == ("I", "NO_SOLUTION_CLASS")
    assert got[9502] == ("H", "IN_PLACE")
    assert got[9503] == ("H", "OWN_WRAPPER")


@pytest.mark.django_db
def test_11_every_question_gets_exactly_one_known_category(bank):
    for pk, report in reports().items():
        assert report.category in qr.CATEGORIES, pk
        assert report.reason, pk
        assert report.allowed_operation == \
            qr.CATEGORIES[report.category].allowed_operation
        assert report.stage in qr.STAGES
        assert all(b.code in qr.BLOCKER_CODES for b in report.blockers)


@pytest.mark.django_db
def test_12_the_same_input_produces_the_same_report(bank):
    first, second = reports(), reports()
    for pk in first:
        assert first[pk] == second[pk]
        assert json.dumps(first[pk].as_dict(), sort_keys=True) == \
            json.dumps(second[pk].as_dict(), sort_keys=True)


# ═════════════════════════════════════════════════════════════
# Languages
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_language_readiness_is_read_from_language_readiness(bank):
    for pk, report in reports().items():
        question = Question.objects.get(pk=pk)
        assert [e.language for e in report.language_readiness] == \
            [lang.key for lang in languages.REGISTRY]
        for entry in report.language_readiness:
            verdict = language_readiness.assess(question, entry.language)
            assert (entry.verdict, entry.cause, entry.served) == \
                (verdict.verdict, verdict.cause, verdict.ready), (pk, entry.language)


@pytest.mark.django_db
def test_c_and_cpp_stay_self_contained(bank):
    for report in reports().values():
        for entry in report.language_readiness:
            if entry.language in ("c", "cpp"):
                assert entry.self_contained
                assert entry.refused_cases is None


@pytest.mark.django_db
def test_a_missing_language_starter_is_not_served(topic):
    make(topic, 9601, boilerplate_code={"python": PY, "javascript": JS})
    report = report_for(9601)
    java = next(e for e in report.language_readiness if e.language == "java")
    assert (java.served, java.cause) == (False, language_readiness.NO_STARTER)
    assert report.category == "D"


@pytest.mark.django_db
def test_a_mis_binding_language_that_is_not_served_is_not_F(topic):
    # Java's harness would mis-bind this case, but there is no Java starter,
    # so no learner can choose Java: Python's next step decides.
    make(topic, 9602, boilerplate_code={"python": TWO_INTS["python"],
                                        "javascript": TWO_INTS["javascript"]},
         hidden_test_cases=[{"stdin": "[1, 2]", "expected_output": "3"}])
    report = report_for(9602)
    java = next(e for e in report.language_readiness if e.language == "java")
    assert java.refused_cases == 1 and not java.served
    assert report.category == "D"


@pytest.mark.django_db
def test_a_non_text_answer_key_is_a_malformed_suite_not_a_crash(topic):
    # Found by the first production run: an answer key stored as a JSON
    # number reached a helper that reads text.
    make(topic, 9604, hidden_test_cases=[
        {"stdin": "[[1,2,3]]", "expected_output": 6},
        {"stdin": "[[4]]", "expected_output": "4"}])
    report = report_for(9604)
    assert (report.category, report.reason) == ("C", "MALFORMED_SUITE")


@pytest.mark.django_db
def test_a_true_answer_on_an_int_method_is_not_a_true_false_respell(topic):
    make(topic, 9603, hidden_test_cases=[
        {"stdin": "[[1,2,3]]", "expected_output": "True"}])
    report = report_for(9603)
    assert (report.category, report.reason) == ("C", "ANSWER_KEY_FORM")


@pytest.mark.django_db
def test_a_served_language_that_mis_binds_is_reported(bank):
    report = report_for(SHAPE_IDS["F_java_mismatch"])
    blocker = next(b for b in report.blockers
                   if b.code == qr.LANGUAGE_BINDING_REFUSED)
    assert blocker.detail.startswith("java:")


# ═════════════════════════════════════════════════════════════
# Read-only, and bounded in queries
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_assess_issues_no_query_once_the_context_is_loaded(bank):
    context = qr.BankContext.load()
    questions = list(Question.objects.select_related("topic"))
    with CaptureQueriesContext(connection) as captured:
        for question in questions:
            qr.assess(question, context)
    assert len(captured) == 0


@pytest.mark.django_db
def test_the_context_costs_the_same_for_one_question_or_all(bank):
    with CaptureQueriesContext(connection) as one:
        qr.BankContext.load(question_ids=[TRUSTED_ID])
    with CaptureQueriesContext(connection) as everything:
        qr.BankContext.load()
    assert len(one) == len(everything)
    assert all(q["sql"].lstrip().upper().startswith(("SELECT", "WITH"))
               for q in everything.captured_queries)


@pytest.mark.django_db
def test_the_context_reads_references_oracle_and_approvals(bank):
    context = qr.BankContext.load()
    assert context.references[TRUSTED_ID] == ("python",)
    assert context.oracle_success[TRUSTED_ID] == 1
    assert context.approvals[TRUSTED_ID][0].quality_passed
    assert TRUSTED_ID in context.servable_ids
    scoped = qr.BankContext.load(question_ids=[SHAPE_IDS["D_reference_required"]])
    assert scoped.references == {} and scoped.servable_ids == {
        SHAPE_IDS["D_reference_required"]}


def snapshot():
    return {q.pk: (q.title, q.status, q.trust_state, q.hidden_test_cases,
                   q.execution_contract_version, q.boilerplate_code)
            for q in Question.objects.all()}


def run(*extra):
    out = io.StringIO()
    call_command("question_readiness", "--allow-non-production",
                 "--allow-write-role", *extra, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_the_command_writes_nothing(bank):
    before = snapshot()
    counts = (QuestionApproval.objects.count(), OracleExecution.objects.count())
    run()
    assert snapshot() == before
    assert (QuestionApproval.objects.count(), OracleExecution.objects.count()) == counts


@pytest.mark.django_db
def test_the_statement_guard_refuses_a_write(bank, monkeypatch):
    original = qr.assess

    def assess_that_writes(question, context):
        Question.objects.filter(pk=question.pk).update(title="changed")
        return original(question, context)

    monkeypatch.setattr(qr, "assess", assess_that_writes)
    before = snapshot()
    with pytest.raises(command.ReadOnlyViolation):
        # A savepoint, so the refused statement does not poison the test's
        # own transaction and the snapshot below can still read.
        with transaction.atomic():
            run()
    assert snapshot() == before


def test_the_guard_only_admits_reads():
    guard = command._StatementGuard()
    calls = []

    def execute(sql, params, many, context):
        calls.append(sql)

    guard(execute, "SELECT 1", None, False, None)
    guard(execute, "  with x as (select 1) select * from x", None, False, None)
    for statement in ("UPDATE groups_question SET title = 'x'",
                      "INSERT INTO t VALUES (1)", "DELETE FROM t",
                      "SELECT 1 FOR UPDATE; UPDATE t SET a = 1"[22:]):
        with pytest.raises(command.ReadOnlyViolation):
            guard(execute, statement, None, False, None)
    assert len(calls) == 2 and guard.count == 2


@pytest.mark.django_db
def test_one_failing_question_is_reported_without_losing_the_bank(
        bank, monkeypatch, tmp_path):
    original = qr.assess

    def assess_that_fails_once(question, context):
        if question.pk == TRUSTED_ID:
            raise ValueError("synthetic failure")
        return original(question, context)

    monkeypatch.setattr(qr, "assess", assess_that_fails_once)
    target = tmp_path / "readiness.json"
    with pytest.raises(CommandError, match="could not be assessed"):
        run("--out", str(target))
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["errors"] == [{"question_id": TRUSTED_ID,
                                  "error": "ValueError('synthetic failure')"}]
    assert payload["questions"] == Question.objects.count() - 1


@pytest.mark.django_db
def test_the_command_refuses_a_local_database_without_the_gate_flag(bank):
    with pytest.raises(CommandError, match="CONNECTION GATE FAILED"):
        call_command("question_readiness", stdout=io.StringIO())


# ═════════════════════════════════════════════════════════════
# The command's output
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_json_payload(bank):
    payload = json.loads(run("--json"))
    assert payload["schema_version"] == qr.SCHEMA_VERSION
    assert payload["questions"] == Question.objects.count()
    assert sum(payload["categories"].values()) == payload["questions"]
    assert sum(payload["stages"].values()) == payload["questions"]
    assert len(payload["reports"]) == payload["questions"]
    assert payload["adaptive_eligible"] == 1
    assert payload["measurements"]["queries"] > 0
    first = payload["reports"][0]
    for key in ("question_id", "stage", "category", "blockers",
                "allowed_operation", "trust_state", "status",
                "verified_language", "language_readiness",
                "adaptive_eligibility", "evidence", "schema_version"):
        assert key in first


@pytest.mark.django_db
def test_subsets_by_question_topic_and_category(bank, topic):
    ids = [SHAPE_IDS["D_reference_required"], TRUSTED_ID]
    payload = json.loads(run("--json", "--questions", *map(str, ids)))
    assert sorted(r["question_id"] for r in payload["reports"]) == sorted(ids)

    other = Topic.objects.create(name="QP1Other", structure_type="flat",
                                 portal=topic.portal)
    make(other, 9701)
    payload = json.loads(run("--json", "--topic", "QP1Other"))
    assert [r["question_id"] for r in payload["reports"]] == [9701]

    payload = json.loads(run("--json", "--category", "H"))
    assert payload["reports"] and all(r["category"] == "H"
                                      for r in payload["reports"])


@pytest.mark.django_db
def test_out_writes_the_full_report(bank, tmp_path):
    target = tmp_path / "readiness.json"
    text = run("--out", str(target))
    assert "QUESTION READINESS" in text
    assert json.loads(target.read_text(encoding="utf-8"))["questions"] == \
        Question.objects.count()
