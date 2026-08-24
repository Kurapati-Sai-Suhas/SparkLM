"""
The remediation role contract, proven against a REAL role (M2 P2.7).

A gate bug sent a statement repair looking for INSERT on `groups_question` — a
privilege it neither needs nor should ever hold. The role was right and the
gate was wrong, and it failed safe only by luck.

Mocked privilege checks could not have caught that: they assert what the code
asks for, and the code was asking for the wrong thing. So these tests build
roles with exact grants and use `SET LOCAL ROLE` to become them — the database,
not a fake, decides what is permitted.

Every denial runs inside its own SAVEPOINT. A permission error aborts the
surrounding Postgres transaction, so without one the first denial would make
every later statement fail for the wrong reason: the test would still pass, but
it would be measuring an aborted transaction rather than a privilege.

Local/synthetic database only. Roles are created inside the test transaction
and vanish with it.
"""

import pytest
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from groups.management.commands import _preimage_ops as ops
from groups.models import CodingPortal, Question, Topic

ROLE = "test_remediate_contract"


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Contract Portal")
    topic, _ = Topic.objects.get_or_create(
        name="ContractTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9300, title="Contract subject", content="Original.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


def make_role(name, grants):
    with connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {name}")
        cursor.execute(f"CREATE ROLE {name} NOLOGIN")
        for grant in grants:
            cursor.execute(grant.format(role=name))
    return name


@pytest.fixture
def remediation_role(db, question):
    """
    EXACTLY the intended contract: read a question, update its statement, read
    the batch and pre-image tables, append an audit row. Nothing else.
    """
    return make_role(ROLE, [
        "GRANT SELECT ON groups_question TO {role}",
        "GRANT UPDATE (content) ON groups_question TO {role}",
        "GRANT SELECT ON groups_remediationbatch, groups_questionpreimage TO {role}",
        "GRANT SELECT, INSERT ON groups_remediationaction TO {role}",
    ])


def expect_denied(role, sql, params=()):
    """Assert the database refuses `sql` for `role`, inside a savepoint."""
    with pytest.raises(ProgrammingError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {role}")
                cursor.execute(sql, params)


# ═════════════════════════════════════════════════════════════
# What the role can and cannot actually do
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_role_can_update_the_statement(remediation_role, question):
    """B — the operation the command exists to perform."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {remediation_role}")
            cursor.execute(
                "update groups_question set content = %s where id = %s",
                ["Repaired.", question.pk])

    question.refresh_from_db()
    assert question.content == "Repaired."


@pytest.mark.django_db
def test_the_role_cannot_update_hidden_tests(remediation_role, question):
    """C — what makes statement-before-key a privilege rather than a rule."""
    expect_denied(
        remediation_role,
        "update groups_question set hidden_test_cases = %s where id = %s",
        ["[]", question.pk])


@pytest.mark.django_db
def test_the_role_cannot_update_trust_state(remediation_role, question):
    """D — a repair must never be able to confer trust."""
    expect_denied(
        remediation_role,
        "update groups_question set trust_state = %s where id = %s",
        ["ORACLE_VERIFIED", question.pk])


@pytest.mark.django_db
def test_the_role_cannot_update_status(remediation_role, question):
    expect_denied(remediation_role,
                  "update groups_question set status = %s where id = %s",
                  ["PUBLISHED", question.pk])


@pytest.mark.django_db
def test_the_role_cannot_insert_a_question(remediation_role):
    """E — INSERT is not needed and must not be held."""
    expect_denied(
        remediation_role,
        "insert into groups_question (id, title) values (99999, 'x')")


@pytest.mark.django_db
def test_the_role_cannot_delete_a_question(remediation_role, question):
    expect_denied(remediation_role,
                  "delete from groups_question where id = %s", [question.pk])


@pytest.mark.django_db
def test_the_role_can_append_its_own_audit_row(remediation_role):
    """The audit trail must be writable, or a repair cannot be recorded."""
    with connection.cursor() as cursor:
        cursor.execute("select has_table_privilege(%s, %s, %s)",
                       [remediation_role, "groups_remediationaction", "INSERT"])
        assert cursor.fetchone()[0] is True


# ═════════════════════════════════════════════════════════════
# The gate agrees with the database
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_gate_passes_for_the_intended_role(remediation_role):
    """
    A — the gate must accept exactly the role the contract describes. This is
    the test the INSERT bug would have failed.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {remediation_role}")
            assert ops.gate_write_privilege(
                "default",
                required=ops.STATEMENT_REPAIR_PROBE,
                forbidden=ops.STATEMENT_REPAIR_FORBIDDEN) is True


@pytest.mark.django_db
def test_the_gate_refuses_a_role_that_cannot_update_content(db):
    lean = make_role("test_remediate_lean",
                     ["GRANT SELECT ON groups_question TO {role}"])
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {lean}")
            with pytest.raises(ops.GateFailure, match="cannot UPDATE"):
                ops.gate_write_privilege(
                    "default", required=ops.STATEMENT_REPAIR_PROBE)


@pytest.mark.django_db
def test_the_gate_refuses_an_over_granted_role(db):
    """
    The other direction, and the more important one: a role able to do MORE
    than the operation needs is refused, not trusted. The written contract
    becomes a run-time check.
    """
    broad = make_role("test_remediate_broad",
                      ["GRANT SELECT, UPDATE ON groups_question TO {role}"])
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL ROLE {broad}")
            with pytest.raises(ops.GateFailure, match="must not have"):
                ops.gate_write_privilege(
                    "default",
                    required=ops.STATEMENT_REPAIR_PROBE,
                    forbidden=ops.STATEMENT_REPAIR_FORBIDDEN)


# ═════════════════════════════════════════════════════════════
# The probes themselves
# ═════════════════════════════════════════════════════════════

def test_the_gate_does_not_require_insert_on_question():
    """E, stated as the gate's own contract — the regression that started this."""
    assert ops.STATEMENT_REPAIR_PROBE == (
        ("groups_question", "content", "UPDATE"),)
    assert not any(privilege == "INSERT"
                   for _t, _c, privilege in ops.STATEMENT_REPAIR_PROBE)


def test_the_probe_is_column_scoped_not_table_scoped():
    """
    A table-level UPDATE check returns TRUE when the role holds UPDATE on ANY
    column, so it cannot tell the narrow grant from a dangerous one.
    """
    for _table, column, _privilege in ops.STATEMENT_REPAIR_PROBE:
        assert column is not None


def test_the_forbidden_set_covers_every_grading_truth_field():
    """
    If a remediable field is added to the model without appearing here, the
    gate silently stops protecting it.
    """
    columns = {column for _t, column, _p in ops.STATEMENT_REPAIR_FORBIDDEN
               if column}
    assert columns == {"hidden_test_cases", "status", "trust_state",
                       "execution_contract_version", "boilerplate_code",
                       "hidden_wrapper_code"}

    table_level = {privilege for _t, column, privilege
                   in ops.STATEMENT_REPAIR_FORBIDDEN if column is None}
    assert table_level == {"INSERT", "DELETE", "TRUNCATE"}


def test_capture_still_probes_its_own_operation():
    """Fixing remediation must not have changed what capture asks for."""
    assert ops.CAPTURE_PROBE == (("groups_questionpreimage", None, "INSERT"),)
