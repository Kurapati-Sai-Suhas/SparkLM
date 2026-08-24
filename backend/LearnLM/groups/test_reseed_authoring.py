"""
The reseed authoring path: statement generation and signature declaration
(M2 P2.7h-18).

Local/synthetic database only. Nothing here touches production.

What these tests exist to hold: the two authoring commands may write exactly
one column each, only on a question that has no grading truth to corrupt, and
only inside a frozen batch with a verified pre-image. Everything else — the
suite, the expected outputs, the contract, the status, the trust state — must
be unreachable from here, and the coordinator must be unable to forge the
record of having done any of it.
"""

import hashlib

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from groups import pre_image, reseed_authoring
from groups.models import (
    CodingPortal, OracleExecution, Question, QuestionApproval,
    QuestionPreImage, ReferenceSolution, RemediationAction, RemediationBatch,
    ReseedLedger, Topic,
)

VARIADIC = ("class Solution:\n"
            "    def widgetCount(self, *args, **kwargs):\n"
            "        pass\n")
DECLARED = ("class Solution:\n"
            "    def widgetCount(self, grid: list[list[str]], target: int):\n"
            "        pass\n")
STATEMENT = "<p>Count the widgets in the grid that match the target.</p>"
#: `oracle_execution_output_digest_matches` recomputes this in the database.
PRODUCED = "1"

APPLY = ("--apply", "--confirm")

#: A refusal reaches the operator either as a gate failure or as the
#: write-ahead check refusing outright. Both are refusals; neither writes.
REFUSALS = (CommandError, pre_image.CaptureIncomplete)


@pytest.fixture
def operator(db, django_user_model):
    return django_user_model.objects.create_user(
        username="reseed-author", password="pw", email="a@example.com",
        is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Authoring Portal")
    row, _ = Topic.objects.get_or_create(
        name="AuthoringTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return row


def make_stub(topic, question_id=9850, **overrides):
    fields = dict(
        id=question_id, title="Widget Count", topic=topic,
        base_difficulty=1300.0,
        content=f"<p>{Question.PLACEHOLDER_MARKER} Widget Count</p>",
        boilerplate_code={"python": VARIADIC},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")
    fields.update(overrides)
    return Question.objects.create(**fields)


@pytest.fixture
def stub(topic):
    return make_stub(topic)


def freeze(question, operator, key="reseed-slice-1"):
    batch = RemediationBatch.objects.create(
        batch_key=key, purpose="authoring tests", created_by=operator)
    pre_image.capture(batch, question, operator)
    RemediationBatch.objects.filter(pk=batch.pk).update(
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    batch.refresh_from_db()
    return batch


@pytest.fixture
def batch(stub, operator):
    return freeze(stub, operator)


def approved_reference(question, operator):
    """
    A reference in the only shape the database accepts.

    `reference_approval_provenance` requires review_state, approved_by,
    approved_at and source_hash to move together, so the lifecycle methods are
    used rather than hand-built rows.
    """
    row = ReferenceSolution.objects.create(
        question=question, language="python", source_code=DECLARED)
    row.submit_for_review()
    row.approve(by=operator)
    row.activate()
    return row


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def generate(tmp_path, question, operator, batch, content=STATEMENT,
             digest=None, extra=()):
    call_command("reseed_statement", "--batch", batch.batch_key,
                 "--question", str(question.pk),
                 "--content-file", write(tmp_path, "s.html", content),
                 "--expect-digest", digest or pre_image.live_digest(question),
                 "--reason", "reseed slice 1",
                 "--operator", operator.username, "--local", *extra)


def declare(tmp_path, question, operator, batch, source=DECLARED,
            digest=None, extra=()):
    call_command("declare_signature", "--batch", batch.batch_key,
                 "--question", str(question.pk),
                 "--source-file", write(tmp_path, "b.py", source),
                 "--expect-digest", digest or pre_image.live_digest(question),
                 "--reason", "reseed slice 1",
                 "--operator", operator.username, "--local", *extra)


# ═════════════════════════════════════════════════════════════
# 1-2. Statement generation, and the back door that is now shut
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_statement_generation_succeeds_on_a_virgin_stub(tmp_path, stub, batch,
                                                        operator):
    before = pre_image.live_digest(stub)
    generate(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert stub.content == STATEMENT
    assert Question.PLACEHOLDER_MARKER not in stub.content
    assert pre_image.live_digest(stub) != before

    actions = RemediationAction.objects.filter(question=stub)
    assert actions.count() == 1
    assert actions.first().action_class == \
        RemediationAction.CLASS_STATEMENT_GENERATION


@pytest.mark.django_db
def test_statement_generation_changes_nothing_else(tmp_path, stub, batch,
                                                   operator):
    before = pre_image.question_state(stub)
    generate(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    after = pre_image.question_state(stub)
    for field, value in before.items():
        if field == "content":
            continue
        assert after[field] == value, field


@pytest.mark.django_db
def test_statement_repair_refuses_a_placeholder(tmp_path, stub, batch,
                                                operator):
    """The repair command must not be usable as a generation back door."""
    with pytest.raises(CommandError, match="STATEMENT_GENERATION"):
        call_command("remediate_statement", "--batch", batch.batch_key,
                     "--question", str(stub.pk),
                     "--content-file", write(tmp_path, "s.html", STATEMENT),
                     "--reason", "sneaky", "--operator", operator.username,
                     "--local", *APPLY)

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content
    assert RemediationAction.objects.filter(question=stub).count() == 0


@pytest.mark.django_db
def test_generation_refuses_a_question_with_a_real_statement(tmp_path, topic,
                                                             operator):
    """The exact complement: neither command accepts the other's population."""
    real = make_stub(topic, 9851, content="<p>A real statement.</p>")
    batch = freeze(real, operator, key="real-batch")

    with pytest.raises(CommandError, match="placeholder marker"):
        generate(tmp_path, real, operator, batch, extra=APPLY)


@pytest.mark.django_db
def test_generation_refuses_a_statement_that_keeps_the_marker(tmp_path, stub,
                                                              batch, operator):
    with pytest.raises(CommandError, match="placeholder marker"):
        generate(tmp_path, stub, operator, batch,
                 content=f"<p>{Question.PLACEHOLDER_MARKER} still</p>",
                 extra=APPLY)


# ═════════════════════════════════════════════════════════════
# 3-8. Signature declaration and everything it refuses
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_signature_declaration_succeeds_on_a_virgin_stub(tmp_path, stub, batch,
                                                         operator):
    declare(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert stub.boilerplate_code["python"] == DECLARED
    actions = RemediationAction.objects.filter(question=stub)
    assert actions.count() == 1
    assert actions.first().action_class == \
        RemediationAction.CLASS_SIGNATURE_DECLARATION


@pytest.mark.django_db
def test_signature_declaration_changes_nothing_else(tmp_path, stub, batch,
                                                    operator):
    before = pre_image.question_state(stub)
    declare(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    after = pre_image.question_state(stub)
    for field, value in before.items():
        if field == "boilerplate_code":
            continue
        assert after[field] == value, field
    assert stub.hidden_test_cases == []


@pytest.mark.django_db
def test_signature_declaration_refuses_existing_hidden_tests(tmp_path, topic,
                                                             operator):
    question = make_stub(topic, 9852,
                         hidden_test_cases=[{"stdin": "1", "expected_output": "1"}])
    batch = freeze(question, operator, key="suite-batch")

    with pytest.raises(CommandError, match="hidden test case"):
        declare(tmp_path, question, operator, batch, extra=APPLY)

    question.refresh_from_db()
    assert question.boilerplate_code["python"] == VARIADIC


@pytest.mark.django_db
def test_signature_declaration_refuses_an_existing_oracle_execution(
        tmp_path, stub, batch, operator):
    reference = approved_reference(stub, operator)
    OracleExecution.objects.create(
        question=stub, reference=reference,
        reference_source_hash=reference.source_hash, language="python",
        case_digest="c" * 64, input_digest="d" * 64,
        produced_output=PRODUCED,
        output_digest=hashlib.sha256(PRODUCED.encode()).hexdigest(),
        execution_contract_version="v1", status="SUCCESS",
        executed_at=timezone.now())

    with pytest.raises(CommandError, match="OracleExecution"):
        declare(tmp_path, stub, operator, batch, extra=APPLY)


@pytest.mark.django_db
def test_signature_declaration_refuses_an_existing_approval(tmp_path, stub,
                                                            batch, operator):
    reference = approved_reference(stub, operator)
    QuestionApproval.objects.create(
        question=stub, reference=reference,
        reference_source_hash=reference.source_hash,
        artifact_digest="b" * 64, quality_outcome={"verdict": "PASS"},
        approved_by_id=operator.pk, approved_at=timezone.now())

    with pytest.raises(CommandError, match="QuestionApproval"):
        declare(tmp_path, stub, operator, batch, extra=APPLY)


@pytest.mark.django_db
def test_signature_declaration_refuses_published(tmp_path, topic, operator):
    question = make_stub(topic, 9853, status=Question.STATUS_PUBLISHED)
    batch = freeze(question, operator, key="published-batch")

    with pytest.raises(CommandError, match="not DRAFT"):
        declare(tmp_path, question, operator, batch, extra=APPLY)


@pytest.mark.django_db
def test_signature_declaration_refuses_oracle_verified(tmp_path, topic,
                                                       operator):
    """
    A DB CHECK forbids DRAFT + ORACLE_VERIFIED outright, so the nearest
    reachable shape is PENDING_REVIEW — which trips the status gate too. That
    is the point: verified trust is never one refusal away from a write.
    """
    question = make_stub(topic, 9854, status=Question.STATUS_PENDING_REVIEW,
                         trust_state=Question.TRUST_ORACLE_VERIFIED)
    batch = freeze(question, operator, key="verified-batch")

    with pytest.raises(CommandError, match="not UNVERIFIED"):
        declare(tmp_path, question, operator, batch, extra=APPLY)


@pytest.mark.django_db
def test_signature_declaration_refuses_a_declared_signature(tmp_path, topic,
                                                            operator):
    question = make_stub(topic, 9855,
                         boilerplate_code={"python": DECLARED})
    batch = freeze(question, operator, key="declared-batch")

    with pytest.raises(CommandError, match="already declares parameters"):
        declare(tmp_path, question, operator, batch,
                source=DECLARED.replace("target: int", "target: str"),
                extra=APPLY)


# ═════════════════════════════════════════════════════════════
# 9-11. The handshakes
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_a_digest_mismatch_refuses(tmp_path, stub, batch, operator, command):
    runner = generate if command == "statement" else declare
    with pytest.raises(CommandError, match="has moved since"):
        runner(tmp_path, stub, operator, batch, digest="0" * 64, extra=APPLY)

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content
    assert stub.boilerplate_code["python"] == VARIADIC


@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_a_batch_mismatch_refuses(tmp_path, stub, batch, topic, operator,
                                  command):
    """A question with no pre-image in THIS batch is not writable in it."""
    other = make_stub(topic, 9856)
    elsewhere = freeze(other, operator, key="other-batch")

    runner = generate if command == "statement" else declare
    with pytest.raises(REFUSALS):
        runner(tmp_path, stub, operator, elsewhere, extra=APPLY)

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content


@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_a_missing_pre_image_refuses(tmp_path, stub, batch, operator, command):
    QuestionPreImage.objects.filter(batch=batch, question=stub).delete()

    runner = generate if command == "statement" else declare
    with pytest.raises(REFUSALS):
        runner(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content


@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_an_unfrozen_batch_refuses(tmp_path, stub, operator, command):
    batch = RemediationBatch.objects.create(
        batch_key="thawed", purpose="x", created_by=operator)
    pre_image.capture(batch, stub, operator)

    runner = generate if command == "statement" else declare
    with pytest.raises(REFUSALS):
        runner(tmp_path, stub, operator, batch, extra=APPLY)


@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_the_write_is_locked(tmp_path, stub, batch, operator, command):
    """
    `select_for_update` is named in the apply path, and the preconditions are
    re-checked INSIDE it. Everything read before the lock was read without
    one, and a stub can stop being a stub in between.
    """
    import inspect
    from groups.management.commands import declare_signature, reseed_statement

    module = reseed_statement if command == "statement" else declare_signature
    source = inspect.getsource(module.Command._apply)
    assert "select_for_update()" in source
    lock = source.index("select_for_update()")
    assert "blockers" in source[lock:], "preconditions re-checked under lock"
    assert "live_digest" in source[lock:], "digest re-checked under lock"


# ═════════════════════════════════════════════════════════════
# 12-13. Dry run and audit
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("command", ["statement", "signature"])
def test_a_dry_run_writes_nothing(tmp_path, stub, batch, operator, command):
    before = pre_image.live_digest(stub)
    runner = generate if command == "statement" else declare
    runner(tmp_path, stub, operator, batch)

    stub.refresh_from_db()
    assert pre_image.live_digest(stub) == before
    assert RemediationAction.objects.count() == 0
    assert ReseedLedger.objects.count() == 0


@pytest.mark.django_db
def test_exactly_one_action_per_successful_write(tmp_path, stub, batch,
                                                 operator):
    generate(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    declare(tmp_path, stub, operator, batch, extra=APPLY)

    classes = list(RemediationAction.objects.filter(question=stub)
                   .order_by("id").values_list("action_class", flat=True))
    assert classes == [RemediationAction.CLASS_STATEMENT_GENERATION,
                       RemediationAction.CLASS_SIGNATURE_DECLARATION]


@pytest.mark.django_db
def test_the_action_carries_the_resulting_digest(tmp_path, stub, batch,
                                                 operator):
    generate(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    action = RemediationAction.objects.get(question=stub)
    assert action.post_digest == pre_image.live_digest(stub)
    assert action.pre_image.state_digest != action.post_digest


# ═════════════════════════════════════════════════════════════
# 14-16. Ledger progression
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_derived_stage_advances_with_the_real_writes(tmp_path, stub, batch,
                                                         operator):
    stage, problems = reseed_authoring.derive_stage(stub, batch)
    assert (stage, problems) == (ReseedLedger.STAGE_PENDING, [])

    generate(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    stage, problems = reseed_authoring.derive_stage(stub, batch)
    assert (stage, problems) == (ReseedLedger.STAGE_STATEMENT, [])

    declare(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    stage, problems = reseed_authoring.derive_stage(stub, batch)
    assert (stage, problems) == (ReseedLedger.STAGE_COMPLETE, [])


@pytest.mark.django_db
def test_the_derived_stage_ignores_a_lying_ledger(tmp_path, stub, batch,
                                                  operator):
    """
    The ledger is orchestration state. If it claims a stage the question did
    not reach, live state wins — which is the whole reason it holds no digest.
    """
    ReseedLedger.objects.create(batch=batch, question=stub,
                                stage=ReseedLedger.STAGE_COMPLETE)

    stage, _problems = reseed_authoring.derive_stage(stub, batch)
    assert stage == ReseedLedger.STAGE_PENDING


@pytest.mark.django_db
def test_an_edit_outside_the_audit_trail_is_a_discrepancy(stub, batch):
    """
    Marker gone, nothing recorded: something else changed the statement. The
    orchestrator must refuse rather than call it progress.
    """
    Question.objects.filter(pk=stub.pk).update(content="<p>edited elsewhere</p>")
    stub.refresh_from_db()

    stage, problems = reseed_authoring.derive_stage(stub, batch)
    assert stage == ReseedLedger.STAGE_PENDING
    assert any("changed by something else" in problem for problem in problems)


@pytest.mark.django_db
def test_failed_never_advances(stub, batch):
    row = ReseedLedger.objects.create(batch=batch, question=stub,
                                      stage=ReseedLedger.STAGE_FAILED,
                                      last_error="judge0 timeout")
    assert row.next_stage() is None
    assert row.is_resumable
    assert ReseedLedger.STAGE_FAILED not in ReseedLedger.ADVANCES


@pytest.mark.django_db
def test_complete_is_idempotent(tmp_path, stub, batch, operator):
    generate(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    declare(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()

    digest = pre_image.live_digest(stub)
    actions = RemediationAction.objects.filter(question=stub).count()

    # Re-running either stage on a COMPLETE question must refuse, not repeat.
    with pytest.raises(CommandError):
        generate(tmp_path, stub, operator, batch, extra=APPLY)
    with pytest.raises(CommandError):
        declare(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert pre_image.live_digest(stub) == digest
    assert RemediationAction.objects.filter(question=stub).count() == actions


# ═════════════════════════════════════════════════════════════
# 17-20. What the authoring path can never do
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_authoring_path_never_writes_a_hidden_test(tmp_path, stub, batch,
                                                       operator):
    generate(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    declare(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert stub.hidden_test_cases == []
    assert stub.hidden_wrapper_code == {}


@pytest.mark.django_db
def test_the_authoring_path_never_writes_status_or_trust(tmp_path, stub, batch,
                                                         operator):
    generate(tmp_path, stub, operator, batch, extra=APPLY)
    stub.refresh_from_db()
    declare(tmp_path, stub, operator, batch, extra=APPLY)

    stub.refresh_from_db()
    assert stub.status == Question.STATUS_DRAFT
    assert stub.trust_state == Question.TRUST_UNVERIFIED
    assert stub.execution_contract_version == "v1"
    assert not stub.is_adaptive_eligible


@pytest.mark.django_db
def test_neither_command_names_a_forbidden_field():
    """`update_fields` is the third brace: one column, named in the source."""
    import inspect
    from groups.management.commands import declare_signature, reseed_statement

    assert reseed_statement.AUTHORED_FIELD == "content"
    assert declare_signature.DECLARED_FIELD == "boilerplate_code"
    for module in (reseed_statement, declare_signature):
        source = inspect.getsource(module)
        for forbidden in ("hidden_test_cases=", "status=", "trust_state=",
                          "execution_contract_version="):
            assert forbidden not in source, (module.__name__, forbidden)


def test_the_orchestrator_holds_no_question_authority():
    """
    Its alias is the ledger role, whose grant list contains no question
    write. The coordinator coordinates.
    """
    from groups.management.commands import _preimage_ops as ops
    from groups.management.commands import reseed_orchestrate

    import inspect
    source = inspect.getsource(reseed_orchestrate)
    assert "ALLOWED_RESEED_ROLES" in source
    assert "RESEED_LEDGER_FORBIDDEN" in source
    for grant in ops.RESEED_ROLE_GRANTS:
        if "groups_question" in grant:
            assert grant.startswith("GRANT SELECT"), grant


def test_the_orchestrator_stops_at_complete():
    """Stages 3-8 keep their own authorities and are not driven from here."""
    import inspect
    from groups.management.commands import reseed_orchestrate

    source = inspect.getsource(reseed_orchestrate)
    for later_stage in ("expand_hidden_tests", "remediate_contract",
                        "oracle_execute", "question_approve",
                        "question_promote", "question_status"):
        assert f'call_command("{later_stage}"' not in source, later_stage
        assert f"call_command('{later_stage}'" not in source, later_stage


def test_the_orchestrator_cannot_fabricate_an_audit_action():
    """It never calls `record_action`; the writers do, under their own role."""
    import inspect
    from groups.management.commands import reseed_orchestrate

    source = inspect.getsource(reseed_orchestrate)
    assert "record_action" not in source
    assert "RemediationAction.objects.create" not in source


# ═════════════════════════════════════════════════════════════
# 21-22. The real production shapes
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_candidate_carrying_a_suite_is_rejected(topic):
    """q2201's shape: a placeholder stub that already has hidden tests."""
    question = make_stub(topic, 9857, hidden_test_cases=[
        {"stdin": "1", "expected_output": "1"} for _ in range(4)])

    blockers = reseed_authoring.signature_blockers(question)
    assert any("hidden test case" in blocker for blocker in blockers)
    assert reseed_authoring.stub_blockers(question)


@pytest.mark.django_db
@pytest.mark.parametrize("status,trust", [
    (Question.STATUS_PUBLISHED, Question.TRUST_ORACLE_VERIFIED)])
def test_a_published_verified_question_is_rejected_on_many_grounds(
        topic, status, trust):
    """q3309/q1436's shape. Defence in depth: not one ground, several."""
    question = make_stub(
        topic, 9858, status=status, trust_state=trust,
        content="<p>A real statement.</p>",
        boilerplate_code={"python": DECLARED},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}])

    blockers = reseed_authoring.signature_blockers(question)
    assert len(blockers) >= 4, blockers


@pytest.mark.django_db
def test_only_the_intended_gate_separates_candidates(topic):
    """
    23: across a candidate population, eligibility turns on the documented
    conditions and nothing else. A clean stub passes; each single defect
    fails, one reason at a time.
    """
    clean = make_stub(topic, 9860)
    assert reseed_authoring.signature_blockers(clean) == []

    defects = {
        9861: (dict(content="<p>real</p>"), "not a placeholder stub", 1),
        9862: (dict(status=Question.STATUS_PUBLISHED), "not DRAFT", 1),
        9864: (dict(hidden_test_cases=[{"stdin": "1"}]), "hidden test case", 1),
        9865: (dict(boilerplate_code={"python": DECLARED}),
               "already declares parameters", 1),
        # DRAFT + ORACLE_VERIFIED is refused by the database itself, so the
        # nearest reachable shape necessarily trips the status gate too.
        9863: (dict(status=Question.STATUS_PENDING_REVIEW,
                    trust_state=Question.TRUST_ORACLE_VERIFIED),
               "not UNVERIFIED", 2),
    }
    for question_id, (overrides, fragment, expected) in defects.items():
        question = make_stub(topic, question_id, **overrides)
        blockers = reseed_authoring.signature_blockers(question)
        assert len(blockers) == expected, (question_id, blockers)
        assert any(fragment in blocker for blocker in blockers), (
            question_id, blockers)


# ═════════════════════════════════════════════════════════════
# Signature validation
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source,fragment", [
    ("class Solution:\n    def widgetCount(self, *args):\n        pass\n",
     "*args"),
    ("class Solution:\n    def widgetCount(self, **kwargs):\n        pass\n",
     "*args"),
    ("class Solution:\n    def widgetCount(self):\n        pass\n",
     "no parameters"),
    ("class Solution:\n    def widgetCount(self, a: int, *, b: int):\n"
     "        pass\n", "keyword-only"),
    ("class Solution:\n    def widgetCount(self, a):\n        pass\n",
     "no type annotation"),
    ("class Solution:\n    def renamed(self, a: int):\n        pass\n",
     "renamed"),
    ("class Renamed:\n    def widgetCount(self, a: int):\n        pass\n",
     "class was renamed"),
    ("class Solution:\n    def widgetCount(self, a: int):\n        pass\n"
     "    def other(self, b: int):\n        pass\n", "exactly one public"),
    ("class Solution:\n    def widgetCount(self, a: int)\n        pass\n",
     "not valid Python"),
    ("class Solution:\n    def widgetCount(self, a: int):\n        return a\n",
     "not a stub"),
    ("class Solution:\n    def widgetCount(self, a: Widget):\n        pass\n",
     "cannot classify"),
])
def test_the_proposed_signature_is_validated(source, fragment):
    refusals = reseed_authoring.validate_signature(VARIADIC, source)
    assert any(fragment in refusal for refusal in refusals), refusals


def test_a_well_formed_declaration_is_accepted():
    assert reseed_authoring.validate_signature(VARIADIC, DECLARED) == []


def test_duplicate_parameters_are_refused():
    source = ("class Solution:\n"
              "    def widgetCount(self, a: int, a: int):\n        pass\n")
    refusals = reseed_authoring.validate_signature(VARIADIC, source)
    assert refusals


def test_binding_is_validated_against_cases_when_they_exist():
    """
    Not called during authoring — no cases exist yet. It is the handshake the
    hidden-test phase runs later, defined here so both halves live together.
    """
    good = reseed_authoring.binding_blockers(
        DECLARED, [{"stdin": '[["a"]]\n1'}])
    assert good == []

    bad = reseed_authoring.binding_blockers(DECLARED, [{"stdin": ""}])
    assert bad


@pytest.mark.django_db
def test_no_hidden_test_is_created_by_validation(stub):
    reseed_authoring.validate_signature(VARIADIC, DECLARED)
    stub.refresh_from_db()
    assert stub.hidden_test_cases == []


# ═════════════════════════════════════════════════════════════
# The orchestrator: stages 1-2, and the dry run
# ═════════════════════════════════════════════════════════════

def slice_files(tmp_path, question_id, statement=STATEMENT, starter=DECLARED):
    directory = tmp_path / "slice"
    directory.mkdir(exist_ok=True)
    (directory / f"{question_id}.statement.html").write_text(
        statement, encoding="utf-8")
    (directory / f"{question_id}.starter.py").write_text(
        starter, encoding="utf-8")
    return str(directory)


def orchestrate(batch, operator, content_dir=None, extra=()):
    arguments = ["--batch", batch.batch_key, "--operator", operator.username,
                 "--local"]
    if content_dir:
        arguments += ["--content-dir", content_dir]
    call_command("reseed_orchestrate", *arguments, *extra)


APPLY_SLICE = ("--apply", "--confirm",
               "--statement-alias", "default",
               "--signature-alias", "default")


@pytest.mark.django_db
def test_the_dry_run_writes_absolutely_nothing(tmp_path, stub, batch,
                                               operator):
    before = pre_image.live_digest(stub)
    orchestrate(batch, operator, slice_files(tmp_path, stub.pk))

    stub.refresh_from_db()
    assert pre_image.live_digest(stub) == before
    assert ReseedLedger.objects.count() == 0
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_the_dry_run_reports_what_it_would_do(tmp_path, stub, batch, operator,
                                              capsys):
    orchestrate(batch, operator, slice_files(tmp_path, stub.pk))
    report = capsys.readouterr().out

    assert str(stub.pk) in report
    assert pre_image.live_digest(stub) in report      # current digest
    assert "ELIGIBLE" in report
    assert "author statement" in report               # intended statement
    assert "declare signature" in report              # intended signature
    assert "derived stage" in report and "PENDING" in report
    assert "projected next" in report
    assert "pre-image" in report
    assert "DRY RUN" in report


@pytest.mark.django_db
def test_the_dry_run_explains_an_unsafe_candidate(tmp_path, topic, operator,
                                                  capsys):
    """A q2201-shaped candidate: a placeholder stub that carries a suite."""
    unsafe = make_stub(topic, 9870, hidden_test_cases=[{"stdin": "1"}])
    batch = freeze(unsafe, operator, key="unsafe-slice")

    orchestrate(batch, operator, slice_files(tmp_path, unsafe.pk))
    report = capsys.readouterr().out

    assert "REFUSED" in report
    assert "unsafe" in report
    assert "hidden test case" in report


@pytest.mark.django_db
def test_the_orchestrator_runs_both_stages_to_complete(tmp_path, stub, batch,
                                                       operator):
    orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                extra=APPLY_SLICE)

    stub.refresh_from_db()
    assert stub.content == STATEMENT
    assert stub.boilerplate_code["python"] == DECLARED

    row = ReseedLedger.objects.get(batch=batch, question=stub)
    assert row.stage == ReseedLedger.STAGE_COMPLETE

    classes = list(RemediationAction.objects.filter(question=stub)
                   .order_by("id").values_list("action_class", flat=True))
    assert classes == [RemediationAction.CLASS_STATEMENT_GENERATION,
                       RemediationAction.CLASS_SIGNATURE_DECLARATION]


@pytest.mark.django_db
def test_the_orchestrator_leaves_the_later_stages_alone(tmp_path, stub, batch,
                                                        operator):
    orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                extra=APPLY_SLICE)

    stub.refresh_from_db()
    assert stub.hidden_test_cases == []
    assert stub.hidden_wrapper_code == {}
    assert stub.status == Question.STATUS_DRAFT
    assert stub.trust_state == Question.TRUST_UNVERIFIED
    assert stub.execution_contract_version == "v1"
    assert QuestionApproval.objects.filter(question=stub).count() == 0
    assert OracleExecution.objects.filter(question=stub).count() == 0


@pytest.mark.django_db
def test_a_missing_artefact_fails_without_advancing(tmp_path, stub, batch,
                                                    operator):
    """FAILED records the reason and moves the question nowhere."""
    directory = tmp_path / "empty"
    directory.mkdir()
    orchestrate(batch, operator, str(directory), extra=APPLY_SLICE)

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content
    assert RemediationAction.objects.filter(question=stub).count() == 0

    row = ReseedLedger.objects.get(batch=batch, question=stub)
    assert row.stage == ReseedLedger.STAGE_FAILED
    assert "no statement file" in row.last_error
    assert row.attempts == 1
    assert row.next_stage() is None


@pytest.mark.django_db
def test_a_failed_question_resumes_from_live_state(tmp_path, stub, batch,
                                                   operator):
    """
    Resume re-derives the stage rather than trusting FAILED, so a question
    that failed before writing anything starts again at PENDING and finishes.
    """
    directory = tmp_path / "empty"
    directory.mkdir()
    orchestrate(batch, operator, str(directory), extra=APPLY_SLICE)
    assert ReseedLedger.objects.get(question=stub).stage == \
        ReseedLedger.STAGE_FAILED

    orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                extra=APPLY_SLICE)

    stub.refresh_from_db()
    assert ReseedLedger.objects.get(question=stub).stage == \
        ReseedLedger.STAGE_COMPLETE
    assert RemediationAction.objects.filter(question=stub).count() == 2


@pytest.mark.django_db
def test_rerunning_a_complete_slice_changes_nothing(tmp_path, stub, batch,
                                                    operator):
    content_dir = slice_files(tmp_path, stub.pk)
    orchestrate(batch, operator, content_dir, extra=APPLY_SLICE)

    stub.refresh_from_db()
    digest = pre_image.live_digest(stub)

    orchestrate(batch, operator, content_dir, extra=APPLY_SLICE)

    stub.refresh_from_db()
    assert pre_image.live_digest(stub) == digest
    assert RemediationAction.objects.filter(question=stub).count() == 2
    assert ReseedLedger.objects.get(question=stub).stage == \
        ReseedLedger.STAGE_COMPLETE


@pytest.mark.django_db
def test_an_ineligible_question_is_skipped_not_written(tmp_path, topic,
                                                       operator):
    unsafe = make_stub(topic, 9871, hidden_test_cases=[{"stdin": "1"}])
    batch = freeze(unsafe, operator, key="skip-slice")

    orchestrate(batch, operator, slice_files(tmp_path, unsafe.pk),
                extra=APPLY_SLICE)

    unsafe.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in unsafe.content
    assert unsafe.hidden_test_cases == [{"stdin": "1"}]
    assert RemediationAction.objects.count() == 0
    assert ReseedLedger.objects.count() == 0


@pytest.mark.django_db
def test_a_slice_is_small_by_construction(tmp_path, stub, batch, operator):
    from groups.management.commands import reseed_orchestrate as cmd

    with pytest.raises(CommandError, match="not a slice"):
        orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                    extra=("--limit", str(cmd.MAX_SLICE + 1)))
    with pytest.raises(CommandError, match="not a slice"):
        orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                    extra=("--limit", "0"))


@pytest.mark.django_db
def test_the_orchestrator_refuses_an_unfrozen_batch(tmp_path, stub, operator):
    batch = RemediationBatch.objects.create(
        batch_key="thawed-slice", purpose="x", created_by=operator)
    pre_image.capture(batch, stub, operator)

    with pytest.raises(CommandError, match="not CAPTURED"):
        orchestrate(batch, operator, slice_files(tmp_path, stub.pk))


@pytest.mark.django_db
def test_applying_without_stage_aliases_refuses(tmp_path, stub, batch,
                                                operator):
    """
    The coordinator's own alias writes the ledger and holds no authority over
    any question column, so it cannot stand in for the stage roles.
    """
    with pytest.raises(CommandError, match="statement-alias"):
        orchestrate(batch, operator, slice_files(tmp_path, stub.pk),
                    extra=("--apply", "--confirm"))
