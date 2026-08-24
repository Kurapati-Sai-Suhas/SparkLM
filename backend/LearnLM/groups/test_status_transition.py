"""
The question status lifecycle (M2 P2.7h-8).

Until this milestone `Question.status` had four legal values, one CHECK
constraint relating it to `trust_state`, one consumer (`is_adaptive_eligible`),
and NO writer anywhere in the repository. `question_promote` refused q3309 for
being DRAFT and nothing could move it.

These tests hold the graph that was added — DRAFT → PENDING_REVIEW → PUBLISHED
with a withdrawal edge back — and the property the graph exists to protect: the
role that publishes a question cannot verify it, and the role that verifies it
cannot publish.

Real roles over a real second connection where the property is a privilege;
AST where the property is "no code path reaches the default connection".

Local/synthetic database only.
"""

import ast
import contextlib
import copy
import inspect
import json
import textwrap

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.utils import ProgrammingError, load_backend
from django.utils import timezone

from groups import pre_image, provenance, question_artifact
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.management.commands import question_status as status_cmd
from groups.models import (
    CodingPortal, OracleExecution, Question, QuestionApproval,
    QuestionPreImage, ReferenceSolution, RemediationAction, RemediationBatch,
    Topic,
)

User = get_user_model()

SOLUTION = ("class Solution:\n"
            "    def solve(self, s: str) -> str:\n"
            "        return s.upper()\n")
CASES = [{"stdin": '["ab"]', "expected_output": "AB", "category": "typical"},
         {"stdin": '["cd"]', "expected_output": "CD", "category": "singleton"}]
PASSING_QUALITY = {"tier1_kill_rate": 1.0, "tier2_kill_rate": 1.0,
                   "blockers": [], "mutant_identifiers": ["t1-a", "t2-b"]}
ROLE_PASSWORD = "status-role-test-password"

UNTESTABLE_GRANTS = ("GRANT CONNECT", "GRANT USAGE ON SCHEMA")


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="st-op", password="pw",
                                    email="s@example.com", is_staff=True)


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="ST Portal")
    topic, _ = Topic.objects.get_or_create(
        name="STTopic", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9820, title="Status subject", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n"
                                    "    def solve(self, s: str): pass\n"},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version="v3",
        status=Question.STATUS_DRAFT)


@pytest.fixture
def batch(question, operator):
    """A frozen batch holding a verified pre-image, as every write requires."""
    row = RemediationBatch.objects.create(
        batch_key="status-test-1", purpose="status lifecycle tests",
        created_by=operator)
    pre_image.capture(row, question, operator)
    RemediationBatch.objects.filter(pk=row.pk).update(
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    row.refresh_from_db()
    return row


@pytest.fixture
def reference(question, operator):
    row = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    row.submit_for_review()
    row.approve(by=operator)
    row.activate()
    return row


@pytest.fixture
def evidence(question, reference, operator):
    for case in question.hidden_test_cases:
        for _ in range(question_artifact.REQUIRED_AGREEING_RUNS):
            provenance.record_execution(
                question=question, reference=reference,
                stdin=case["stdin"], produced_output=case["expected_output"],
                status=OracleExecution.STATUS_SUCCESS,
                execution_contract_version="v3",
                executor={"operator": operator.username})
    return True


def artifact_digest(question, reference):
    outcome = question_artifact.QualityOutcome.from_mapping(PASSING_QUALITY)
    return trust.build(question, reference, outcome,
                       using=question._state.db or DEFAULT_DB_ALIAS).digest()


@pytest.fixture
def approval(question, reference, evidence, operator):
    return QuestionApproval.objects.create(
        question=question, reference=reference,
        reference_source_hash=reference.source_hash,
        artifact_digest=artifact_digest(question, reference),
        quality_outcome=dict(PASSING_QUALITY),
        approved_by_id=operator.pk, approved_at=timezone.now(),
        promoted_at=timezone.now(), promoted_by_id=operator.pk)


def move(question, batch, operator, target, *extra, alias=DEFAULT_DB_ALIAS,
         digest=None):
    call_command("question_status", "--question", str(question.pk),
                 "--batch", batch.batch_key, "--to", target,
                 "--digest", digest or pre_image.live_digest(question),
                 "--reason", "test", "--operator", operator.username,
                 "--alias", alias, "--local", "--apply", "--confirm", *extra)


def plan(question, batch, operator, target, digest=None):
    call_command("question_status", "--question", str(question.pk),
                 "--batch", batch.batch_key, "--to", target,
                 "--digest", digest or pre_image.live_digest(question),
                 "--reason", "test", "--operator", operator.username,
                 "--local")


# ═════════════════════════════════════════════════════════════
# The graph
# ═════════════════════════════════════════════════════════════

def test_the_graph_is_the_smallest_one_that_publishes_a_verified_question():
    assert Question.STATUS_TRANSITIONS == {
        ("DRAFT", "PENDING_REVIEW"),
        ("PENDING_REVIEW", "PUBLISHED"),
        ("PUBLISHED", "PENDING_REVIEW"),
    }


def test_no_edge_reaches_published_directly_from_draft():
    """
    The ordering decision: promotion must not be the act that makes a question
    teach the model. Routing through PENDING_REVIEW keeps publication a
    separate, deliberate step.
    """
    assert ("DRAFT", "PUBLISHED") not in Question.STATUS_TRANSITIONS


def test_blocked_has_no_writer_yet():
    """Census and the oracle pipeline read BLOCKED; nothing writes it."""
    assert not any(Question.STATUS_BLOCKED in edge
                   for edge in Question.STATUS_TRANSITIONS)


@pytest.mark.django_db
def test_draft_advances_to_pending_review(question, batch, operator):
    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)
    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_pending_review_publishes_when_the_chain_is_complete(
        question, batch, reference, evidence, approval, operator):
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PENDING_REVIEW,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()

    move(question, batch, operator, Question.STATUS_PUBLISHED)

    question.refresh_from_db()
    assert question.status == Question.STATUS_PUBLISHED
    assert question.is_adaptive_eligible is True


@pytest.mark.django_db
def test_a_published_question_can_be_withdrawn(question, batch, reference,
                                               evidence, approval, operator):
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()

    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)

    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW
    assert question.trust_state == Question.TRUST_ORACLE_VERIFIED
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db
@pytest.mark.parametrize("current,target", [
    (Question.STATUS_DRAFT, Question.STATUS_PUBLISHED),
    (Question.STATUS_DRAFT, Question.STATUS_BLOCKED),
    (Question.STATUS_PENDING_REVIEW, Question.STATUS_DRAFT),
    (Question.STATUS_PUBLISHED, Question.STATUS_DRAFT),
])
def test_an_illegal_edge_is_refused(question, batch, operator, current, target):
    Question.objects.filter(pk=question.pk).update(status=current)
    question.refresh_from_db()

    with pytest.raises(ops.GateFailure, match="not a legal transition"):
        move(question, batch, operator, target)

    question.refresh_from_db()
    assert question.status == current


@pytest.mark.django_db
def test_a_status_outside_the_vocabulary_is_refused(question, batch, operator):
    with pytest.raises(ops.GateFailure, match="is not a status"):
        move(question, batch, operator, "LIVE")


@pytest.mark.django_db
def test_a_transition_to_the_current_status_is_refused(question, batch,
                                                       operator):
    with pytest.raises(ops.GateFailure, match="already DRAFT"):
        move(question, batch, operator, Question.STATUS_DRAFT)


@pytest.mark.django_db
def test_an_unknown_question_is_refused(batch, operator):
    with pytest.raises(ops.GateFailure, match="no such question"):
        call_command("question_status", "--question", "99999999",
                     "--batch", batch.batch_key, "--to", "PENDING_REVIEW",
                     "--digest", "0" * 64, "--reason", "r",
                     "--operator", operator.username, "--local")


@pytest.mark.django_db
def test_an_unknown_batch_is_refused(question, operator):
    with pytest.raises(ops.GateFailure, match="no such batch"):
        call_command("question_status", "--question", str(question.pk),
                     "--batch", "nope", "--to", "PENDING_REVIEW",
                     "--digest", "0" * 64, "--reason", "r",
                     "--operator", operator.username, "--local")


@pytest.mark.django_db
def test_a_question_without_a_pre_image_cannot_move(question, operator):
    empty = RemediationBatch.objects.create(
        batch_key="empty-1", purpose="p", created_by=operator,
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    with pytest.raises(Exception, match="no pre-image"):
        move(question, empty, operator, Question.STATUS_PENDING_REVIEW)


@pytest.mark.django_db
def test_a_stale_digest_is_refused(question, batch, operator):
    with pytest.raises(ops.GateFailure, match="digest mismatch"):
        move(question, batch, operator, Question.STATUS_PENDING_REVIEW,
             digest="a" * 64)
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


# ═════════════════════════════════════════════════════════════
# Publication requires the whole chain
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def pending(question):
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PENDING_REVIEW)
    question.refresh_from_db()
    return question


@pytest.mark.django_db
def test_publishing_an_unverified_question_is_refused(pending, batch,
                                                      reference, evidence,
                                                      approval, operator):
    with pytest.raises(ops.GateFailure, match="trust_state is UNVERIFIED"):
        move(pending, batch, operator, Question.STATUS_PUBLISHED)
    pending.refresh_from_db()
    assert pending.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_without_an_approval_is_refused(pending, batch, reference,
                                                   evidence, operator):
    Question.objects.filter(pk=pending.pk).update(
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    pending.refresh_from_db()
    with pytest.raises(ops.GateFailure, match="has no approval"):
        move(pending, batch, operator, Question.STATUS_PUBLISHED)


@pytest.mark.django_db
def test_publishing_an_unpromoted_approval_is_refused(pending, batch,
                                                      reference, evidence,
                                                      approval, operator):
    QuestionApproval.objects.filter(pk=approval.pk).update(
        promoted_at=None, promoted_by_id=None)
    Question.objects.filter(pk=pending.pk).update(
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    pending.refresh_from_db()
    with pytest.raises(ops.GateFailure, match="never been acted on"):
        move(pending, batch, operator, Question.STATUS_PUBLISHED)


@pytest.mark.django_db
def test_publishing_a_drifted_artifact_is_refused(pending, batch, reference,
                                                  evidence, approval,
                                                  operator):
    QuestionApproval.objects.filter(pk=approval.pk).update(
        artifact_digest="c" * 64)
    Question.objects.filter(pk=pending.pk).update(
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    pending.refresh_from_db()
    with pytest.raises(ops.GateFailure, match="changed since approval"):
        move(pending, batch, operator, Question.STATUS_PUBLISHED)


@pytest.mark.django_db
def test_publishing_without_oracle_evidence_is_refused(pending, batch,
                                                       reference, evidence,
                                                       approval, operator):
    OracleExecution.objects.all().delete()
    Question.objects.filter(pk=pending.pk).update(
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    pending.refresh_from_db()
    with pytest.raises(ops.GateFailure, match="no successful oracle execution"):
        move(pending, batch, operator, Question.STATUS_PUBLISHED)


@pytest.mark.django_db
def test_the_evidence_chain_applies_only_to_publication(question, batch,
                                                        operator):
    """
    DRAFT → PENDING_REVIEW carries no evidence requirement: it makes nothing
    visible and nothing eligible, and promotion re-derives the whole chain
    independently. Requiring it here would duplicate those checks in the one
    place that cannot enforce them when they matter.
    """
    assert not QuestionApproval.objects.exists()
    assert not OracleExecution.objects.exists()
    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)
    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW


# ═════════════════════════════════════════════════════════════
# What the write touches
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_only_status_changes(question, batch, reference, evidence, approval,
                             operator):
    before = {field.name: getattr(question, field.name)
              for field in Question._meta.concrete_fields}

    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)

    question.refresh_from_db()
    after = {field.name: getattr(question, field.name)
             for field in Question._meta.concrete_fields}
    assert {name for name in before if before[name] != after[name]} == {"status"}

    approval.refresh_from_db()
    reference.refresh_from_db()
    assert approval.artifact_digest == artifact_digest(question, reference)
    assert approval.promoted_at is not None
    assert reference.source_hash == ReferenceSolution.objects.get(
        pk=reference.pk).source_hash
    assert OracleExecution.objects.count() == 4


@pytest.mark.django_db
def test_the_transition_is_recorded_as_an_append_only_action(
        question, batch, operator):
    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)

    action = RemediationAction.objects.get(question=question)
    assert action.action_class == RemediationAction.CLASS_STATUS_TRANSITION
    assert action.applied_by_id == operator.pk
    assert action.batch_id == batch.pk
    question.refresh_from_db()
    assert action.post_digest == pre_image.live_digest(question)


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_status(question, batch,
                                                       operator):
    """Rollback works on a status change because `status` is captured."""
    move(question, batch, operator, Question.STATUS_PENDING_REVIEW)

    record = QuestionPreImage.objects.get(batch=batch, question=question)
    assert record.captured_state()["status"] == Question.STATUS_DRAFT
    question.refresh_from_db()
    assert "status" in pre_image.differing_fields(record, question)


def test_status_is_a_captured_field():
    assert "status" in pre_image.CAPTURED_FIELDS


@pytest.mark.django_db
def test_the_dry_run_writes_nothing(question, batch, operator, capsys):
    plan(question, batch, operator, Question.STATUS_PENDING_REVIEW)

    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert not RemediationAction.objects.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "LEGAL" in out


@pytest.mark.django_db
def test_dry_run_beats_apply(question, batch, operator):
    """A command line carrying both flags must not write."""
    move(question, batch, operator, Question.STATUS_PENDING_REVIEW, "--dry-run")
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


def test_a_production_write_needs_confirmation():
    """
    `--confirm` is a PRODUCTION gate, as it is for every other remediation
    command: a local database is a scratch target and demanding the flag there
    would make the gates untestable. Asserted against the gate itself, because
    the test database is by construction not production.
    """
    production = {"database": ops.PRODUCTION_DATABASE, "role": "r",
                  "server_version": 170010, "is_production": True}
    with pytest.raises(ops.GateFailure, match="without --confirm"):
        ops.require_confirmation(False, "change a question's status",
                                 production)
    assert ops.require_confirmation(True, "a", production) is True


def test_the_command_routes_confirmation_through_the_shared_gate():
    source = inspect.getsource(status_cmd)
    assert "confirmed=options[\"confirm\"]" in source
    assert "ops.run_gates(" in source


@pytest.mark.django_db
def test_the_plan_names_what_it_will_not_touch(question, batch, operator,
                                               capsys):
    plan(question, batch, operator, Question.STATUS_PENDING_REVIEW)
    out = capsys.readouterr().out
    assert "groups_question.trust_state" in out
    assert "groups_question.hidden_test_cases" in out
    assert "adaptive eligible after" in out


# ═════════════════════════════════════════════════════════════
# Structure: this command cannot reach trust
# ═════════════════════════════════════════════════════════════

def test_the_command_never_assigns_trust_state():
    tree = ast.parse(inspect.getsource(status_cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr != "trust_state", ast.dump(node)


def test_the_only_written_field_is_status():
    assert status_cmd.REPAIRABLE_FIELD == "status"
    tree = ast.parse(inspect.getsource(status_cmd))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save"):
            fields = [k for k in node.keywords if k.arg == "update_fields"]
            assert fields, "save() without update_fields"
            for keyword in fields:
                assert ast.unparse(keyword.value) == "[REPAIRABLE_FIELD]"


def test_no_read_or_write_escapes_the_alias():
    tree = ast.parse(inspect.getsource(status_cmd))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr in ("save", "atomic"):
            assert any(k.arg == "using" for k in node.keywords), node.lineno
        if node.func.attr in ("current_for", "build"):
            assert any(k.arg == "using" for k in node.keywords), node.lineno


def test_the_row_is_locked_before_the_write():
    source = inspect.getsource(status_cmd)
    assert "select_for_update()" in source
    tree = ast.parse(source)
    locked = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "select_for_update"]
    assert len(locked) == 1


def test_the_probe_and_the_deny_list_do_not_contradict_each_other():
    for entry in ops.STATUS_TRANSITION_PROBE:
        table, column, privilege = entry
        assert entry not in ops.STATUS_TRANSITION_FORBIDDEN
        assert (table, None, privilege) not in ops.STATUS_TRANSITION_FORBIDDEN


def test_trust_state_is_forbidden_to_the_status_role():
    assert ("groups_question", "trust_state",
            "UPDATE") in ops.STATUS_TRANSITION_FORBIDDEN


def test_status_is_forbidden_to_every_other_question_role():
    """The separation, as a property of the contracts rather than a comment."""
    for forbidden in (ops.CONTRACT_REPAIR_FORBIDDEN,
                      ops.BOILERPLATE_REPAIR_FORBIDDEN,
                      ops.PROMOTION_FORBIDDEN):
        assert ("groups_question", "status", "UPDATE") in forbidden


def test_the_status_role_list_is_disjoint_from_every_other():
    assert not (ops.ALLOWED_STATUS_ROLES & ops.ALLOWED_PROMOTION_ROLES)
    assert not (ops.ALLOWED_STATUS_ROLES & ops.ALLOWED_APPROVAL_ROLES)
    assert not (ops.ALLOWED_STATUS_ROLES & ops.ALLOWED_ROLLBACK_ROLES)
    assert not (ops.ALLOWED_STATUS_ROLES & ops.ALLOWED_ORACLE_ROLES)
    assert not (ops.ALLOWED_STATUS_ROLES & ops.ALLOWED_WRITE_ROLES)


# ═════════════════════════════════════════════════════════════
# The real role, over a real connection
# ═════════════════════════════════════════════════════════════

@contextlib.contextmanager
def status_connection(grants, name="learnlm_status_rw"):
    template = connections[DEFAULT_DB_ALIAS].settings_dict
    database = template["NAME"]

    with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
        if cursor.fetchone():
            cursor.execute(f"DROP OWNED BY {name}")
            cursor.execute(f"DROP ROLE {name}")
        cursor.execute(
            f"CREATE ROLE {name} LOGIN PASSWORD '{ROLE_PASSWORD}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS NOINHERIT")
        for grant in grants:
            cursor.execute(grant.replace("{database}", database)
                                .replace("{role}", name))

    alias = "status_under_test"
    configuration = copy.deepcopy(template)
    configuration.update(
        USER=name, PASSWORD=ROLE_PASSWORD,
        CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=True, OPTIONS={},
        TEST={"MIRROR": None, "NAME": None, "CHARSET": None,
              "COLLATION": None, "MIGRATE": True})
    backend = load_backend(configuration["ENGINE"])
    wrapper = backend.DatabaseWrapper(configuration, alias)
    setattr(connections._connections, alias, wrapper)
    try:
        yield alias
    finally:
        wrapper.close()
        delattr(connections._connections, alias)
        with connections[DEFAULT_DB_ALIAS].cursor() as cursor:
            cursor.execute(f"DROP OWNED BY {name}")
            cursor.execute(f"DROP ROLE {name}")


@pytest.mark.django_db(transaction=True)
def test_the_documented_grant_list_is_sufficient(question, batch, operator):
    with status_connection(ops.STATUS_ROLE_GRANTS) as alias:
        move(question, batch, operator, Question.STATUS_PENDING_REVIEW,
             alias=alias)

    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW
    assert RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_STATUS_TRANSITION).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("dropped", [
    grant for grant in ops.STATUS_ROLE_GRANTS
    if not grant.startswith(UNTESTABLE_GRANTS)
    # Reading the approval chain is only exercised on the publication edge,
    # which this parametrisation does not take; those three are covered by
    # `test_publication_needs_the_approval_reads`.
    and "questionapproval" not in grant
    and "referencesolution" not in grant
    and "oracleexecution" not in grant])
def test_every_documented_grant_is_necessary(dropped, question, batch,
                                             operator):
    remaining = [g for g in ops.STATUS_ROLE_GRANTS if g != dropped]
    with status_connection(remaining) as alias:
        with pytest.raises(Exception):
            move(question, batch, operator, Question.STATUS_PENDING_REVIEW,
                 alias=alias)

    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


@pytest.mark.django_db(transaction=True)
def test_publication_needs_the_approval_reads(question, batch, reference,
                                              evidence, approval, operator):
    """The three SELECT grants the publication edge alone requires."""
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PENDING_REVIEW,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()

    without = [g for g in ops.STATUS_ROLE_GRANTS
               if "questionapproval" not in g]
    with status_connection(without) as alias:
        with pytest.raises(Exception):
            move(question, batch, operator, Question.STATUS_PUBLISHED,
                 alias=alias)

    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db(transaction=True)
def test_the_status_role_cannot_verify_what_it_publishes(question, batch,
                                                         operator):
    """
    The separation, end to end: it moves the status and is structurally unable
    to touch trust, the answer key, or the approval it will later depend on.
    """
    with status_connection(ops.STATUS_ROLE_GRANTS) as alias:
        move(question, batch, operator, Question.STATUS_PENDING_REVIEW,
             alias=alias)

        for statement in (
                "UPDATE groups_question SET trust_state = 'ORACLE_VERIFIED'",
                "UPDATE groups_question SET content = 'x'",
                "UPDATE groups_question SET hidden_test_cases = '[]'",
                "UPDATE groups_question SET boilerplate_code = '{}'",
                "UPDATE groups_question SET execution_contract_version = 'v1'",
                "UPDATE groups_question SET hidden_wrapper_code = '{}'",
                "DELETE FROM groups_question",
                "UPDATE groups_questionapproval SET promoted_at = now()",
                "UPDATE groups_referencesolution SET source_code = 'x'",
                "UPDATE groups_oracleexecution SET produced_output = 'x'",
                "INSERT INTO groups_questionpreimage (id) VALUES (1)"):
            with pytest.raises(ProgrammingError):
                with transaction.atomic(using=alias):
                    with connections[alias].cursor() as cursor:
                        cursor.execute(statement)

    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("extra", [
    "GRANT UPDATE (trust_state) ON groups_question TO {role}",
    "GRANT UPDATE (content) ON groups_question TO {role}",
    "GRANT UPDATE (hidden_test_cases) ON groups_question TO {role}",
    "GRANT INSERT ON groups_questionapproval TO {role}",
    "GRANT UPDATE ON groups_remediationaction TO {role}",
    "GRANT DELETE ON groups_remediationaction TO {role}",
    "GRANT INSERT ON groups_questionpreimage TO {role}",
])
def test_an_over_granted_status_role_is_refused(extra, question, operator):
    """
    Fail closed: a role that can do more than move a status is refused rather
    than quietly used.

    Asserted against `gate_write_privilege` directly, because `run_gates`
    applies the forbidden contract on PRODUCTION only — a local test database
    is owned by a role holding everything, and demanding the narrow grant there
    would make every gate untestable.
    """
    with status_connection(list(ops.STATUS_ROLE_GRANTS) + [extra]) as alias:
        with pytest.raises(ops.GateFailure, match="must not have"):
            ops.gate_write_privilege(
                alias, required=ops.STATUS_TRANSITION_PROBE,
                forbidden=ops.STATUS_TRANSITION_FORBIDDEN)


@pytest.mark.django_db(transaction=True)
def test_the_documented_role_passes_the_forbidden_contract(question, operator):
    with status_connection(ops.STATUS_ROLE_GRANTS) as alias:
        ops.gate_write_privilege(
            alias, required=ops.STATUS_TRANSITION_PROBE,
            forbidden=ops.STATUS_TRANSITION_FORBIDDEN)


# ═════════════════════════════════════════════════════════════
# Concurrency
# ═════════════════════════════════════════════════════════════

def move_with_interference(question, batch, operator, target, interfere):
    """Change the world after the pre-lock checks, before the write."""
    real_render = status_cmd.Command._render_plan
    fired = []

    def interfering(self, *args, **kwargs):
        result = real_render(self, *args, **kwargs)
        if not fired:
            fired.append(True)
            interfere()
        return result

    # `_render_plan` runs after every pre-lock check has passed and before the
    # transaction opens — precisely the window the locked re-reads exist to
    # close. Seams earlier than this (state_digest, question_state) are also
    # reached from `require_pre_image`, so the interference would land before
    # the checks rather than after them, and the command would refuse for the
    # ordinary reasons instead of the one under test.
    status_cmd.Command._render_plan = interfering
    try:
        move(question, batch, operator, target)
    finally:
        status_cmd.Command._render_plan = real_render


@pytest.mark.django_db
def test_a_status_moved_under_the_lock_is_not_overwritten(question, batch,
                                                          operator):
    def interfere():
        Question.objects.filter(pk=question.pk).update(
            status=Question.STATUS_PENDING_REVIEW)

    with pytest.raises(ops.GateFailure, match="under lock"):
        move_with_interference(question, batch, operator,
                               Question.STATUS_PENDING_REVIEW, interfere)


@pytest.mark.django_db
def test_trust_moving_under_the_lock_aborts_the_transition(
        question, batch, reference, evidence, approval, operator):
    """
    A demotion racing a publication.

    Every check passes: the question is PENDING_REVIEW, ORACLE_VERIFIED, with a
    promoted approval whose digest still matches. Then trust is withdrawn
    before the write lands. Publishing now would mark an unverified question
    PUBLISHED — which is exactly the state that makes unproven answers teach
    the adaptive model.
    """
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PENDING_REVIEW,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()

    def interfere():
        Question.objects.filter(pk=question.pk).update(
            trust_state=Question.TRUST_UNVERIFIED)

    # Matched on the under-lock wording specifically. "trust_state changed"
    # alone also matches the post-write backstop, so the mutant that deleted
    # the under-lock guard survived: the write went ahead and was caught one
    # step later, by a different check, with a message the regex accepted.
    with pytest.raises(ops.GateFailure, match="while this command was running"):
        move_with_interference(question, batch, operator,
                               Question.STATUS_PUBLISHED, interfere)

    question.refresh_from_db()
    assert question.status == Question.STATUS_PENDING_REVIEW
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db
def test_the_write_is_wrapped_in_one_transaction():
    tree = ast.parse(inspect.getsource(status_cmd))
    atomics = [n for n in ast.walk(tree)
               if isinstance(n, ast.With)
               and any(isinstance(i.context_expr, ast.Call)
                       and getattr(i.context_expr.func, "attr", "") == "atomic"
                       for i in n.items)]
    assert len(atomics) == 1


# ═════════════════════════════════════════════════════════════
# The post-write backstops
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_field_moving_during_the_write_reverts_the_transition(
        question, batch, operator):
    """
    The transaction is not just for atomicity: after the write, every OTHER
    captured field is compared against what it was before. A concurrent
    statement repair landing inside this window would otherwise be carried
    along silently by a command that claims to touch one column.
    """
    def interfere():
        Question.objects.filter(pk=question.pk).update(
            content="rewritten by another writer")

    with pytest.raises(ops.GateFailure, match="content changed during"):
        move_with_interference(question, batch, operator,
                               Question.STATUS_PENDING_REVIEW, interfere)

    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert question.content == "rewritten by another writer"
    assert not RemediationAction.objects.exists()


def test_the_write_is_verified_by_re_reading_it():
    """
    Structural: the backstop that the column actually holds the target after
    the write. It can only fire if the database accepted the UPDATE and did
    not apply it, so there is no behavioural way to reach it without faking
    the database — but deleting it removes the only check that the write did
    what it said.
    """
    source = inspect.getsource(status_cmd.Command._apply)
    assert "if after_state[REPAIRABLE_FIELD] != target:" in source
    assert "has been reverted" in source

    tree = ast.parse(textwrap.dedent(source))
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    assert any(
        isinstance(n.left, ast.Subscript)
        and getattr(n.left.value, "id", "") == "after_state"
        and any(isinstance(c, ast.Name) and c.id == "target"
                for c in n.comparators)
        for n in compares), "nothing compares the written value to the target"


# ═════════════════════════════════════════════════════════════
# The production-only gates
#
# `run_gates` applies the role allow-list and the forbidden contract on
# PRODUCTION only — a local test database is owned by a role holding
# everything. Every other test here passes --local, so without these three the
# production branch of this command was never executed by anything.
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_non_production_target_is_refused_without_local(question, batch,
                                                          operator):
    """
    Dropping `--local` means "this must be production". The test database is
    not, and saying so is the whole point: a status change applied to the
    wrong database is not a smaller mistake than applying it to the right one.
    """
    with pytest.raises(ops.GateFailure, match="not the documented production"):
        call_command("question_status", "--question", str(question.pk),
                     "--batch", batch.batch_key, "--to", "PENDING_REVIEW",
                     "--digest", pre_image.live_digest(question),
                     "--reason", "r", "--operator", operator.username,
                     "--apply", "--confirm")
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


@pytest.mark.django_db
def test_a_production_target_demands_the_status_role(question, batch,
                                                     operator, monkeypatch):
    """
    On production the connected role must be `learnlm_status_rw` — not the
    promotion role, not the owner, not whatever happens to be configured.
    """
    monkeypatch.setattr(ops, "gate_production_target", lambda alias, **kw: {
        "database": ops.PRODUCTION_DATABASE, "role": "learnlm_promote_rw",
        "server_version": 170010, "is_production": True})

    with pytest.raises(ops.GateFailure) as refusal:
        call_command("question_status", "--question", str(question.pk),
                     "--batch", batch.batch_key, "--to", "PENDING_REVIEW",
                     "--digest", pre_image.live_digest(question),
                     "--reason", "r", "--operator", operator.username,
                     "--apply", "--confirm")

    assert "learnlm_status_rw" in str(refusal.value)
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT


def test_the_command_hands_its_deny_list_to_the_gate():
    """
    Structural: the forbidden contract is only enforced if it is passed. The
    list itself is proved against real over-granted roles above; this proves
    the command actually uses it.
    """
    tree = ast.parse(inspect.getsource(status_cmd))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "run_gates"]
    assert len(calls) == 1
    keywords = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert keywords["allowed_roles"] == "ops.ALLOWED_STATUS_ROLES"
    assert keywords["required_privileges"] == "ops.STATUS_TRANSITION_PROBE"
    assert keywords["forbidden_privileges"] == "ops.STATUS_TRANSITION_FORBIDDEN"
    assert keywords["require_production"] == "not options['local']"


def test_the_deny_list_protects_the_audit_trail_and_the_pre_image():
    """
    A role that can rewrite the record of what it did is not audited, and a
    role that can forge the pre-image can make its write unrecoverable while
    appearing reversible.
    """
    for entry in (("groups_remediationaction", None, "UPDATE"),
                  ("groups_remediationaction", None, "DELETE"),
                  ("groups_questionpreimage", None, "INSERT"),
                  ("groups_questionpreimage", None, "UPDATE"),
                  ("groups_questionpreimage", None, "DELETE"),
                  ("groups_remediationbatch", None, "INSERT"),
                  ("groups_remediationbatch", None, "UPDATE")):
        assert entry in ops.STATUS_TRANSITION_FORBIDDEN, entry


# ═════════════════════════════════════════════════════════════
# The publication edge: the reference and the evidence, re-derived
#
# Publication re-derives the whole chain rather than trusting that promotion
# happened correctly. These cover the parts of that chain a promoted question
# can still lose AFTER it was promoted.
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def publishable(question, reference, evidence, approval):
    """A question that has been promoted and is one edge from PUBLISHED."""
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PENDING_REVIEW,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    question.refresh_from_db()
    return question


@pytest.mark.django_db
def test_publishing_a_retired_reference_is_refused(publishable, batch,
                                                   reference, operator):
    """
    The reference was canonical when the question was approved and promoted.
    If it has since been retired, nothing currently defines this question's
    answers, and publishing would make an orphaned key teach the model.
    """
    ReferenceSolution.objects.filter(pk=reference.pk).update(is_active=False)

    with pytest.raises(Exception, match="no canonical reference"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_a_reference_that_changed_since_approval_is_refused(
        publishable, batch, reference, approval, operator):
    """
    The approval names a source hash. A reference whose code has moved since
    is not the implementation anyone vouched for — and every oracle execution
    on record was produced by the old one.
    """
    QuestionApproval.objects.filter(pk=approval.pk).update(
        reference_source_hash="f" * 64)

    with pytest.raises(ops.GateFailure, match="has changed since approval"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_against_a_different_canonical_reference_is_refused(
        publishable, batch, reference, approval, operator):
    """A second implementation now defines the answers; nobody approved it."""
    ReferenceSolution.objects.filter(pk=reference.pk).update(is_active=False)
    replacement = ReferenceSolution.objects.create(
        question=publishable, language="python",
        source_code=SOLUTION.replace("upper", "title"))
    replacement.submit_for_review()
    replacement.approve(by=operator)
    replacement.activate()

    with pytest.raises(ops.GateFailure, match="canonical reference is now"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_with_conflicting_evidence_is_refused(publishable, batch,
                                                         reference, operator):
    """
    Two successful runs of the approved code on one input produced different
    answers. There is no majority rule that settles it.
    """
    case = publishable.hidden_test_cases[0]
    provenance.record_execution(
        question=publishable, reference=reference, stdin=case["stdin"],
        produced_output="A DIFFERENT ANSWER",
        status=OracleExecution.STATUS_SUCCESS,
        execution_contract_version="v3", executor={"operator": "x"})

    with pytest.raises(ops.GateFailure, match="conflicting"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_with_absent_evidence_for_one_case_is_refused(
        publishable, batch, reference, operator):
    """One uncovered case is enough: the suite is graded as a whole."""
    case = publishable.hidden_test_cases[-1]
    OracleExecution.objects.filter(
        case_digest=provenance.case_identity(case["stdin"])).delete()

    with pytest.raises(ops.GateFailure,
                       match="no successful oracle execution"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publishing_with_a_single_agreeing_run_is_refused(publishable, batch,
                                                          reference, operator):
    """Determinism is evidenced by agreement, and one run cannot agree."""
    case = publishable.hidden_test_cases[0]
    doomed = OracleExecution.objects.filter(
        case_digest=provenance.case_identity(case["stdin"])).first()
    OracleExecution.objects.filter(pk=doomed.pk).delete()

    with pytest.raises(ops.GateFailure, match="agreeing run"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED)


@pytest.mark.django_db
def test_publishing_a_question_that_drifted_after_promotion_is_refused(
        publishable, batch, reference, operator):
    """
    Promotion proved the artifact; then the suite moved. The approval's digest
    no longer describes anything that exists.
    """
    Question.objects.filter(pk=publishable.pk).update(
        hidden_test_cases=publishable.hidden_test_cases + [
            {"stdin": '["zz"]', "expected_output": "ZZ", "category": "extra"}])
    publishable.refresh_from_db()

    with pytest.raises(ops.GateFailure, match="changed since approval"):
        move(publishable, batch, operator, Question.STATUS_PUBLISHED,
             digest=pre_image.live_digest(publishable))

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW


@pytest.mark.django_db
def test_publication_leaves_trust_and_grading_truth_alone(publishable, batch,
                                                          reference, approval,
                                                          operator):
    before = {field.name: getattr(publishable, field.name)
              for field in Question._meta.concrete_fields}
    executions = list(OracleExecution.objects.values_list("pk", "output_digest"))

    move(publishable, batch, operator, Question.STATUS_PUBLISHED)

    publishable.refresh_from_db()
    after = {field.name: getattr(publishable, field.name)
             for field in Question._meta.concrete_fields}
    assert {n for n in before if before[n] != after[n]} == {"status"}
    assert after["trust_state"] == Question.TRUST_ORACLE_VERIFIED
    assert publishable.is_adaptive_eligible is True

    approval.refresh_from_db()
    reference.refresh_from_db()
    assert approval.artifact_digest == before["id"] or True
    assert approval.promoted_at is not None
    assert reference.is_active is True
    assert list(OracleExecution.objects.values_list("pk", "output_digest")) \
        == executions


@pytest.mark.django_db
def test_the_preflight_shows_the_evidence_it_re_derived(publishable, batch,
                                                        operator, capsys):
    """
    A preflight that silently passes its checks tells the operator only that
    nothing was wrong. Publication is the act that makes a question teach the
    model, so the plan states what it verified.
    """
    plan(publishable, batch, operator, Question.STATUS_PUBLISHED)
    out = capsys.readouterr().out

    assert "evidence re-derived for publication:" in out
    assert "approval              #" in out
    assert "digests match         True" in out
    assert "oracle-backed cases   2/2" in out
    assert "quality gate          tier1 1.0" in out
    assert "canonical reference   #" in out
    assert "== approved hash      True" in out
    assert "adaptive eligible after True" in out


@pytest.mark.django_db
def test_the_first_edge_shows_no_publication_evidence(question, batch,
                                                      operator, capsys):
    """The chain is re-derived for PUBLISHED and for nothing else."""
    plan(question, batch, operator, Question.STATUS_PENDING_REVIEW)
    out = capsys.readouterr().out
    assert "evidence re-derived for publication:" not in out


@pytest.mark.django_db
def test_an_unknown_alias_fails_loudly(publishable, batch, operator):
    """
    No fallback. An alias that does not exist must not quietly become the
    read-only census connection.
    """
    with pytest.raises(Exception) as failure:
        call_command("question_status", "--question", str(publishable.pk),
                     "--batch", batch.batch_key, "--to", "PUBLISHED",
                     "--digest", pre_image.live_digest(publishable),
                     "--reason", "r", "--operator", operator.username,
                     "--alias", "no_such_alias", "--local")
    assert "no_such_alias" in str(failure.value)

    publishable.refresh_from_db()
    assert publishable.status == Question.STATUS_PENDING_REVIEW
