"""
The oracle write path: alias routing and the privilege contract (M2 P2.7).

`reference_create`, `reference_review` and `oracle_execute` predate the
operator aliases. They used default managers throughout, and on this deployment
`default` is the READ-ONLY census role — so authoring a reference or recording
an execution could only ever have failed, and the least-privilege role that
should perform it had no way to be selected.

These tests hold two properties: every read and write goes to the NAMED
connection, and the role that may write provenance may not touch the question,
approve it, or edit the remediation trail.

Real roles where the property is a privilege (`SET LOCAL ROLE`, each denial in
its own SAVEPOINT); AST where the property is "no code path can reach the
default connection", which no single run can demonstrate.

Local/synthetic database only.
"""

import ast
import inspect
import json

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from groups import oracle_pipeline, provenance
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import oracle_execute as oracle_cmd
from groups.management.commands import reference_create as create_cmd
from groups.management.commands import reference_review as review_cmd
from groups.models import (
    CodingPortal, OracleExecution, Question, ReferenceSolution, Topic,
)

User = get_user_model()

#: The operator aliases point at PRODUCTION hosts, so a test cannot open one:
#: `TEST: {MIRROR: default}` shares the database NAME, not the server. The
#: routing is therefore proved by capturing the alias the command passes into
#: `.using(...)` and into the provenance writer, together with the AST tests
#: below that no access escapes routing at all.

SOLUTION = ("class Solution:\n"
            "    def solve(self, s: str) -> str:\n"
            "        return s.upper()\n")


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="or-op", password="pw",
                                    email="o@example.com", is_staff=True)


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Oracle Portal")
    topic, _ = Topic.objects.get_or_create(
        name="OracleTopic", defaults={"structure_type": "flat",
                                      "portal": portal})
    return Question.objects.create(
        id=9400, title="Oracle subject", content="Statement.", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def solve(self, s: str): pass\n"},
        hidden_test_cases=[{"stdin": "ab", "expected_output": "AB"}],
        hidden_wrapper_code={}, execution_contract_version="v3")


def source_file(tmp_path, text=SOLUTION, name="reference.py"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def make_role(name, grants):
    with connection.cursor() as cursor:
        cursor.execute(f"DROP ROLE IF EXISTS {name}")
        cursor.execute(f"CREATE ROLE {name} NOLOGIN")
        for grant in grants:
            cursor.execute(grant.format(role=name))
    return name


ORACLE_GRANTS = [
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT SELECT, INSERT, UPDATE ON groups_referencesolution TO {role}",
    "GRANT SELECT, INSERT ON groups_oracleexecution TO {role}",
]


def expect_denied(role, sql, params=()):
    with pytest.raises(ProgrammingError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {role}")
                cursor.execute(sql, params)


# ═════════════════════════════════════════════════════════════
# A-F: the alias is accepted, used, and not bypassed
# ═════════════════════════════════════════════════════════════

def _alias_option(module):
    parser = module.Command().create_parser("manage.py", "cmd")
    return {action.dest for action in parser._actions}


def test_a_reference_create_accepts_an_alias():
    assert "alias" in _alias_option(create_cmd)


def test_d_oracle_execute_accepts_an_alias():
    assert "alias" in _alias_option(oracle_cmd)


def test_reference_review_accepts_an_alias():
    assert "alias" in _alias_option(review_cmd)


@pytest.mark.django_db
def test_b_reference_create_writes_through_the_named_alias(question, operator,
                                                           tmp_path,
                                                           monkeypatch):
    """
    The write goes through `.using(<the named alias>)`, not the default manager.

    Captured rather than executed against a second server: the operator aliases
    point at production hosts, and `MIRROR` shares a database name, not a
    machine. What matters is the value the command routes with.
    """
    routed = []
    real_using = ReferenceSolution.objects.using

    def recording(alias):
        routed.append(alias)
        return real_using(alias)

    monkeypatch.setattr(ReferenceSolution.objects, "using", recording)

    call_command("reference_create",
                     "--origin", "human", "--question", str(question.pk),
                 "--language", "python", "--source-file", source_file(tmp_path),
                 "--operator", operator.username, "--confirm",
                 "--alias", "default")

    assert routed and set(routed) == {"default"}, routed
    reference = ReferenceSolution.objects.get(question=question)
    assert reference.review_state == ReferenceSolution.REVIEW_DRAFT
    assert reference.is_active is False
    assert reference.source_hash is None


def test_c_no_reference_query_uses_the_default_manager():
    """
    Every ReferenceSolution and Question access in the command must be routed.
    A single unrouted read is how a row gets attached to a question that is not
    on the connection being written to — the defect already fixed in
    `pre_image.py`.
    """
    tree = ast.parse(inspect.getsource(create_cmd))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "objects"):
            continue
        parent_is_using = False
        for candidate in ast.walk(tree):
            if (isinstance(candidate, ast.Call)
                    and isinstance(candidate.func, ast.Attribute)
                    and candidate.func.attr == "using"
                    and candidate.func.value is node):
                parent_is_using = True
        assert parent_is_using, ast.dump(node)


def test_f_no_oracle_query_uses_the_default_manager():
    tree = ast.parse(inspect.getsource(oracle_cmd))
    routed, unrouted = 0, []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "objects"):
            continue
        # get_user_model().objects — the operator lookup — is DELIBERATELY on
        # default: the oracle role holds no privilege on the user table.
        if isinstance(node.value, ast.Call):
            continue
        if any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
               and c.func.attr == "using" and c.func.value is node
               for c in ast.walk(tree)):
            routed += 1
        else:
            unrouted.append(ast.dump(node))
    assert routed >= 1
    assert not unrouted, unrouted


@pytest.mark.django_db
def test_e_oracle_execute_writes_provenance_through_the_alias(question,
                                                              operator,
                                                              monkeypatch):
    """The pipeline must carry the alias all the way to the row."""
    seen = {}
    real = provenance.record_execution

    def recording(**kwargs):
        seen["using"] = kwargs.get("using")
        return real(**kwargs)

    monkeypatch.setattr(provenance, "record_execution", recording)

    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)
    reference.activate()

    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "AB", "status_id": 3})
    monkeypatch.setenv("JUDGE0_CPU_TIME_LIMIT", "5")
    monkeypatch.setenv("JUDGE0_MEMORY_LIMIT", "128000")

    call_command("oracle_execute", "--question", str(question.pk),
                 "--execute", "--operator", operator.username,
                 "--alias", "default")

    # The value threads command -> run_question -> execute_case -> _record ->
    # record_execution. Before this phase it never left the command.
    assert seen["using"] == "default"
    assert OracleExecution.objects.filter(question=question).exists()


@pytest.mark.django_db
def test_the_writer_actually_saves_on_the_named_connection(question, operator,
                                                           monkeypatch):
    """
    Threading the alias into `record_execution` is not enough — the row must be
    SAVED with it. A mutation sweep dropped `using=` from the save and every
    other test still passed, because in a test both connections are `default`.
    """
    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)

    seen = {}
    real_save = OracleExecution.save

    def recording(self, *args, **kwargs):
        seen["using"] = kwargs.get("using", "<not passed>")
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(OracleExecution, "save", recording)

    provenance.record_execution(
        question=question, reference=reference, stdin="ab",
        produced_output="AB", status=OracleExecution.STATUS_SUCCESS,
        execution_contract_version="v3", executor={}, using="default")

    assert seen["using"] == "default"


@pytest.mark.django_db
def test_provenance_refuses_a_reference_from_another_question(question,
                                                              operator):
    """A recorded output must belong to the problem it was produced for."""
    other = Question.objects.create(
        id=9401, title="Other", content="x", topic=question.topic,
        base_difficulty=1200.0, boilerplate_code={"python": "pass"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v3")
    reference = ReferenceSolution.objects.create(
        question=other, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)

    with pytest.raises(Exception, match="may not cross questions"):
        provenance.record_execution(
            question=question, reference=reference, stdin="ab",
            produced_output="AB", status=OracleExecution.STATUS_SUCCESS,
            execution_contract_version="v3", executor={}, using="default")
    assert not OracleExecution.objects.filter(question=question).exists()


@pytest.mark.django_db
def test_provenance_refuses_an_unapproved_reference(question):
    """
    An execution of an unapproved implementation is not evidence of anything.
    """
    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    assert reference.review_state == ReferenceSolution.REVIEW_DRAFT

    with pytest.raises(Exception, match="only an APPROVED reference"):
        provenance.record_execution(
            question=question, reference=reference, stdin="ab",
            produced_output="AB", status=OracleExecution.STATUS_SUCCESS,
            execution_contract_version="v3", executor={}, using="default")
    assert not OracleExecution.objects.exists()


def test_every_oracle_gate_probes_the_execution_table():
    """
    BOTH branches — production and not — must probe the table this command
    writes. A mutant pointed the production branch at the reference probe and
    a "the constant is mentioned somewhere" assertion did not notice.
    """
    tree = ast.parse(inspect.getsource(oracle_cmd))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "gate_write_privilege"]
    assert calls
    for call in calls:
        required = next(k.value for k in call.keywords if k.arg == "required")
        assert isinstance(required, ast.Attribute)
        assert required.attr == "ORACLE_EXECUTE_PROBE", ast.dump(required)


@pytest.mark.django_db
def test_a_production_target_triggers_the_role_gate(question, operator,
                                                    tmp_path, monkeypatch):
    """
    The identity lookup must be real. A mutant replaced it with a literal
    saying "not production", which disabled every gate silently.
    """
    monkeypatch.setattr(
        ops, "describe_target",
        lambda alias: {"database": "neondb", "role": "learnlm_census_ro",
                       "server_version": "17.10", "is_production": True})

    with pytest.raises(CommandError, match="not an authorized role"):
        call_command("reference_create",
                     "--origin", "human", "--question", str(question.pk),
                     "--language", "python",
                     "--source-file", source_file(tmp_path),
                     "--operator", operator.username, "--confirm")

    assert not ReferenceSolution.objects.exists()


@pytest.mark.django_db
def test_a_production_target_triggers_the_oracle_role_gate(question, operator,
                                                           monkeypatch):
    """The same check for `oracle_execute` — each command has its own call."""
    monkeypatch.setattr(
        ops, "describe_target",
        lambda alias: {"database": "neondb", "role": "learnlm_census_ro",
                       "server_version": "17.10", "is_production": True})
    monkeypatch.setenv("JUDGE0_CPU_TIME_LIMIT", "5")
    monkeypatch.setenv("JUDGE0_MEMORY_LIMIT", "128000")

    with pytest.raises(CommandError, match="not an authorized role"):
        call_command("oracle_execute", "--question", str(question.pk),
                     "--execute", "--operator", operator.username)

    assert not OracleExecution.objects.exists()


def test_the_pipeline_threads_the_alias_to_the_writer():
    for function in (oracle_pipeline.run_question, oracle_pipeline.execute_case,
                     oracle_pipeline._record, provenance.record_execution):
        assert "using" in inspect.signature(function).parameters, function


@pytest.mark.django_db
def test_g_an_unconfigured_alias_fails_loudly(question, operator, tmp_path):
    with pytest.raises(Exception) as raised:
        call_command("reference_create",
                     "--origin", "human", "--question", str(question.pk),
                     "--language", "python",
                     "--source-file", source_file(tmp_path),
                     "--operator", operator.username, "--confirm",
                     "--alias", "not_a_connection")
    assert "not_a_connection" in str(raised.value)
    assert not ReferenceSolution.objects.exists(), "a row was written anyway"


def test_o_there_is_no_silent_fallback_to_default():
    """
    No command may substitute `default` when the named alias is unusable.
    A try/except around the connection that fell back would be exactly the
    defect this phase exists to remove.
    """
    for module in (create_cmd, review_cmd, oracle_cmd):
        source = inspect.getsource(module)
        assert 'alias = "default"' not in source
        assert "alias or 'default'" not in source
        assert 'alias or "default"' not in source


# ═════════════════════════════════════════════════════════════
# H-L: what the oracle role may and may not do
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db(transaction=False)
def test_i_the_oracle_role_cannot_modify_a_question(question):
    """I + J. It reads the question and writes provenance ABOUT it."""
    role = make_role("test_oracle_rw", ORACLE_GRANTS)

    with connection.cursor() as cursor:
        cursor.execute("select has_table_privilege(%s, 'groups_question', "
                       "'SELECT')", [role])
        assert cursor.fetchone()[0] is True

    for column, value in (("content", "'tampered'"),
                          ("hidden_test_cases", "'[]'::jsonb"),
                          ("status", "'PUBLISHED'"),
                          ("trust_state", "'ORACLE_VERIFIED'"),
                          ("execution_contract_version", "'v1'")):
        expect_denied(role, f"update groups_question set {column} = {value} "
                            f"where id = %s", [question.pk])


@pytest.mark.django_db(transaction=False)
def test_k_the_oracle_role_cannot_approve_a_question(question, operator):
    role = make_role("test_oracle_noapprove", ORACLE_GRANTS)
    expect_denied(
        role,
        "insert into groups_questionapproval (question_id, approved_by_id, "
        "approved_at) values (%s, %s, now())", [question.pk, operator.pk])


@pytest.mark.django_db(transaction=False)
def test_l_the_oracle_role_cannot_touch_pre_images_or_the_audit_trail(question):
    role = make_role("test_oracle_noaudit", ORACLE_GRANTS)
    expect_denied(role, "delete from groups_questionpreimage where question_id "
                        "= %s", [question.pk])
    expect_denied(role, "update groups_remediationaction set detail = 'x'")


@pytest.mark.django_db(transaction=False)
def test_h_a_role_without_the_reference_grants_cannot_author_one(question):
    role = make_role("test_oracle_readonly",
                     ["GRANT SELECT ON groups_question TO {role}",
                      "GRANT SELECT ON groups_referencesolution TO {role}"])
    expect_denied(
        role,
        "insert into groups_referencesolution (question_id, language, "
        "source_code, review_state, is_active, created_at, updated_at) "
        "values (%s, 'python', 'x', 'DRAFT', false, now(), now())",
        [question.pk])


def test_the_gates_name_the_oracle_role_and_its_own_probes():
    for module in (create_cmd, review_cmd):
        source = inspect.getsource(module)
        assert "ALLOWED_ORACLE_ROLES" in source
        assert "REFERENCE_WRITE_PROBE" in source
        assert "ORACLE_FORBIDDEN" in source
    source = inspect.getsource(oracle_cmd)
    assert "ALLOWED_ORACLE_ROLES" in source
    assert "ORACLE_EXECUTE_PROBE" in source
    assert "ORACLE_FORBIDDEN" in source


def test_the_oracle_role_list_is_its_own():
    assert ops.ALLOWED_ORACLE_ROLES == frozenset({"learnlm_oracle_rw"})
    for other in (ops.ALLOWED_WRITE_ROLES, ops.ALLOWED_REMEDIATION_ROLES,
                  ops.ALLOWED_HIDDEN_TEST_ROLES, ops.ALLOWED_CONTRACT_ROLES,
                  ops.ALLOWED_BOILERPLATE_ROLES):
        assert not (ops.ALLOWED_ORACLE_ROLES & other)


def test_the_forbidden_list_names_the_boundary_it_defends():
    forbidden = {(table, privilege)
                 for table, _column, privilege in ops.ORACLE_FORBIDDEN}
    assert ("groups_question", "UPDATE") in forbidden
    assert ("groups_questionapproval", "INSERT") in forbidden
    assert ("groups_remediationaction", "INSERT") in forbidden
    assert ("groups_questionpreimage", "UPDATE") in forbidden
    assert ("groups_oracleexecution", "UPDATE") in forbidden
    # ...and does NOT forbid what the role exists to do
    assert ("groups_referencesolution", "INSERT") not in forbidden
    assert ("groups_oracleexecution", "INSERT") not in forbidden


# ═════════════════════════════════════════════════════════════
# M-P: transaction, FKs, audit
# ═════════════════════════════════════════════════════════════

def test_m_the_reference_write_is_in_an_alias_scoped_transaction():
    tree = ast.parse(inspect.getsource(create_cmd))
    atomics = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "atomic"]
    assert atomics
    for call in atomics:
        assert "using" in {keyword.arg for keyword in call.keywords}


@pytest.mark.django_db
def test_n_the_approver_is_recorded_by_id_across_the_alias_boundary(question,
                                                                    operator):
    """
    The reviewer is resolved on `default` while the reference lives on the
    operator alias. Assigning the OBJECT raises a cross-database error; the id
    states the same fact.
    """
    assert "approved_by_id" in inspect.getsource(ReferenceSolution.approve)

    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)

    reference.refresh_from_db()
    assert reference.approved_by_id == operator.pk
    assert reference.approved_at is not None
    assert reference.source_hash


@pytest.mark.django_db
def test_p_the_operator_is_still_recorded_on_the_execution(question, operator,
                                                            monkeypatch):
    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)
    reference.activate()

    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "AB", "status_id": 3})
    monkeypatch.setenv("JUDGE0_CPU_TIME_LIMIT", "5")
    monkeypatch.setenv("JUDGE0_MEMORY_LIMIT", "128000")

    call_command("oracle_execute", "--question", str(question.pk),
                 "--execute", "--operator", operator.username)

    execution = OracleExecution.objects.filter(question=question).first()
    assert execution is not None
    assert execution.executor.get("operator") == operator.username
    assert execution.reference_source_hash == reference.source_hash


@pytest.mark.django_db
def test_the_oracle_command_still_cannot_write_grading_truth(question,
                                                             operator,
                                                             monkeypatch):
    """The property the whole path exists to preserve."""
    before = (question.hidden_test_cases, question.status,
              question.trust_state)

    reference = ReferenceSolution.objects.create(
        question=question, language="python", source_code=SOLUTION)
    reference.submit_for_review()
    reference.approve(by=operator)
    reference.activate()

    monkeypatch.setattr(
        "groups.coding_views._run_on_judge0",
        lambda source, language, stdin="": {"stdout": "ZZ", "status_id": 3})
    monkeypatch.setenv("JUDGE0_CPU_TIME_LIMIT", "5")
    monkeypatch.setenv("JUDGE0_MEMORY_LIMIT", "128000")

    call_command("oracle_execute", "--question", str(question.pk),
                 "--execute", "--operator", operator.username)

    question.refresh_from_db()
    assert (question.hidden_test_cases, question.status,
            question.trust_state) == before

    # Structural: no assignment anywhere in the command or the pipeline
    # targets a grading-truth field.
    for module in (oracle_cmd, oracle_pipeline):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assert target.attr not in (
                            "hidden_test_cases", "status", "trust_state",
                            "expected_output"), target.attr
