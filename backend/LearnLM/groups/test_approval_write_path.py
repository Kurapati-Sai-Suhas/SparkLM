"""
The approval and promotion write path: alias routing and role separation
(M2 P2.7h-5).

`question_review`, `question_approve` and `question_promote` were built in
P2.7g-3, before the operator aliases existed. They used default managers and a
bare `transaction.atomic()`, so on this deployment every write would have gone
through the READ-ONLY census connection. These tests hold the routing and the
two roles apart: an approver records a judgement and must not be able to enact
it; a promoter enacts one and must not be able to author it.

Real roles where the property is a privilege; AST where the property is "no
code path reaches the default connection".

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
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, connection, connections, transaction
from django.db.utils import ProgrammingError, load_backend

from django.utils import timezone

from groups import pre_image, provenance, question_artifact
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.management.commands import question_approve as approve_cmd
from groups.management.commands import question_promote as promote_cmd
from groups.management.commands import question_review as review_cmd
from groups.models import (
    CodingPortal, OracleExecution, Question, QuestionApproval,
    ReferenceSolution, Topic,
)

User = get_user_model()

SOLUTION = ("class Solution:\n"
            "    def solve(self, s: str) -> str:\n"
            "        return s.upper()\n")
CASES = [{"stdin": '["ab"]', "expected_output": "AB", "category": "typical"},
         {"stdin": '["cd"]', "expected_output": "CD", "category": "singleton"}]


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="ap-op", password="pw",
                                    email="a@example.com", is_staff=True)


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="AP Portal")
    topic, _ = Topic.objects.get_or_create(
        name="APTopic", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9800, title="Approval subject", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version="v3",
        status=Question.STATUS_REVIEW if hasattr(Question, "STATUS_REVIEW")
        else Question.STATUS_DRAFT)


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
    """Complete oracle provenance for every case."""
    for case in question.hidden_test_cases:
        for _ in range(question_artifact.REQUIRED_AGREEING_RUNS):
            provenance.record_execution(
                question=question, reference=reference,
                stdin=case["stdin"], produced_output=case["expected_output"],
                status=OracleExecution.STATUS_SUCCESS,
                execution_contract_version="v3",
                executor={"operator": operator.username})
    return True


@pytest.fixture
def quality_report(tmp_path):
    path = tmp_path / "quality.json"
    path.write_text(json.dumps({
        "tier1_kill_rate": 1.0, "tier2_kill_rate": 1.0, "blockers": [],
        "mutant_identifiers": ["t1-a", "t2-b"]}), encoding="utf-8")
    return str(path)


def make_role(name, grants):
    with connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {name}")
        cursor.execute(f"CREATE ROLE {name} NOLOGIN")
        for grant in grants:
            cursor.execute(grant.format(role=name))
    return name


def expect_denied(role, sql, params=()):
    with pytest.raises(ProgrammingError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {role}")
                cursor.execute(sql, params)


APPROVAL_GRANTS = [
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT SELECT ON groups_referencesolution, groups_oracleexecution TO {role}",
    "GRANT SELECT, INSERT ON groups_questionapproval TO {role}",
]
PROMOTION_GRANTS = [
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT UPDATE (status, trust_state) ON groups_question TO {role}",
    "GRANT SELECT, UPDATE ON groups_questionapproval TO {role}",
    "GRANT SELECT ON groups_referencesolution, groups_oracleexecution TO {role}",
]


# ═════════════════════════════════════════════════════════════
# Alias routing
# ═════════════════════════════════════════════════════════════

def _options(module):
    parser = module.Command().create_parser("manage.py", "cmd")
    return {action.dest for action in parser._actions}


def test_all_three_commands_accept_an_alias():
    for module in (review_cmd, approve_cmd, promote_cmd):
        assert "alias" in _options(module), module.__name__


@pytest.mark.django_db
def test_the_question_read_is_routed(question, reference, evidence,
                                     quality_report, operator, monkeypatch):
    routed = []
    real = trust.resolve_question

    def recording(question_id, alias="default"):
        routed.append(alias)
        return real(question_id, alias)

    monkeypatch.setattr(trust, "resolve_question", recording)
    call_command("question_review", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report, "--alias", "default")
    assert routed == ["default"]


def test_the_trust_helper_routes_the_read_through_the_alias():
    """
    Structural, because every alias resolves to the same database in a test:
    no single run can show that a read went to `default` rather than the
    named connection. A mutation sweep dropped `.using(alias)` here and every
    behavioural test still passed.
    """
    tree = ast.parse(inspect.getsource(trust.resolve_question))
    usings = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "using"]
    assert usings, "resolve_question does not route its read"
    assert any(isinstance(call.args[0], ast.Name) and call.args[0].id == "alias"
               for call in usings if call.args)


def test_both_writers_pass_the_alias_to_the_read():
    """
    The helper defaults to `default`, so a caller that forgets the argument
    reads production through the census connection and writes elsewhere.
    """
    for module in (approve_cmd, promote_cmd, review_cmd):
        tree = ast.parse(inspect.getsource(module))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "resolve_question"]
        assert calls, module.__name__
        for call in calls:
            assert len(call.args) == 2, (
                f"{module.__name__}: resolve_question called with "
                f"{len(call.args)} argument(s); the alias is missing")


def test_no_write_escapes_the_alias():
    """
    Every save and every atomic in the two writers must name the connection.
    A bare `transaction.atomic()` opens on `default` while the write goes
    elsewhere — the defect already fixed once inside `pre_image.py`.
    """
    for module in (approve_cmd, promote_cmd):
        tree = ast.parse(inspect.getsource(module))
        saves = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "save"]
        assert saves, module.__name__
        for call in saves:
            assert "using" in {k.arg for k in call.keywords}, \
                f"{module.__name__}: {ast.dump(call)}"

        atomics = [node for node in ast.walk(tree)
                   if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "atomic"]
        assert atomics, module.__name__
        for call in atomics:
            assert "using" in {k.arg for k in call.keywords}, module.__name__


def test_cross_alias_foreign_keys_are_assigned_by_id():
    """
    The operator is resolved on `default` while the row is written through the
    operator alias; assigning the OBJECT raises a cross-database error.
    """
    for module in (approve_cmd, promote_cmd):
        source = inspect.getsource(module)
        for field in ("approved_by", "reviewed_by", "executed_by",
                      "promoted_by"):
            assert f"{field}=operator" not in source, f"{module.__name__}.{field}"


# ═════════════════════════════════════════════════════════════
# Role separation — real roles
# ═════════════════════════════════════════════════════════════

def test_the_two_role_lists_are_disjoint_from_each_other_and_the_rest():
    lists = (ops.ALLOWED_APPROVAL_ROLES, ops.ALLOWED_PROMOTION_ROLES,
             ops.ALLOWED_ORACLE_ROLES, ops.ALLOWED_REMEDIATION_ROLES,
             ops.ALLOWED_HIDDEN_TEST_ROLES, ops.ALLOWED_CONTRACT_ROLES,
             ops.ALLOWED_BOILERPLATE_ROLES, ops.ALLOWED_WRITE_ROLES)
    combined = set().union(*lists)
    assert len(combined) == sum(len(item) for item in lists)


def test_the_probes_name_what_each_command_writes():
    assert ops.APPROVAL_PROBE == (
        ("groups_questionapproval", None, "INSERT"),)
    # `status` was here and is not any more: `question_promote` refuses a
    # DRAFT rather than advancing one, so demanding UPDATE on `status` asked
    # for a privilege nothing uses and would have let a promoter publish
    # (M2 P2.7h-7). The promotion stamp columns are here for the opposite
    # reason — the command writes them and the old probe never named them.
    assert set(ops.PROMOTION_PROBE) == {
        ("groups_question", "trust_state", "UPDATE"),
        ("groups_questionapproval", "promoted_at", "UPDATE"),
        ("groups_questionapproval", "promoted_by_id", "UPDATE")}


def test_an_approver_may_not_enact_its_own_judgement():
    forbidden = {(table, column, privilege)
                 for table, column, privilege in ops.APPROVAL_FORBIDDEN}
    assert ("groups_question", None, "UPDATE") in forbidden
    assert ("groups_questionapproval", None, "UPDATE") in forbidden
    assert ("groups_referencesolution", None, "UPDATE") in forbidden


def test_a_promoter_may_not_author_a_judgement():
    forbidden = {(table, column, privilege)
                 for table, column, privilege in ops.PROMOTION_FORBIDDEN}
    assert ("groups_questionapproval", None, "INSERT") in forbidden
    assert ("groups_question", "hidden_test_cases", "UPDATE") in forbidden
    assert ("groups_question", "content", "UPDATE") in forbidden
    assert ("groups_oracleexecution", None, "INSERT") in forbidden


@pytest.mark.django_db(transaction=False)
def test_the_approval_role_cannot_touch_the_question(question):
    role = make_role("test_ap_approve", APPROVAL_GRANTS)
    for column, value in (("trust_state", "'ORACLE_VERIFIED'"),
                          ("status", "'PUBLISHED'"),
                          ("content", "'tampered'"),
                          ("hidden_test_cases", "'[]'::jsonb")):
        expect_denied(role, f"update groups_question set {column} = {value} "
                            f"where id = %s", [question.pk])


@pytest.mark.django_db(transaction=False)
def test_the_promotion_role_cannot_author_an_approval(question, operator):
    role = make_role("test_ap_promote", PROMOTION_GRANTS)
    expect_denied(
        role,
        "insert into groups_questionapproval (question_id, reference_id, "
        "reference_source_hash, artifact_digest, artifact_schema_version, "
        "quality_outcome, reviewed_by_id, reviewed_at, approved_by_id, "
        "approved_at, created_at) values (%s, 1, 'x', 'y', 1, '{}'::jsonb, "
        "%s, now(), %s, now(), now())",
        [question.pk, operator.pk, operator.pk])


@pytest.mark.django_db(transaction=False)
def test_the_promotion_role_cannot_touch_grading_truth(question):
    role = make_role("test_ap_promote2", PROMOTION_GRANTS)
    for column, value in (("content", "'tampered'"),
                          ("hidden_test_cases", "'[]'::jsonb"),
                          ("boilerplate_code", "'{}'::jsonb"),
                          ("execution_contract_version", "'v1'")):
        expect_denied(role, f"update groups_question set {column} = {value} "
                            f"where id = %s", [question.pk])


@pytest.mark.django_db(transaction=False)
def test_the_promotion_role_may_move_the_trust_state(question):
    role = make_role("test_ap_promote3", PROMOTION_GRANTS)
    with connection.cursor() as cursor:
        cursor.execute("select has_column_privilege(%s, 'groups_question', "
                       "'trust_state', 'UPDATE')", [role])
        assert cursor.fetchone()[0] is True


# ═════════════════════════════════════════════════════════════
# Gate wiring
# ═════════════════════════════════════════════════════════════

def test_each_writer_names_its_own_role_list_and_probes():
    approve_source = inspect.getsource(approve_cmd)
    assert "ALLOWED_APPROVAL_ROLES" in approve_source
    assert "APPROVAL_PROBE" in approve_source
    assert "APPROVAL_FORBIDDEN" in approve_source
    assert "ALLOWED_PROMOTION_ROLES" not in approve_source

    promote_source = inspect.getsource(promote_cmd)
    assert "ALLOWED_PROMOTION_ROLES" in promote_source
    assert "PROMOTION_PROBE" in promote_source
    assert "PROMOTION_FORBIDDEN" in promote_source
    assert "ALLOWED_APPROVAL_ROLES" not in promote_source


@pytest.mark.django_db
def test_a_production_target_triggers_the_approval_role_gate(
        question, reference, evidence, quality_report, operator, monkeypatch):
    monkeypatch.setattr(
        ops, "describe_target",
        lambda alias: {"database": "neondb", "role": "learnlm_census_ro",
                       "server_version": "17.10", "is_production": True})
    with pytest.raises(CommandError, match="not an authorized role"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "0" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_a_production_target_triggers_the_promotion_role_gate(
        question, operator, monkeypatch):
    monkeypatch.setattr(
        ops, "describe_target",
        lambda alias: {"database": "neondb", "role": "learnlm_census_ro",
                       "server_version": "17.10", "is_production": True})
    with pytest.raises(CommandError, match="not an authorized role"):
        call_command("question_promote", "--question", str(question.pk),
                     "--operator", operator.username, "--confirm")
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


# ═════════════════════════════════════════════════════════════
# Review stays read-only
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_review_writes_nothing(question, reference, evidence, quality_report,
                               operator):
    before = pre_image.live_digest(question)
    call_command("question_review", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report)
    question.refresh_from_db()
    assert pre_image.live_digest(question) == before
    assert not QuestionApproval.objects.exists()


def test_review_has_no_write_call_at_all():
    tree = ast.parse(inspect.getsource(review_cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("save", "create", "update", "delete"), \
                ast.dump(node)


@pytest.mark.django_db
def test_review_refuses_an_unknown_question(operator, quality_report):
    with pytest.raises(CommandError, match="no such question"):
        call_command("question_review", "--question", "999999",
                     "--operator", operator.username,
                     "--quality-report", quality_report)


# ═════════════════════════════════════════════════════════════
# Evidence rules the approval path enforces
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_approval_refuses_without_complete_oracle_evidence(
        question, reference, quality_report, operator):
    """No executions recorded: the artifact is not approvable."""
    with pytest.raises(CommandError,
                       match="no successful oracle execution"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "0" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_approval_refuses_a_failing_quality_report(question, reference,
                                                   evidence, operator,
                                                   tmp_path):
    failing = tmp_path / "failing.json"
    failing.write_text(json.dumps({
        "tier1_kill_rate": 0.5, "tier2_kill_rate": 1.0,
        "blockers": ["a Tier-1 mutant survived"],
        "mutant_identifiers": ["t1-a"]}), encoding="utf-8")

    with pytest.raises(CommandError, match="quality gate did not pass"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", str(failing),
                     "--digest", "0" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_approval_refuses_a_digest_that_was_not_reviewed(
        question, reference, evidence, quality_report, operator):
    with pytest.raises(CommandError, match="digest mismatch"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "a" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_approval_requires_confirmation(question, reference, evidence,
                                        quality_report, operator):
    with pytest.raises(CommandError, match="without --confirm"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "0" * 64)


@pytest.mark.django_db
def test_promotion_refuses_without_an_approval(question, reference, evidence,
                                               operator):
    with pytest.raises(CommandError, match="has no approval"):
        call_command("question_promote", "--question", str(question.pk),
                     "--operator", operator.username, "--confirm")
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


def test_approval_cannot_write_a_trust_state():
    """Structural: the approver records a judgement; it never enacts one."""
    tree = ast.parse(inspect.getsource(approve_cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in ("trust_state", "status",
                                               "hidden_test_cases",
                                               "content"), target.attr


def test_promotion_cannot_create_an_approval():
    tree = ast.parse(inspect.getsource(promote_cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("create", "get_or_create"):
                raise AssertionError(ast.dump(node))
    source = inspect.getsource(promote_cmd)
    assert "QuestionApproval(" not in source


# ═════════════════════════════════════════════════════════════
# The production grant list, proved rather than asserted
#
# `ops.APPROVAL_ROLE_GRANTS` is the tuple the production DDL is generated
# from. These tests build a role from that tuple and NOTHING else, SET ROLE
# to it, and run the real command — so "these are the privileges the approval
# needs" is a demonstrated fact rather than a comment, and an over-granted
# role cannot hide behind a test that runs as the owner.
# ═════════════════════════════════════════════════════════════

#: Prerequisites a SET ROLE test cannot exercise: the connection already
#: exists, so CONNECT is never checked, and PUBLIC holds USAGE on `public` by
#: default, so granting it again is a no-op the necessity check would misread.
UNTESTABLE_GRANTS = ("GRANT CONNECT", "GRANT USAGE ON SCHEMA")


ROLE_PASSWORD = "approve-role-test-password"


@contextlib.contextmanager
def approval_connection(grants, name="learnlm_approve_rw"):
    """
    A REAL second connection, logged in as the role, built from `grants`.

    `SET ROLE` on the shared test connection was tried first and is wrong: it
    also applies the role to the operator lookup, which in production runs on
    `default` as the read-only census connection. Simulating both connections
    with one would test a deployment that does not exist, and would demand
    SELECT on `groups_user` — password hashes included — from a role whose
    entire purpose is to write one approval row.
    """
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

    alias = "approve_under_test"
    configuration = copy.deepcopy(template)
    configuration.update(
        USER=name, PASSWORD=ROLE_PASSWORD,
        # No pool, no persistence — matching the production `approve` alias.
        # A pooled connection outlives `close()`, so the role's sessions were
        # still open when the test dropped it ("invalid role OID"), and a
        # pool worker could hand back a connection opened before the GRANTs
        # were applied. An operator command is one short-lived connection.
        CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=True, OPTIONS={},
        TEST={"MIRROR": None, "NAME": None, "CHARSET": None,
              "COLLATION": None, "MIGRATE": True})
    # Registered as a wrapper rather than in `connections.settings`: Django's
    # test guard blocks connections to aliases it can see in the handler, and
    # explicitly allows dynamically created ones. This IS one — it exists for
    # the duration of a single test and is closed and dropped after it.
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


def approve_through(alias, question, operator, quality_report, digest, *extra):
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report, "--digest", digest,
                 "--alias", alias, "--confirm", *extra)


def reviewed_digest(question, reference, quality_report):
    quality = trust.load_quality_outcome(quality_report)
    return trust.build(question, reference, quality,
                       using=question._state.db).digest()


@pytest.mark.django_db(transaction=True)
def test_the_documented_grant_list_is_sufficient(question, reference, evidence,
                                                 quality_report, operator):
    """The role built from APPROVAL_ROLE_GRANTS can complete an approval."""
    digest = reviewed_digest(question, reference, quality_report)
    with approval_connection(ops.APPROVAL_ROLE_GRANTS) as alias:
        approve_through(alias, question, operator, quality_report, digest)

    approval = QuestionApproval.objects.get(question=question)
    assert approval.artifact_digest == digest
    assert approval.approved_by_id == operator.pk
    assert approval.promoted_at is None
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("dropped", [
    grant for grant in ops.APPROVAL_ROLE_GRANTS
    if not grant.startswith(UNTESTABLE_GRANTS)])
def test_every_documented_grant_is_necessary(dropped, question, reference,
                                             evidence, quality_report,
                                             operator):
    """
    Minimality, the other half of least privilege.

    A role granted something it does not need is a role nobody can reason
    about later. Dropping any one line must break the approval — if one of
    these ever stops failing, that grant should come out of the list rather
    than stay in it "to be safe".
    """
    digest = reviewed_digest(question, reference, quality_report)
    remaining = [g for g in ops.APPROVAL_ROLE_GRANTS if g != dropped]

    with approval_connection(remaining) as alias:
        with pytest.raises(Exception):
            approve_through(alias, question, operator, quality_report, digest)

    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_the_approval_role_cannot_promote_what_it_approved(
        question, reference, evidence, quality_report, operator):
    """The point of the split, end to end: it approves, then it cannot act."""
    digest = reviewed_digest(question, reference, quality_report)
    with approval_connection(ops.APPROVAL_ROLE_GRANTS) as alias:
        approve_through(alias, question, operator, quality_report, digest)

        with connections[alias].cursor() as cursor:
            for statement in (
                    "UPDATE groups_question SET trust_state = 'ORACLE_VERIFIED'",
                    "UPDATE groups_questionapproval SET promoted_at = now()",
                    "INSERT INTO groups_oracleexecution (id) VALUES (1)",
                    "UPDATE groups_referencesolution SET is_active = true"):
                with pytest.raises(ProgrammingError):
                    with transaction.atomic(using=alias):
                        cursor.execute(statement)

    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert QuestionApproval.objects.get().promoted_at is None


@pytest.mark.django_db(transaction=True)
def test_the_approval_role_cannot_read_a_password_hash(operator):
    """
    The FK check needs the user's id and nothing else.

    Column-scoped SELECT rather than table-scoped: an approval is recorded by
    a role that has no business reading credentials.
    """
    with approval_connection(ops.APPROVAL_ROLE_GRANTS) as alias:
        for column in ("password", "email", "username"):
            with pytest.raises(ProgrammingError):
                with transaction.atomic(using=alias):
                    with connections[alias].cursor() as cursor:
                        cursor.execute(f"SELECT {column} FROM groups_user")

        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT id FROM groups_user WHERE id = %s",
                           [operator.pk])
            assert cursor.fetchone()[0] == operator.pk


# ═════════════════════════════════════════════════════════════
# One artifact, one connection
# ═════════════════════════════════════════════════════════════

def test_evidence_is_read_on_the_questions_own_connection():
    """
    The artifact's oracle evidence used to be read through `default` while
    the question came from the operator alias — one digest assembled from two
    databases. Structural, because in a test both aliases are the same
    database and no run can tell them apart.
    """
    tree = ast.parse(inspect.getsource(question_artifact.collect_case_evidence))
    usings = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "using"]
    assert usings, "collect_case_evidence does not route its evidence read"
    assert any(isinstance(call.args[0], ast.Name) and call.args[0].id == "alias"
               for call in usings if call.args)


def test_every_command_names_the_connection_its_artifact_is_built_from():
    for module in (review_cmd, approve_cmd, promote_cmd):
        tree = ast.parse(inspect.getsource(module))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "build"]
        assert calls, module.__name__
        for call in calls:
            assert any(keyword.arg == "using" for keyword in call.keywords), (
                f"{module.__name__}: trust.build does not name a connection")


@pytest.mark.django_db
def test_the_evidence_alias_argument_selects_the_read(question, reference,
                                                      evidence):
    """Behavioural companion: `using` actually reaches the query."""
    passing = question_artifact.QualityOutcome.from_mapping(
        {"tier1_kill_rate": 1.0, "tier2_kill_rate": 1.0, "blockers": [],
         "mutant_identifiers": ["a"]})

    artifact = question_artifact.build_artifact(
        question, reference, passing, using="default")
    assert not artifact.blockers

    with pytest.raises(Exception):
        question_artifact.build_artifact(question, reference, passing,
                                         using="no_such_alias")


# ═════════════════════════════════════════════════════════════
# The dry run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_dry_run_writes_nothing(question, reference, evidence,
                                    quality_report, operator, capsys):
    digest = reviewed_digest(question, reference, quality_report)
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report,
                 "--digest", digest, "--dry-run")

    assert not QuestionApproval.objects.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "READY" in out


@pytest.mark.django_db
def test_the_dry_run_does_not_need_confirmation(question, reference, evidence,
                                                quality_report, operator):
    """A plan is not an act; requiring --confirm to read one teaches nothing."""
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report,
                 "--digest", "0" * 64, "--dry-run")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_the_dry_run_reports_every_refusal_at_once(question, reference,
                                                   operator, tmp_path, capsys):
    """
    No oracle evidence AND a failing gate AND a wrong digest: all three are
    reported, so the operator is not led through them one run at a time.
    """
    failing = tmp_path / "failing.json"
    failing.write_text(json.dumps({
        "tier1_kill_rate": 0.5, "tier2_kill_rate": 1.0,
        "blockers": ["a Tier-1 mutant survived"],
        "mutant_identifiers": ["t1-a"]}), encoding="utf-8")

    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", str(failing),
                 "--digest", "b" * 64, "--dry-run")

    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "no successful oracle execution" in out
    assert "quality gate" in out
    assert "digest mismatch" in out
    assert not QuestionApproval.objects.exists()


def test_the_dry_run_opens_no_transaction():
    """Structural: the write block is reachable only when dry_run is false."""
    tree = ast.parse(inspect.getsource(approve_cmd))
    handle = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "handle")
    atomics = [node for node in ast.walk(handle)
               if isinstance(node, ast.With)
               and any(isinstance(item.context_expr, ast.Call)
                       and getattr(item.context_expr.func, "attr", "")
                       == "atomic" for item in node.items)]
    assert len(atomics) == 1

    returns = [node for node in ast.walk(handle)
               if isinstance(node, ast.Return)]
    assert returns, "handle never returns early for the dry run"
    assert min(node.lineno for node in returns) < atomics[0].lineno


@pytest.mark.django_db
def test_the_dry_run_and_the_apply_refuse_for_the_same_reasons(
        question, reference, quality_report, operator, capsys):
    """
    The failure mode a separate planner develops: a dry run that says yes and
    a write that says no. Both paths consume one list.
    """
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report,
                 "--digest", "0" * 64, "--dry-run")
    planned = capsys.readouterr().out

    with pytest.raises(CommandError) as refusal:
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "0" * 64, "--confirm")

    reasons = [line.strip().lstrip("• ")
               for line in str(refusal.value).splitlines()]
    reasons = [reason for reason in reasons
               if reason and not reason.startswith("refusing to approve")]
    assert reasons
    for reason in reasons:
        assert reason.split(".")[0] in planned


# ═════════════════════════════════════════════════════════════
# Approving the same artifact twice
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_same_artifact_cannot_be_approved_twice(question, reference,
                                                    evidence, quality_report,
                                                    operator):
    digest = reviewed_digest(question, reference, quality_report)
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report,
                 "--digest", digest, "--confirm")
    assert QuestionApproval.objects.count() == 1

    with pytest.raises(CommandError, match="already records this exact"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", digest, "--confirm")
    assert QuestionApproval.objects.count() == 1


@pytest.mark.django_db
def test_a_changed_artifact_may_still_supersede(question, reference, evidence,
                                                quality_report, operator):
    """
    The duplicate check is scoped to the digest, not the question: the model
    is append-only and supersession is how a re-approval is expressed.
    """
    QuestionApproval.objects.create(
        question=question, reference=reference,
        reference_source_hash=reference.source_hash,
        artifact_digest="c" * 64, approved_by_id=operator.pk,
        approved_at=timezone.now())

    digest = reviewed_digest(question, reference, quality_report)
    call_command("question_approve", "--question", str(question.pk),
                 "--operator", operator.username,
                 "--quality-report", quality_report,
                 "--digest", digest, "--confirm")

    assert QuestionApproval.objects.count() == 2
    assert QuestionApproval.current_for(question).artifact_digest == digest


def routed_assignment(module, name):
    """The AST of the value assigned to `name`, wherever it is assigned."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets):
            return node.value
    raise AssertionError(f"{module.__name__} never assigns {name}")


def routes_through_alias(value):
    return any(isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "using"
               and node.args
               and isinstance(node.args[0], ast.Name)
               and node.args[0].id == "alias"
               for node in ast.walk(value))


def test_the_duplicate_check_reads_through_the_alias():
    """
    Scoped to the `duplicate` query itself. An earlier version asserted only
    that SOME `.using(alias)` existed in the module, which the question read
    already satisfied — the mutant that unrouted this query survived.
    """
    assert routes_through_alias(routed_assignment(approve_cmd, "duplicate"))


def test_execution_provenance_is_read_on_the_approving_connection():
    """
    The row this names is the row the digest was computed from. Reading it
    from `default` while everything else came from the alias would attribute
    the approval to an execution on another database.
    """
    tree = ast.parse(inspect.getsource(approve_cmd))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "_execution_provenance"]
    assert calls, "approve never reads execution provenance"
    for call in calls:
        assert call.args and isinstance(call.args[-1], ast.Name), ast.dump(call)
        assert call.args[-1].id == "alias"

    source = inspect.getsource(approve_cmd.Command._execution_provenance)
    assert ".using(alias)" in source


def test_current_for_reads_approvals_on_the_questions_connection():
    """`question_promote` acts on what this returns; it must be the same DB."""
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(QuestionApproval.current_for.__func__)))
    usings = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "using"]
    assert usings, "current_for does not route its read"
    assert any(call.args and isinstance(call.args[0], ast.Name)
               and call.args[0].id == "alias" for call in usings)


@pytest.mark.django_db
def test_conflicting_successful_outputs_block_the_artifact(question, reference,
                                                           operator):
    """
    Two successful runs of the same approved code on the same input produced
    different answers. There is no majority rule and no re-run that settles
    it: the artifact is blocked and stays blocked.
    """
    case = question.hidden_test_cases[0]
    for produced in (case["expected_output"], "DIFFERENT"):
        provenance.record_execution(
            question=question, reference=reference, stdin=case["stdin"],
            produced_output=produced,
            status=OracleExecution.STATUS_SUCCESS,
            execution_contract_version="v3",
            executor={"operator": operator.username})

    _evidence, blockers = question_artifact.collect_case_evidence(
        question, reference, using="default")
    assert any("conflicting" in blocker for blocker in blockers), blockers


@pytest.mark.django_db
def test_an_unknown_operator_cannot_approve(question, reference, evidence,
                                            quality_report):
    """
    An approval is an attributed statement. A username nobody holds attributes
    it to nobody, which is worse than refusing.
    """
    with pytest.raises(CommandError, match="no such user"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", "nobody-at-all",
                     "--quality-report", quality_report,
                     "--digest", "0" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()


@pytest.mark.django_db
def test_an_inactive_operator_cannot_approve(question, reference, evidence,
                                             quality_report, operator):
    operator.is_active = False
    operator.save(update_fields=["is_active"])
    with pytest.raises(CommandError, match="not an active account"):
        call_command("question_approve", "--question", str(question.pk),
                     "--operator", operator.username,
                     "--quality-report", quality_report,
                     "--digest", "0" * 64, "--confirm")
    assert not QuestionApproval.objects.exists()
