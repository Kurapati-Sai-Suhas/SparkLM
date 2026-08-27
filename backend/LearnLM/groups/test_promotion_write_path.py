"""
The promotion write path: the role, the lock, and the preflight (M2 P2.7h-7).

`question_promote` is the only writer of `Question.trust_state` in the
codebase. These tests hold three properties: the role can enact a judgement and
cannot author one, every check it performs is re-performed under a row lock
before the write, and the preflight refuses for exactly the reasons the write
refuses.

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
import threading
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, connection, connections, transaction
from django.db.utils import ProgrammingError, load_backend
from django.utils import timezone

from groups import provenance, question_artifact
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.management.commands import question_promote as promote_cmd
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
PASSING_QUALITY = {"tier1_kill_rate": 1.0, "tier2_kill_rate": 1.0,
                   "blockers": [], "mutant_identifiers": ["t1-a", "t2-b"]}
ROLE_PASSWORD = "promote-role-test-password"

#: Prerequisites a real-connection test cannot single out: CONNECT is checked
#: at login (so its absence fails the whole fixture, not the command), and
#: PUBLIC holds USAGE on `public` by default.
UNTESTABLE_GRANTS = ("GRANT CONNECT", "GRANT USAGE ON SCHEMA")


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="pr-op", password="pw",
                                    email="p@example.com", is_staff=True)


@pytest.fixture
def question(db):
    """PUBLISHED, because promotion refuses a DRAFT (see the DRAFT tests)."""
    portal = CodingPortal.objects.create(name="PR Portal")
    topic, _ = Topic.objects.get_or_create(
        name="PRTopic", defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9810, title="Promotion subject", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n"
                                    "    def solve(self, s: str): pass\n"},
        hidden_test_cases=json.loads(json.dumps(CASES)),
        hidden_wrapper_code={}, execution_contract_version="v3",
        status=Question.STATUS_PUBLISHED)


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


def current_digest(question, reference, quality=None):
    outcome = question_artifact.QualityOutcome.from_mapping(
        quality or PASSING_QUALITY)
    return trust.build(question, reference, outcome,
                       using=question._state.db or DEFAULT_DB_ALIAS).digest()


@pytest.fixture
def approval(question, reference, evidence, operator):
    return QuestionApproval.objects.create(
        question=question, reference=reference,
        reference_source_hash=reference.source_hash,
        artifact_digest=current_digest(question, reference),
        quality_outcome=dict(PASSING_QUALITY),
        approved_by_id=operator.pk, approved_at=timezone.now())


# ═════════════════════════════════════════════════════════════
# A real connection as the real role
# ═════════════════════════════════════════════════════════════

@contextlib.contextmanager
def promotion_connection(grants, name="learnlm_promote_rw"):
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

    alias = "promote_under_test"
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


def promote(alias, question, operator, *extra):
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--alias", alias,
                 "--confirm", *extra)


@pytest.mark.django_db(transaction=True)
def test_the_documented_grant_list_is_sufficient(question, reference, evidence,
                                                 approval, operator):
    with promotion_connection(ops.PROMOTION_ROLE_GRANTS) as alias:
        promote(alias, question, operator)

    question.refresh_from_db()
    approval.refresh_from_db()
    assert question.trust_state == Question.TRUST_ORACLE_VERIFIED
    assert approval.promoted_by_id == operator.pk
    assert approval.promoted_at is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("dropped", [
    grant for grant in ops.PROMOTION_ROLE_GRANTS
    if not grant.startswith(UNTESTABLE_GRANTS)])
def test_every_documented_grant_is_necessary(dropped, question, reference,
                                             evidence, approval, operator):
    """Dropping any one line must break the promotion."""
    remaining = [g for g in ops.PROMOTION_ROLE_GRANTS if g != dropped]
    with promotion_connection(remaining) as alias:
        with pytest.raises(Exception):
            promote(alias, question, operator)

    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db(transaction=True)
def test_the_promoter_cannot_author_an_approval(question, reference, evidence,
                                                approval, operator):
    """
    The separation, enforced by the database rather than by convention: a role
    that could write its own approval could promote anything.
    """
    with promotion_connection(ops.PROMOTION_ROLE_GRANTS) as alias:
        for statement, params in (
                ("INSERT INTO groups_questionapproval (question_id, "
                 "reference_id, reference_source_hash, artifact_digest, "
                 "artifact_schema_version, quality_outcome, approved_by_id, "
                 "approved_at, created_at) VALUES (%s, %s, '', '', 1, '{}', "
                 "%s, now(), now())", [question.pk, reference.pk, operator.pk]),
                ("UPDATE groups_questionapproval SET artifact_digest = 'x'", []),
                ("UPDATE groups_questionapproval SET quality_outcome = '{}'", []),
                ("UPDATE groups_questionapproval SET approved_by_id = %s",
                 [operator.pk]),
                ("DELETE FROM groups_questionapproval", []),
                ("UPDATE groups_question SET status = 'PUBLISHED'", []),
                ("UPDATE groups_question SET content = 'x'", []),
                ("UPDATE groups_question SET hidden_test_cases = '[]'", []),
                ("UPDATE groups_question SET execution_contract_version = 'v1'",
                 []),
                ("UPDATE groups_referencesolution SET source_code = 'x'", []),
                ("UPDATE groups_oracleexecution SET produced_output = 'x'", [])):
            with pytest.raises(ProgrammingError):
                with transaction.atomic(using=alias):
                    with connections[alias].cursor() as cursor:
                        cursor.execute(statement, params)


@pytest.mark.django_db(transaction=True)
def test_the_promoter_cannot_read_a_password_hash(question, operator):
    with promotion_connection(ops.PROMOTION_ROLE_GRANTS) as alias:
        for column in ("password", "email"):
            with pytest.raises(ProgrammingError):
                with transaction.atomic(using=alias):
                    with connections[alias].cursor() as cursor:
                        cursor.execute(f"SELECT {column} FROM groups_user")


# ═════════════════════════════════════════════════════════════
# The contract the gates express
# ═════════════════════════════════════════════════════════════

def test_the_probe_names_only_what_promotion_writes():
    assert set(ops.PROMOTION_PROBE) == {
        ("groups_question", "trust_state", "UPDATE"),
        ("groups_questionapproval", "promoted_at", "UPDATE"),
        ("groups_questionapproval", "promoted_by_id", "UPDATE"),
    }


def test_the_probe_and_the_deny_list_do_not_contradict_each_other():
    """
    The previous contract required the promotion stamp (by performing it) and
    forbade it (by listing table-level UPDATE on the approval as excess). No
    role could satisfy both: grant it and the forbidden check refuses, withhold
    it and the write fails mid-transaction.
    """
    for table, column, privilege in ops.PROMOTION_PROBE:
        assert (table, column, privilege) not in ops.PROMOTION_FORBIDDEN
        assert (table, None, privilege) not in ops.PROMOTION_FORBIDDEN, (
            f"table-level {privilege} on {table} is forbidden, but the probe "
            f"requires it on {table}.{column}; has_table_privilege cannot "
            f"tell the two apart")


def test_every_question_column_is_either_written_or_forbidden():
    """No column of groups_question is left unconsidered."""
    columns = {"id", "title", "content", "base_difficulty", "topic_id",
               "hidden_test_cases", "boilerplate_code", "hidden_wrapper_code",
               "execution_contract_version", "status", "trust_state"}
    written = {column for table, column, _ in ops.PROMOTION_PROBE
               if table == "groups_question"}
    forbidden = {column for table, column, _ in ops.PROMOTION_FORBIDDEN
                 if table == "groups_question" and column}
    assert written == {"trust_state"}
    # `id` is the primary key: it is covered by the table-level INSERT/DELETE
    # denials rather than by a column UPDATE denial.
    assert columns - written - forbidden == {"id"}


def test_status_is_forbidden_to_the_promoter():
    """Promotion does not publish. Publishing is a separate decision."""
    assert ("groups_question", "status", "UPDATE") in ops.PROMOTION_FORBIDDEN


def test_the_two_role_lists_stay_disjoint():
    assert not (ops.ALLOWED_PROMOTION_ROLES & ops.ALLOWED_APPROVAL_ROLES)
    assert not (ops.ALLOWED_PROMOTION_ROLES & ops.ALLOWED_WRITE_ROLES)


# ═════════════════════════════════════════════════════════════
# Alias routing
# ═════════════════════════════════════════════════════════════

def test_promote_accepts_an_alias_and_a_dry_run():
    parser = promote_cmd.Command().create_parser("manage.py", "cmd")
    options = {action.dest for action in parser._actions}
    assert {"alias", "dry_run"} <= options


def test_no_read_or_write_escapes_the_alias():
    """
    Every ORM entry point in the command names the connection. Structural,
    because in a test every alias resolves to the same database.
    """
    tree = ast.parse(inspect.getsource(promote_cmd))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr in ("save", "atomic"):
            assert any(keyword.arg == "using" for keyword in node.keywords), (
                f"{node.func.attr}() without using= at line {node.lineno}")
        if node.func.attr in ("resolve_question", "current_for", "build"):
            named = [a for a in node.args
                     if isinstance(a, ast.Name) and a.id == "alias"]
            named += [k for k in node.keywords if k.arg == "using"]
            assert named, (
                f"{node.func.attr}() does not name the connection at line "
                f"{node.lineno}")


def test_the_locked_reads_are_routed():
    """`select_for_update` on the default connection locks the wrong row."""
    tree = ast.parse(inspect.getsource(promote_cmd))
    locked = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "select_for_update"]
    assert len(locked) == 2, "expected the question and the approval to lock"
    source = inspect.getsource(promote_cmd)
    assert source.count(
        "Question.objects.using(alias)\n                      "
        ".select_for_update()") == 1
    assert source.count(
        "QuestionApproval.objects.using(alias)\n                       "
        ".select_for_update()") == 1


def test_cross_alias_foreign_keys_are_assigned_by_id():
    tree = ast.parse(inspect.getsource(promote_cmd))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr != "promoted_by", (
                        "assign promoted_by_id, not promoted_by: the operator "
                        "is resolved on `default` and the row is written "
                        "through the promotion alias")


# ═════════════════════════════════════════════════════════════
# What promotion refuses
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_promotion_refuses_without_an_approval(question, reference, evidence,
                                               operator):
    with pytest.raises(CommandError, match="has no approval"):
        promote(DEFAULT_DB_ALIAS, question, operator)
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_promotion_refuses_a_stale_digest(question, reference, evidence,
                                          approval, operator):
    """The suite moved after approval: the approved artifact no longer exists."""
    question.hidden_test_cases = question.hidden_test_cases + [
        {"stdin": '["ef"]', "expected_output": "EF", "category": "extra"}]
    question.save(update_fields=["hidden_test_cases"])

    with pytest.raises(CommandError, match="STALE APPROVAL"):
        promote(DEFAULT_DB_ALIAS, question, operator)
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_promotion_refuses_a_changed_contract(question, reference, evidence,
                                              approval, operator):
    question.execution_contract_version = "v2"
    question.save(update_fields=["execution_contract_version"])
    with pytest.raises(CommandError, match="STALE APPROVAL"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_a_changed_reference_hash(question, reference,
                                                    evidence, approval,
                                                    operator):
    approval.reference_source_hash = "0" * 64
    QuestionApproval.objects.filter(pk=approval.pk).update(
        reference_source_hash="0" * 64)
    with pytest.raises(CommandError, match="source hash has changed"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_a_frozen_failing_quality_verdict(
        question, reference, evidence, approval, operator):
    QuestionApproval.objects.filter(pk=approval.pk).update(
        quality_outcome={"tier1_kill_rate": 0.5, "tier2_kill_rate": 1.0,
                         "blockers": ["a Tier-1 mutant survived"],
                         "mutant_identifiers": ["t1-a"]})
    with pytest.raises(CommandError, match="FAILING quality verdict"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_incomplete_oracle_evidence(question, reference,
                                                      evidence, approval,
                                                      operator):
    OracleExecution.objects.all().delete()
    with pytest.raises(CommandError, match="no successful oracle execution"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_a_single_agreeing_run(question, reference, evidence,
                                                 approval, operator):
    first = OracleExecution.objects.order_by("pk").first()
    OracleExecution.objects.filter(pk=first.pk).delete()
    with pytest.raises(CommandError, match="agreeing run"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_conflicting_evidence(question, reference, evidence,
                                                approval, operator):
    case = question.hidden_test_cases[0]
    provenance.record_execution(
        question=question, reference=reference, stdin=case["stdin"],
        produced_output="SOMETHING ELSE",
        status=OracleExecution.STATUS_SUCCESS,
        execution_contract_version="v3", executor={"operator": "x"})
    with pytest.raises(CommandError, match="conflicting"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_a_retired_reference(question, reference, evidence,
                                               approval, operator):
    ReferenceSolution.objects.filter(pk=reference.pk).update(is_active=False)
    with pytest.raises(CommandError, match="no canonical reference"):
        promote(DEFAULT_DB_ALIAS, question, operator)


@pytest.mark.django_db
def test_promotion_refuses_a_draft(question, reference, evidence, approval,
                                   operator):
    """
    Explicitly: a DRAFT is refused, and promotion does NOT advance it. The
    database agrees — `question_draft_cannot_be_oracle_verified`.
    """
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_DRAFT)
    with pytest.raises(CommandError, match="is DRAFT"):
        promote(DEFAULT_DB_ALIAS, question, operator)
    question.refresh_from_db()
    assert question.status == Question.STATUS_DRAFT
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_promotion_refuses_an_unknown_question(operator):
    with pytest.raises(CommandError, match="no such question"):
        call_command("question_promote", "--question", "99999999",
                     "--operator", operator.username, "--confirm")


@pytest.mark.django_db
def test_promotion_requires_confirmation(question, reference, evidence,
                                         approval, operator):
    with pytest.raises(CommandError, match="without --confirm"):
        call_command("question_promote", "--question", str(question.pk),
                     "--operator", operator.username)


@pytest.mark.django_db
def test_a_second_promotion_is_a_safe_no_op(question, reference, evidence,
                                            approval, operator):
    promote(DEFAULT_DB_ALIAS, question, operator)
    approval.refresh_from_db()
    stamped_at, stamped_by = approval.promoted_at, approval.promoted_by_id

    promote(DEFAULT_DB_ALIAS, question, operator)

    approval.refresh_from_db()
    assert approval.promoted_at == stamped_at
    assert approval.promoted_by_id == stamped_by
    assert QuestionApproval.objects.count() == 1


# ═════════════════════════════════════════════════════════════
# The transition itself
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_promotion_writes_exactly_two_rows_and_three_columns(
        question, reference, evidence, approval, operator):
    before = {field.name: getattr(question, field.name)
              for field in Question._meta.concrete_fields}

    promote(DEFAULT_DB_ALIAS, question, operator)

    question.refresh_from_db()
    after = {field.name: getattr(question, field.name)
             for field in Question._meta.concrete_fields}
    changed = {name for name in before if before[name] != after[name]}
    assert changed == {"trust_state"}
    assert after["trust_state"] == Question.TRUST_ORACLE_VERIFIED
    assert after["status"] == Question.STATUS_PUBLISHED

    approval.refresh_from_db()
    assert approval.promoted_at is not None
    assert approval.promoted_by_id == operator.pk
    assert approval.artifact_digest == current_digest(question, reference)


@pytest.mark.django_db
def test_adaptive_eligibility_needs_publication_as_well(question, reference,
                                                        evidence, approval,
                                                        operator):
    """
    Promotion alone does not make a question teach the model — it must also be
    PUBLISHED. This fixture is published, so eligibility flips here; the DRAFT
    test above shows the other half.
    """
    assert question.is_adaptive_eligible is False
    promote(DEFAULT_DB_ALIAS, question, operator)
    question.refresh_from_db()
    assert question.is_adaptive_eligible is True

    Question.objects.filter(pk=question.pk).update(status="REVIEW")
    question.refresh_from_db()
    assert question.is_adaptive_eligible is False


@pytest.mark.django_db(transaction=True)
def test_the_write_is_rejected_if_the_row_moves_under_the_lock(
        question, reference, evidence, approval, operator):
    """
    The window the lock exists to close.

    The command's pre-lock checks run against the question it read at the
    start. A concurrent writer changes the row after those checks; the
    in-memory copy still digests to the approved value, so only the re-read
    under the lock can catch it — which is the whole reason the re-read
    exists.
    """
    real_build = trust.build
    interfered = []

    def interfering(*args, **kwargs):
        # Fires inside the locked transaction, on the re-check.
        if interfered:
            return real_build(*args, **kwargs)
        interfered.append(True)

        def move():
            Question.objects.filter(pk=question.pk).update(
                content="rewritten while the promotion was running")
            connections[DEFAULT_DB_ALIAS].close()

        worker = threading.Thread(target=move)
        worker.start()
        worker.join()
        return real_build(*args, **kwargs)

    with promotion_connection(ops.PROMOTION_ROLE_GRANTS) as alias:
        # The interference lands before the FIRST build. That build still
        # digests the in-memory question and matches, so the command proceeds
        # — and the locked re-read is the only thing that can notice.
        trust.build = interfering
        try:
            with pytest.raises(
                    CommandError,
                    match="artifact changed between preflight and write"):
                promote(alias, question, operator)
        finally:
            trust.build = real_build

    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_the_re_check_under_the_lock_uses_the_approved_digest():
    """
    Structural: the value compared inside the transaction must be the digest
    on the approval, not the one computed before the lock was taken. A
    re-check against a variable computed outside the lock proves nothing.
    """
    source = inspect.getsource(promote_cmd)
    body = source[source.index("with transaction.atomic(using=alias):"):]
    assert "confirmed.digest() != approval.artifact_digest" in body
    assert "confirmed.blockers" in body


# ═════════════════════════════════════════════════════════════
# The preflight
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_preflight_writes_nothing(question, reference, evidence, approval,
                                      operator, capsys):
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")

    question.refresh_from_db()
    approval.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert approval.promoted_at is None

    out = capsys.readouterr().out
    assert "PREFLIGHT" in out and "PROMOTABLE" in out
    assert "UNVERIFIED → ORACLE_VERIFIED" in out


@pytest.mark.django_db
def test_the_preflight_names_the_fields_it_will_not_write(
        question, reference, evidence, approval, operator, capsys):
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    out = capsys.readouterr().out
    for name in ("content", "hidden_test_cases", "boilerplate_code",
                 "execution_contract_version", "status"):
        assert f"groups_question.{name}" in out or "stays" in out
    assert "promotion does not publish" in out


@pytest.mark.django_db
def test_the_preflight_reports_a_draft_as_a_blocker(question, reference,
                                                    evidence, approval,
                                                    operator, capsys):
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_DRAFT)
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    out = capsys.readouterr().out
    assert "NOT PROMOTABLE" in out
    assert "is DRAFT" in out
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_the_preflight_and_the_write_refuse_for_the_same_reasons(
        question, reference, evidence, approval, operator, capsys):
    OracleExecution.objects.all().delete()

    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    planned = capsys.readouterr().out

    with pytest.raises(CommandError) as refusal:
        promote(DEFAULT_DB_ALIAS, question, operator)

    reasons = [line.strip().lstrip("• ")
               for line in str(refusal.value).splitlines()]
    reasons = [reason for reason in reasons
               if reason and not reason.startswith("refusing to promote")]
    assert reasons
    for reason in reasons:
        assert reason.split(".")[0] in planned


def test_the_preflight_opens_no_transaction():
    tree = ast.parse(inspect.getsource(promote_cmd))
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
    assert min(node.lineno for node in returns) < atomics[0].lineno


@pytest.mark.django_db
def test_the_preflight_does_not_need_confirmation(question, reference,
                                                  evidence, approval,
                                                  operator):
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_the_preflight_reports_an_already_promoted_question(
        question, reference, evidence, approval, operator, capsys):
    promote(DEFAULT_DB_ALIAS, question, operator)
    capsys.readouterr()

    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    out = capsys.readouterr().out
    assert "ALREADY ORACLE_VERIFIED" in out


# ═════════════════════════════════════════════════════════════
# Production identity
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_production_target_triggers_the_promotion_role_gate(
        question, reference, evidence, approval, operator, monkeypatch):
    monkeypatch.setattr(ops, "describe_target", lambda alias: {
        "database": ops.PRODUCTION_DATABASE, "role": "neondb_owner",
        "server_version": 170010, "is_production": True})
    with pytest.raises(ops.GateFailure, match="learnlm_promote_rw"):
        promote(DEFAULT_DB_ALIAS, question, operator)
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED


@pytest.mark.django_db
def test_the_preflight_reports_a_refused_role_instead_of_hiding_the_plan(
        question, reference, evidence, approval, operator, monkeypatch,
        capsys):
    monkeypatch.setattr(ops, "describe_target", lambda alias: {
        "database": ops.PRODUCTION_DATABASE, "role": "learnlm_census_ro",
        "server_version": 170010, "is_production": True})
    call_command("question_promote", "--question", str(question.pk),
                 "--operator", operator.username, "--dry-run")
    out = capsys.readouterr().out
    assert "PREFLIGHT" in out
    assert "NOT PROMOTABLE" in out
    assert "learnlm_census_ro" in out


#: The ONLY two modules permitted to assign `trust_state`.
#:
#: `question_demote` joined `question_promote` in M2 P2.7h-35. Until then
#: ORACLE_VERIFIED was a one-way door, which meant a question whose evidence
#: stopped covering its suite kept claiming trust and could never re-earn it.
#: Adding a second writer weakens nothing PROVIDED it can only ever remove the
#: claim — which the companion test below pins.
TRUST_WRITERS = {"question_promote.py", "question_demote.py"}


def trust_state_writers():
    """(filename, line) for every non-test assignment to `trust_state`."""
    import pathlib
    root = pathlib.Path(promote_cmd.__file__).resolve().parents[1]
    writers = []
    for path in root.rglob("*.py"):
        if "test" in path.name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Attribute)
                            and target.attr == "trust_state"):
                        writers.append((path.name, node.lineno))
    return writers


def test_only_the_two_sanctioned_commands_write_trust_state():
    """
    The property the whole milestone rests on. Any OTHER module assigning
    `trust_state` would be an unreviewed path into or out of trust.
    """
    unexpected = [f"{name}:{line}" for name, line in trust_state_writers()
                  if name not in TRUST_WRITERS]
    assert not unexpected, unexpected


def test_demotion_can_only_ever_remove_the_claim():
    """
    Why a second writer is safe. `question_demote` must never assign the
    verified value — if it could, it would be a promotion path that skips
    the oracle, the approval and the artifact digest.
    """
    import pathlib
    from groups.models import Question

    source = (pathlib.Path(promote_cmd.__file__).resolve().parent
              / "question_demote.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assigned = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Attribute)
                        and target.attr == "trust_state"):
                    assigned.append(ast.unparse(node.value))
    assert assigned == ["TO_STATE"], assigned

    from groups.management.commands import question_demote
    assert question_demote.TO_STATE == Question.TRUST_UNVERIFIED
    assert question_demote.TO_STATE != Question.TRUST_ORACLE_VERIFIED


def test_current_for_is_read_on_the_promoting_connection():
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(QuestionApproval.current_for.__func__)))
    usings = [node for node in ast.walk(tree)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "using"]
    assert usings


def promote_with_interference(question, operator, interfere,
                              alias=DEFAULT_DB_ALIAS):
    """
    Run a promotion, changing the world once the pre-lock checks are done.

    `trust.build` is the seam: the command calls it once before taking the
    lock and once inside. Interfering on the first call reproduces exactly the
    race the locked re-reads exist for — the command has already decided, and
    the row it decided about then moves.
    """
    real_build = trust.build
    fired = []

    def interfering(*args, **kwargs):
        if not fired:
            fired.append(True)
            interfere()
        return real_build(*args, **kwargs)

    trust.build = interfering
    try:
        promote(alias, question, operator)
    finally:
        trust.build = real_build


@pytest.mark.django_db
def test_a_question_moved_under_the_lock_is_not_promoted(
        question, reference, evidence, approval, operator):
    """
    Another writer promotes the same question first. The early "already
    ORACLE_VERIFIED, nothing to do" branch cannot catch this — it ran before
    the other writer landed — so the check under the lock is the only thing
    between here and a second promotion stamping a second approval.
    """
    def interfere():
        Question.objects.filter(pk=question.pk).update(
            trust_state=Question.TRUST_ORACLE_VERIFIED)

    with pytest.raises(CommandError, match="under lock"):
        promote_with_interference(question, operator, interfere)

    approval.refresh_from_db()
    assert approval.promoted_at is None


@pytest.mark.django_db
def test_an_approval_stamped_under_the_lock_is_not_stamped_twice(
        question, reference, evidence, approval, operator):
    """
    An approval records who promoted it and when. Overwriting that stamp would
    quietly rewrite the answer to "who made this trusted, and when".
    """
    marker = timezone.now() - timedelta(days=1)

    def interfere():
        QuestionApproval.objects.filter(pk=approval.pk).update(
            promoted_at=marker, promoted_by_id=operator.pk)

    with pytest.raises(CommandError, match="stamp it twice"):
        promote_with_interference(question, operator, interfere)

    approval.refresh_from_db()
    assert approval.promoted_at == marker
    question.refresh_from_db()
    assert question.trust_state == Question.TRUST_UNVERIFIED
