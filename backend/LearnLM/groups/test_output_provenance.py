"""
Output provenance (M2 P2.7g-1).

The property this file exists to prove:

    Every recorded output can be traced to the exact approved reference, the
    exact revision of its source, the exact input, the exact contract and the
    exact execution that produced it — and none of that can be edited
    afterwards.

Provenance records that an execution HAPPENED. It never asserts the output is
correct: no test here promotes a question, and several assert that it cannot.

SYNTHETIC ONLY. No Judge0, no production data, no real expected_output, and no
reference solution outside the throwaway test database.
"""

import hashlib

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from groups import provenance
from groups.models import (
    CodingPortal, OracleExecution, Question, ReferenceSolution, Topic,
    compute_source_hash,
)
from groups.utils import normalize_output

User = get_user_model()

SOURCE = "class Solution:\n    def solve(self, n):\n        return n\n"
TABLE = OracleExecution._meta.db_table


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Prov Portal")


@pytest.fixture
def topic(portal):
    return Topic.objects.create(name="ProvTopic", structure_type="flat",
                                portal=portal)


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="prov-op", email="po@t.com",
                                    password="Pv#2026xyz", is_staff=True)


def make_question(topic, title="Prov Q"):
    return Question.objects.create(
        title=title, content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        boilerplate_code={"python": "x"}, hidden_wrapper_code={})


def approved_reference(question, operator, source=SOURCE, language="python",
                       active=True):
    """A reference walked through the real P2.7d lifecycle."""
    reference = ReferenceSolution.objects.create(
        question=question, language=language, source_code=source)
    reference.submit_for_review()
    reference.approve(by=operator)
    if active:
        reference.activate()
    return reference


@pytest.fixture
def question(topic):
    return make_question(topic)


@pytest.fixture
def reference(question, operator):
    return approved_reference(question, operator)


def record(question, reference, stdin="5", output="5", **kwargs):
    kwargs.setdefault("status", OracleExecution.STATUS_SUCCESS)
    kwargs.setdefault("execution_contract_version", "v2")
    return provenance.record_execution(
        question=question, reference=reference, stdin=stdin,
        produced_output=output, **kwargs)


# ═════════════════════════════════════════════════════════════
# Recording
# ═════════════════════════════════════════════════════════════

def test_a_valid_execution_is_recorded(question, reference):
    execution = record(question, reference, stdin="7", output="7")

    assert execution.pk is not None
    assert execution.question_id == question.pk
    assert execution.reference_id == reference.pk
    assert execution.status == OracleExecution.STATUS_SUCCESS
    assert execution.execution_contract_version == "v2"


def test_the_record_pins_the_exact_reference_revision(question, reference):
    execution = record(question, reference)

    assert execution.reference_source_hash == compute_source_hash(SOURCE)
    assert execution.reference_source_hash == reference.source_hash


def test_the_record_captures_reproducibility_metadata(question, reference):
    execution = record(question, reference,
                       executor={"runner": "judge0", "cpu_limit": 2.0})

    assert execution.executor == {"runner": "judge0", "cpu_limit": 2.0}
    assert execution.executed_at is not None
    assert execution.provenance_schema_version == \
        OracleExecution.PROVENANCE_SCHEMA_VERSION


def test_an_explicit_timestamp_is_preserved(question, reference):
    moment = timezone.now() - timezone.timedelta(days=3)

    execution = record(question, reference, executed_at=moment)

    assert execution.executed_at == moment


def test_a_failed_execution_is_still_recorded(question, reference):
    """A reference that crashed is a fact worth keeping, not an absence."""
    execution = record(question, reference, output="",
                       status=OracleExecution.STATUS_FAILED)

    assert execution.status == OracleExecution.STATUS_FAILED
    assert execution.is_authoritative is False


# ═════════════════════════════════════════════════════════════
# Digest strategy
# ═════════════════════════════════════════════════════════════

def test_case_identity_reuses_the_established_normalization():
    """
    Not a third definition. `reseed_questions` and the P2.7h-1 quality gate
    both compare `normalize_output(stdin)`; this digests exactly that.
    """
    stdin = "3 1 2 3\n"
    expected = hashlib.sha256(
        normalize_output(stdin).encode("utf-8")).hexdigest()

    assert provenance.case_identity(stdin) == expected


def test_case_identity_is_stable_under_whitespace(question, reference):
    assert provenance.case_identity("1 2 3") == \
        provenance.case_identity("1 2 3\n")


def test_case_identity_differs_for_different_inputs():
    assert provenance.case_identity("1 2") != provenance.case_identity("1 3")


def test_input_identity_records_what_actually_executed():
    """
    The executor receives the literal backslash-n converted to a newline, so
    two stored cases that differ only in that respect execute identically.
    """
    assert provenance.effective_input("1\\n2") == "1\n2"
    assert provenance.input_identity("1\\n2") == provenance.input_identity("1\n2")


def test_case_identity_and_input_identity_are_different_questions():
    """
    They answer different things and must not be collapsed: identity of a
    CASE versus fingerprint of the bytes that RAN.

    For an input with nothing to normalise and no literal backslash-n the two
    coincide, which is correct — the distinction only shows up where the
    stored form and the executed form diverge. Both such inputs are asserted.
    """
    trailing = "1 2 3\n"           # normalisation strips it; execution does not
    assert provenance.case_identity(trailing) != \
        provenance.input_identity(trailing)

    literal = "1\\n2"              # execution converts it; normalisation does not
    assert provenance.case_identity(literal) != \
        provenance.input_identity(literal)

    plain = "1 2 3"                # nothing to diverge on — they agree
    assert provenance.case_identity(plain) == provenance.input_identity(plain)


def test_the_output_digest_matches_the_output(question, reference):
    execution = record(question, reference, output="42")

    assert execution.output_digest == provenance.output_identity("42")
    assert execution.output_digest == hashlib.sha256(b"42").hexdigest()


def test_identity_is_deterministic():
    for _ in range(5):
        assert provenance.case_identity("x") == provenance.case_identity("x")
        assert provenance.input_identity("x") == provenance.input_identity("x")


# ═════════════════════════════════════════════════════════════
# Ownership — a reference answers only for its own question
# ═════════════════════════════════════════════════════════════

def test_a_reference_from_another_question_is_refused(topic, operator):
    question_a = make_question(topic, "A")
    question_b = make_question(topic, "B")
    reference_b = approved_reference(question_b, operator)

    with pytest.raises(ValidationError, match="may not cross questions"):
        record(question_a, reference_b)

    assert OracleExecution.objects.count() == 0


def test_the_database_also_refuses_cross_question_provenance(topic, operator):
    """
    Application checks are bypassed by raw SQL; the trigger is not. This is
    the same cross-question defect P2.7d's review found in OracleService.
    """
    question_a = make_question(topic, "A")
    question_b = make_question(topic, "B")
    reference_b = approved_reference(question_b, operator)
    digest = provenance.output_identity("x")

    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (question_id, reference_id, "
                f"reference_source_hash, language, case_digest, input_digest, "
                f"produced_output, output_digest, execution_contract_version, "
                f"status, executed_at, executor, provenance_schema_version, "
                f"is_authoritative, created_at) VALUES "
                f"(%s,%s,%s,'python',%s,%s,'x',%s,'v2','SUCCESS',now(),"
                f"'{{}}',1,false,now())",
                [question_a.pk, reference_b.pk, reference_b.source_hash,
                 "d" * 64, "d" * 64, digest])


def test_a_reference_for_its_own_question_is_accepted(question, reference):
    """Positive control for the two above."""
    assert record(question, reference).pk is not None


# ═════════════════════════════════════════════════════════════
# Approval is a precondition
# ═════════════════════════════════════════════════════════════

def test_an_unapproved_reference_cannot_produce_provenance(question):
    draft = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOURCE)

    with pytest.raises(ValidationError, match="only an APPROVED reference"):
        record(question, draft)


def test_a_rejected_reference_cannot_produce_provenance(question, operator):
    rejected = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOURCE)
    rejected.submit_for_review()
    rejected.reject()

    with pytest.raises(ValidationError, match="only an APPROVED reference"):
        record(question, rejected)


def test_a_deactivated_but_approved_reference_may_still_be_recorded(
        question, reference):
    """
    Provenance is history. A superseded reference's executions are exactly
    what a revocation needs to read, so activity is NOT a precondition.
    """
    reference.deactivate()

    execution = record(question, reference)

    assert execution.pk is not None
    assert reference.is_active is False


# ═════════════════════════════════════════════════════════════
# Immutability
# ═════════════════════════════════════════════════════════════

def test_the_orm_refuses_to_edit_a_recorded_execution(question, reference):
    execution = record(question, reference)
    execution.produced_output = "tampered"

    with pytest.raises(ValidationError, match="append-only"):
        execution.save()


def test_queryset_update_is_refused_by_the_database(question, reference):
    """`save()` is bypassed by `update()`; the trigger is not."""
    execution = record(question, reference)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OracleExecution.objects.filter(pk=execution.pk).update(
                produced_output="tampered")


@pytest.mark.parametrize("column,value", [
    ("reference_source_hash", "'" + "0" * 64 + "'"),
    ("case_digest", "'" + "0" * 64 + "'"),
    ("input_digest", "'" + "0" * 64 + "'"),
    ("execution_contract_version", "'v1'"),
    ("status", "'FAILED'"),
    ("executed_at", "now()"),
    ("language", "'java'"),
    ("provenance_schema_version", "99"),
])
def test_every_immutable_column_is_frozen_at_the_database(
        question, reference, column, value):
    execution = record(question, reference)

    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET {column} = {value} WHERE id = %s",
                [execution.pk])


def test_raw_sql_cannot_repoint_a_record_at_another_question(
        topic, operator, question, reference):
    other = make_question(topic, "Other")
    execution = record(question, reference)

    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET question_id = %s WHERE id = %s",
                [other.pk, execution.pk])


def test_is_authoritative_remains_mutable(question, reference):
    """
    The one intended exception. An execution is a fact; whether its output is
    the accepted answer is a later decision, and recording that decision must
    not require rewriting the fact.
    """
    execution = record(question, reference)

    execution.is_authoritative = True
    execution.save(update_fields=["is_authoritative"])

    execution.refresh_from_db()
    assert execution.is_authoritative is True
    assert execution.produced_output == "5"


def test_a_correction_is_a_new_row_not_an_edit(question, reference):
    first = record(question, reference, output="wrong")
    second = record(question, reference, output="right")

    assert first.pk != second.pk
    first.refresh_from_db()
    assert first.produced_output == "wrong", "history was rewritten"
    assert OracleExecution.objects.count() == 2


# ═════════════════════════════════════════════════════════════
# Digest integrity at the database
# ═════════════════════════════════════════════════════════════

def test_an_output_that_disagrees_with_its_digest_is_unwritable(
        question, reference):
    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {TABLE} (question_id, reference_id, "
                f"reference_source_hash, language, case_digest, input_digest, "
                f"produced_output, output_digest, execution_contract_version, "
                f"status, executed_at, executor, provenance_schema_version, "
                f"is_authoritative, created_at) VALUES "
                f"(%s,%s,%s,'python',%s,%s,'actual output',%s,'v2','SUCCESS',"
                f"now(),'{{}}',1,false,now())",
                [question.pk, reference.pk, reference.source_hash,
                 "d" * 64, "d" * 64, provenance.output_identity("different")])


# ═════════════════════════════════════════════════════════════
# Idempotency: many executions, one authoritative answer
# ═════════════════════════════════════════════════════════════

def test_repeated_executions_are_all_recorded(question, reference):
    """Determinism checking runs the same input repeatedly. All are facts."""
    for _ in range(3):
        record(question, reference, stdin="9", output="9")

    assert OracleExecution.objects.filter(question=question).count() == 3


def test_only_one_execution_may_be_authoritative_per_case(question, reference):
    first = record(question, reference, stdin="9", output="9",
                   is_authoritative=True)
    second = record(question, reference, stdin="9", output="9")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            second.is_authoritative = True
            second.save(update_fields=["is_authoritative"])

    assert first.is_authoritative is True


def test_different_cases_may_each_be_authoritative(question, reference):
    a = record(question, reference, stdin="1", output="1", is_authoritative=True)
    b = record(question, reference, stdin="2", output="2", is_authoritative=True)

    assert a.is_authoritative and b.is_authoritative


def test_the_authoritative_output_is_retrievable_by_input(question, reference):
    record(question, reference, stdin="8", output="wrong")
    accepted = record(question, reference, stdin="8", output="8",
                      is_authoritative=True)

    found = provenance.authoritative_output(question, "8")

    assert found.pk == accepted.pk
    assert found.produced_output == "8"


def test_no_authoritative_output_returns_none(question, reference):
    record(question, reference, stdin="8", output="8")

    assert provenance.authoritative_output(question, "8") is None


# ═════════════════════════════════════════════════════════════
# Distinguishing the four identity axes
# ═════════════════════════════════════════════════════════════

def test_same_input_different_reference_is_distinguishable(question, operator):
    first = approved_reference(question, operator, language="python")
    second = approved_reference(question, operator, source="int main(){}",
                                language="cpp")

    a = record(question, first, stdin="1", output="1")
    b = record(question, second, stdin="1", output="1")

    assert a.case_digest == b.case_digest
    assert a.reference_id != b.reference_id
    assert a.reference_source_hash != b.reference_source_hash


def test_same_input_different_reference_revision_is_distinguishable(
        question, operator):
    first = approved_reference(question, operator, source="version one\n")
    first.deactivate()
    second = approved_reference(question, operator, source="version two\n")

    a = record(question, first, stdin="1", output="1")
    b = record(question, second, stdin="1", output="1")

    assert a.reference_source_hash != b.reference_source_hash
    assert a.case_digest == b.case_digest


def test_different_input_same_reference_is_distinguishable(question, reference):
    a = record(question, reference, stdin="1", output="1")
    b = record(question, reference, stdin="2", output="2")

    assert a.case_digest != b.case_digest
    assert a.reference_id == b.reference_id


def test_same_input_same_reference_different_contract_is_distinguishable(
        question, reference):
    a = record(question, reference, stdin="1", output="1",
               execution_contract_version="v1")
    b = record(question, reference, stdin="1", output="1",
               execution_contract_version="v2")

    assert a.case_digest == b.case_digest
    assert a.execution_contract_version != b.execution_contract_version


# ═════════════════════════════════════════════════════════════
# Revocation compatibility (P2.7d F5)
# ═════════════════════════════════════════════════════════════

def test_every_output_from_a_reference_is_retrievable(question, operator):
    first = approved_reference(question, operator, source="one\n")
    first.deactivate()
    second = approved_reference(question, operator, source="two\n")
    for stdin in ("1", "2", "3"):
        record(question, first, stdin=stdin, output=stdin)
    record(question, second, stdin="4", output="4")

    produced = provenance.outputs_produced_by(first)

    assert produced.count() == 3
    assert all(e.reference_id == first.pk for e in produced)


def test_outputs_are_retrievable_by_reference_revision(question, operator):
    first = approved_reference(question, operator, source="one\n")
    first.deactivate()
    second = approved_reference(question, operator, source="two\n")
    record(question, first, stdin="1", output="1")
    record(question, second, stdin="2", output="2")

    from_first = provenance.outputs_produced_by_source_hash(
        compute_source_hash("one\n"))

    assert from_first.count() == 1
    assert from_first.first().reference_id == first.pk


def test_provenance_survives_reference_deactivation(question, reference):
    execution = record(question, reference)

    reference.deactivate()

    execution.refresh_from_db()
    assert execution.reference_id == reference.pk
    assert provenance.outputs_produced_by(reference).count() == 1


def test_a_reference_with_provenance_cannot_be_deleted(question, reference):
    """
    PROTECT. Provenance that outlives what it describes is not provenance.
    """
    from django.db.models import ProtectedError

    record(question, reference)

    with pytest.raises(ProtectedError):
        reference.delete()


def test_a_question_with_provenance_cannot_be_deleted(question, reference):
    from django.db.models import ProtectedError

    record(question, reference)

    with pytest.raises(ProtectedError):
        question.delete()


# ═════════════════════════════════════════════════════════════
# Trust boundary — provenance grants nothing
# ═════════════════════════════════════════════════════════════

def test_recording_does_not_publish_the_question(question, reference):
    record(question, reference, is_authoritative=True)

    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


def test_recording_does_not_set_trust_state(question, reference):
    record(question, reference, is_authoritative=True)

    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


def test_recording_does_not_touch_hidden_tests_or_expected_output(
        question, reference):
    before = question.hidden_test_cases

    record(question, reference, stdin="1", output="totally different")

    question.refresh_from_db()
    assert question.hidden_test_cases == before


def test_recording_does_not_activate_the_reference(question, operator):
    approved = approved_reference(question, operator, active=False)

    record(question, approved)

    approved.refresh_from_db()
    assert approved.is_active is False


def test_recording_creates_no_learner_state(question, reference):
    from groups.models import (
        CodeSubmission, LearnerTopicSkill, QuestionSkill, UserCodingProfile,
        UserTopicMastery,
    )

    record(question, reference, is_authoritative=True)

    assert CodeSubmission.objects.count() == 0
    assert UserCodingProfile.objects.count() == 0
    assert UserTopicMastery.objects.count() == 0
    assert LearnerTopicSkill.objects.count() == 0
    assert QuestionSkill.objects.count() == 0


# ═════════════════════════════════════════════════════════════
# Security
# ═════════════════════════════════════════════════════════════

def test_provenance_has_no_serializer():
    import inspect

    from rest_framework import serializers as drf

    from groups import serializers as groups_serializers

    for _, obj in inspect.getmembers(groups_serializers, inspect.isclass):
        if not (isinstance(obj, type) and issubclass(obj, drf.BaseSerializer)):
            continue
        model = getattr(getattr(obj, "Meta", None), "model", None)
        assert model is not OracleExecution, f"{obj.__name__} exposes provenance"
        declared = set(getattr(obj, "_declared_fields", {}))
        meta_fields = set(getattr(getattr(obj, "Meta", None), "fields", None) or [])
        assert not ({"produced_output", "reference_source_hash", "executor",
                     "input_digest"} & (declared | meta_fields))


def test_provenance_is_absent_from_admin():
    from django.contrib import admin

    assert OracleExecution not in admin.site._registry


def test_no_module_both_reads_provenance_and_builds_a_response():
    """
    The same structural guard `test_reference_solution_secrecy` applies to
    reference source: reading provenance and building an HTTP response in one
    module is the shape a leak takes.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parent.parent
    offenders = []
    for path in backend.rglob("*.py"):
        if "test" in path.name or "migrations" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "OracleExecution" not in source and "provenance" not in source:
            continue
        if "produced_output" not in source and "OracleExecution" not in source:
            continue
        if "Response(" in source or "JsonResponse" in source:
            offenders.append(str(path.relative_to(backend)))

    assert offenders == [], f"these modules expose provenance: {offenders}"


def test_no_learner_endpoint_returns_provenance(question, reference, topic):
    from django.urls import reverse
    from rest_framework.test import APIClient

    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED)
    record(question, reference, stdin="1", output="SECRET_ORACLE_OUTPUT_4471")
    learner = User.objects.create_user(username="prov-learner",
                                       email="pl@t.com", password="Pl#2026xyz")
    client = APIClient()
    client.force_authenticate(user=learner)

    responses = [
        client.get(reverse("code-next-problem"), {"topic": "ProvTopic"}),
        client.post(reverse("code-run"), {
            "code": "print(1)", "language": "python",
            "problem_id": question.pk}, format="json"),
    ]

    for response in responses:
        body = response.content.decode()
        assert "SECRET_ORACLE_OUTPUT_4471" not in body
        assert reference.source_code not in body


# ═════════════════════════════════════════════════════════════
# Migration safety
# ═════════════════════════════════════════════════════════════

def test_the_migration_creates_no_provenance_rows(db):
    """The test database is built by replaying every migration."""
    assert OracleExecution.objects.count() == 0


def test_the_migration_is_additive_only():
    """
    Asserted against the migration's EXECUTABLE source. Its own docstring
    explains that it does not touch hidden_test_cases, and a raw text search
    reads that sentence as evidence of the thing it denies — the trap the
    P2.7d and P2.7h-1 guards both hit.
    """
    import ast
    from pathlib import Path

    path = (Path(__file__).resolve().parent / "migrations"
            / "0041_output_provenance.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    executable = ast.unparse(tree)

    assert "CreateModel" in executable
    for forbidden in ("AlterField", "RemoveField", "DeleteModel", "RunPython",
                      "hidden_test_cases", "expected_output", "trust_state",
                      "adaptive_eligible"):
        assert forbidden not in executable, f"migration 0041 uses {forbidden}"


def test_both_triggers_exist_in_the_database(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass "
            "AND NOT tgisinternal ORDER BY tgname", [TABLE])
        triggers = [row[0] for row in cursor.fetchall()]

    assert "groups_oracleexecution_append_only_trg" in triggers
    assert "groups_oracleexecution_ownership_trg" in triggers


# ═════════════════════════════════════════════════════════════
# Layer separation — found by mutation testing
# ═════════════════════════════════════════════════════════════
#
# Ownership is guarded three times: in `record_execution`, in `clean()`, and by
# a database trigger. Defence in depth means removing any ONE layer is
# invisible, so the first sweep could not tell them apart. These pin each layer
# individually.

def test_ownership_is_reported_before_approval(topic, operator):
    """
    Pins the `record_execution` guard specifically.

    A reference that is BOTH cross-question AND unapproved has two problems.
    Ownership is the more fundamental one and is checked first, so that is what
    the operator is told. If that guard were removed the approval check would
    answer instead — a correct refusal for the wrong reason.
    """
    question_a = make_question(topic, "A")
    question_b = make_question(topic, "B")
    draft_for_b = ReferenceSolution.objects.create(
        question=question_b, language="python", source_code=SOURCE)

    with pytest.raises(ValidationError) as exc:
        record(question_a, draft_for_b)

    assert "may not cross questions" in str(exc.value)
    assert "APPROVED" not in str(exc.value)


def test_model_clean_refuses_a_cross_question_reference(topic, operator):
    """Pins the `clean()` layer, reached by any caller that validates."""
    question_a = make_question(topic, "A")
    question_b = make_question(topic, "B")
    reference_b = approved_reference(question_b, operator)

    execution = OracleExecution(
        question=question_a, reference=reference_b,
        reference_source_hash=reference_b.source_hash, language="python",
        case_digest="d" * 64, input_digest="d" * 64,
        produced_output="x", output_digest=provenance.output_identity("x"),
        execution_contract_version="v2",
        status=OracleExecution.STATUS_SUCCESS, executed_at=timezone.now())

    with pytest.raises(ValidationError) as exc:
        execution.full_clean()

    assert "reference" in exc.value.message_dict


def test_a_consistent_output_rewrite_is_still_refused(question, reference):
    """
    Rewriting output AND its digest together — the one path that satisfies the
    digest CHECK — must still be refused.

    EQUIVALENCE NOTE. The trigger's `produced_output` clause turns out to be
    redundant, and mutation testing proved it: deleting that clause changes
    nothing observable, because every route to a rewritten output is already
    closed twice over.

        change produced_output alone      -> the digest CHECK fires
                                             (output_digest no longer matches)
        change produced_output + digest   -> the trigger's output_digest
                                             clause fires

    There is no third route, so no test can isolate the clause. It is kept as
    defence in depth — if the digest CHECK were ever dropped it becomes the
    only guard — and recorded here as EQUIVALENT rather than counted as killed.
    """
    execution = record(question, reference, output="original")
    forged = provenance.output_identity("rewritten")

    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET produced_output = %s, output_digest = %s "
                f"WHERE id = %s", ["rewritten", forged, execution.pk])

    execution.refresh_from_db()
    assert execution.produced_output == "original"


def test_the_source_hash_check_is_unreachable_by_construction(question, reference):
    """
    EQUIVALENT-MUTANT PROOF, not a behaviour test.

    `record_execution` recomputes the reference source hash and refuses a
    mismatch. That branch cannot be reached through any sanctioned path: the
    P2.7d constraint `reference_approved_source_unmodified` makes an APPROVED
    row whose source disagrees with its hash UNWRITABLE, and `record_execution`
    already requires APPROVED. Deleting the check is therefore an equivalent
    mutant, and this records why rather than pretending it was killed.

    It is still asserted, because the equivalence depends on that constraint
    continuing to exist.
    """
    assert reference.review_state == ReferenceSolution.REVIEW_APPROVED
    assert reference.source_hash == compute_source_hash(reference.source_code)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            ReferenceSolution.objects.filter(pk=reference.pk).update(
                source_code="a different implementation")


def test_repointing_the_question_always_violates_ownership(topic, operator):
    """
    EQUIVALENCE NOTE for the trigger's `question_id` clause.

    A reference belongs to exactly one question, and the ownership trigger
    fires on UPDATE as well as INSERT. So repointing a record's `question_id`
    at anything other than its current value necessarily breaks ownership and
    is refused there — the append-only clause for that column can never be the
    guard that fires. Recorded as EQUIVALENT rather than counted as killed.

    Asserted because the equivalence depends on the ownership trigger covering
    UPDATE, which is easy to lose in a later edit.
    """
    other = make_question(topic, "Elsewhere")
    reference = approved_reference(other, operator)
    question = make_question(topic, "Home")
    home_reference = approved_reference(question, operator)
    execution = record(question, home_reference)

    with pytest.raises(IntegrityError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {TABLE} SET question_id = %s WHERE id = %s",
                [other.pk, execution.pk])

    execution.refresh_from_db()
    assert execution.question_id == question.pk
