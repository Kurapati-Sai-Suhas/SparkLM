"""
Reseed write authority (M2 P2.7h-17) — DESIGN ONLY.

None of these roles exists on production. Every role here is created in the
LOCAL test database, exercised, and dropped. Nothing in this file grants,
revokes or writes anything on production.

Three authorities are involved in a reseed and they are deliberately not the
same one:

    statement   `learnlm_remediate_rw`    writes content
    signature   `learnlm_boilerplate_rw`  writes boilerplate_code
    ledger      `learnlm_reseed_rw`       writes groups_reseedledger, nothing else

The first two already exist and are proposed UNCHANGED — the claim these tests
have to earn is that no new grant is needed for either. The third is new, and
its whole design is a deny-list: it coordinates, and can change nothing that
a learner or the trust chain would ever see.
"""

import contextlib
import copy

import pytest
from django.db import DEFAULT_DB_ALIAS, ProgrammingError, connections
from django.db.utils import load_backend

from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, ReseedLedger

ROLE_PASSWORD = "reseed-authority-probe"
UNTESTABLE_GRANTS = ("GRANT CONNECT", "GRANT USAGE ON SCHEMA")


@contextlib.contextmanager
def role_connection(grants, name):
    """Create `name` in the LOCAL test database, yield an alias, drop it."""
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

    alias = f"{name}_under_test"
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


def reseed_connection(grants=None):
    return role_connection(grants or ops.RESEED_ROLE_GRANTS,
                           "learnlm_reseed_rw")


@pytest.fixture
def operator(db, django_user_model):
    return django_user_model.objects.create_user(
        username="reseed-authority", password="pw", email="a@example.com",
        is_staff=True)


@pytest.fixture
def stub(db):
    from groups.models import CodingPortal, Topic
    portal = CodingPortal.objects.create(name="Authority Portal")
    topic, _ = Topic.objects.get_or_create(
        name="AuthorityTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9840, title="Widget Count", topic=topic, base_difficulty=1300.0,
        content=f"<p>{Question.PLACEHOLDER_MARKER} Widget Count</p>",
        boilerplate_code={"python": "class Solution:\n"
                                    "    def widgetCount(self, *args, **kwargs):\n"
                                    "        pass\n"},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")


@pytest.fixture
def batch(stub, operator):
    from django.utils import timezone
    from groups import pre_image
    from groups.models import RemediationBatch
    row = RemediationBatch.objects.create(
        batch_key="reseed-authority-A", purpose="authority probes",
        created_by=operator)
    pre_image.capture(row, stub, operator)
    RemediationBatch.objects.filter(pk=row.pk).update(
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    row.refresh_from_db()
    return row


# ═════════════════════════════════════════════════════════════
# 1. Statement generation needs no new authority
# ═════════════════════════════════════════════════════════════

def test_statement_generation_reuses_the_statement_repair_role():
    """
    The claim that saves a role: generation and repair write the same column,
    so the existing grant is already exactly right.
    """
    assert (ops.ALLOWED_STATEMENT_GENERATION_ROLES
            == ops.ALLOWED_REMEDIATION_ROLES)
    assert ops.STATEMENT_GENERATION_PROBE == ops.STATEMENT_REPAIR_PROBE
    assert ops.STATEMENT_GENERATION_FORBIDDEN == ops.STATEMENT_REPAIR_FORBIDDEN


def test_statement_generation_writes_only_content():
    assert ops.STATEMENT_GENERATION_PROBE == (
        ("groups_question", "content", "UPDATE"),)


@pytest.mark.parametrize("column", ["boilerplate_code", "hidden_test_cases",
                                    "status", "trust_state",
                                    "execution_contract_version",
                                    "hidden_wrapper_code"])
def test_statement_generation_cannot_touch(column):
    """Generating a statement must not reach the starter or the answers."""
    assert ("groups_question", column, "UPDATE") \
        in ops.STATEMENT_GENERATION_FORBIDDEN


# ═════════════════════════════════════════════════════════════
# 2. Signature declaration needs no new authority either
# ═════════════════════════════════════════════════════════════

def test_signature_declaration_reuses_the_boilerplate_role():
    assert ops.ALLOWED_SIGNATURE_ROLES == ops.ALLOWED_BOILERPLATE_ROLES
    assert ops.SIGNATURE_DECLARATION_PROBE == ops.BOILERPLATE_REPAIR_PROBE


def test_signature_declaration_writes_only_boilerplate():
    assert ops.SIGNATURE_DECLARATION_PROBE == (
        ("groups_question", "boilerplate_code", "UPDATE"),)


@pytest.mark.parametrize("column", ["content", "hidden_test_cases", "status",
                                    "trust_state",
                                    "execution_contract_version",
                                    "hidden_wrapper_code"])
def test_signature_declaration_cannot_touch(column):
    """
    `hidden_test_cases` is the one that matters. The declared signature is
    what every hidden case is bound against, so an authority able to move both
    could redefine the question and its answers together and leave them
    agreeing with each other.
    """
    assert ("groups_question", column, "UPDATE") \
        in ops.SIGNATURE_DECLARATION_FORBIDDEN


def test_the_two_content_authorities_stay_disjoint():
    """
    Statement writes content; signature writes the starter. Each is forbidden
    the other's column. Unioning them is the single most valuable mutation in
    this milestone's sweep, and this is what kills it.
    """
    assert ("groups_question", "boilerplate_code", "UPDATE") \
        in ops.STATEMENT_GENERATION_FORBIDDEN
    assert ("groups_question", "content", "UPDATE") \
        in ops.SIGNATURE_DECLARATION_FORBIDDEN


def test_neither_content_authority_can_move_status_or_trust():
    for forbidden in (ops.STATEMENT_GENERATION_FORBIDDEN,
                      ops.SIGNATURE_DECLARATION_FORBIDDEN):
        assert ("groups_question", "status", "UPDATE") in forbidden
        assert ("groups_question", "trust_state", "UPDATE") in forbidden


# ═════════════════════════════════════════════════════════════
# 3. The ledger writer, on a real connection
# ═════════════════════════════════════════════════════════════

def orchestrate(alias, batch, question):
    """
    One full unit of the coordinator's work, and the whole of it.

    Deciding what to do next is a READ of live state — is the placeholder
    still in the statement? is the batch frozen? — followed by a ledger write.
    That reading is the reason the ledger carries no digest: the coordinator
    re-derives the truth every time instead of trusting its own notes. So the
    reads belong in this helper, and the minimality test below is only honest
    if it exercises them.
    """
    from groups.models import RemediationBatch

    live = Question.objects.using(alias).get(pk=question.pk)
    frozen = RemediationBatch.objects.using(alias).get(pk=batch.pk)
    done = Question.PLACEHOLDER_MARKER not in live.content

    row, _created = ReseedLedger.objects.using(alias).get_or_create(
        batch_id=frozen.pk, question_id=live.pk)
    row.stage = (ReseedLedger.STAGE_STATEMENT if done
                 else ReseedLedger.STAGE_PENDING)
    row.attempts = row.attempts + 1
    row.last_error = ""
    row.save(using=alias,
             update_fields=["stage", "attempts", "last_error", "updated_at"])
    return row


@pytest.mark.django_db(transaction=True)
def test_the_proposed_grants_are_sufficient(stub, batch):
    """The intended work succeeds: read live state, insert a row, advance it."""
    with reseed_connection() as alias:
        row = orchestrate(alias, batch, stub)

    stored = ReseedLedger.objects.get(pk=row.pk)
    assert stored.stage == ReseedLedger.STAGE_PENDING
    assert stored.attempts == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("dropped", [
    grant for grant in ops.RESEED_ROLE_GRANTS
    if not grant.startswith(UNTESTABLE_GRANTS)])
def test_every_proposed_grant_is_necessary(dropped, stub, batch):
    """
    Minimality. Drop any one line and the coordinator's work must fail — the
    check that removed a sequence grant the approval role never needed.
    """
    remaining = [grant for grant in ops.RESEED_ROLE_GRANTS if grant != dropped]
    with reseed_connection(remaining) as alias:
        with pytest.raises(ProgrammingError):
            orchestrate(alias, batch, stub)


@pytest.mark.django_db(transaction=True)
def test_the_ledger_writer_cannot_write_any_question_column(stub, batch):
    """The coordinator coordinates. It changes nothing a learner can see."""
    with reseed_connection() as alias:
        for column, value in (("content", "rewritten"),
                              ("boilerplate_code", {"python": "x"}),
                              ("hidden_test_cases", [{"stdin": "1"}]),
                              ("status", Question.STATUS_PUBLISHED),
                              ("trust_state", "ORACLE_VERIFIED"),
                              ("execution_contract_version", "v3")):
            with pytest.raises(ProgrammingError):
                Question.objects.using(alias).filter(pk=stub.pk).update(
                    **{column: value})

    stub.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in stub.content
    assert stub.status == Question.STATUS_DRAFT
    assert stub.hidden_test_cases == []


@pytest.mark.django_db(transaction=True)
def test_the_ledger_writer_cannot_retarget_a_row(stub, batch):
    """
    Column-level UPDATE is what makes "this row is about question N"
    permanent. Without it, a coordinator could advance a row for one question
    and then point the finished record at another.
    """
    row = ReseedLedger.objects.create(batch=batch, question=stub)
    other = Question.objects.create(
        id=9841, title="Other", topic=stub.topic, base_difficulty=1300.0,
        content="<p>real statement</p>", boilerplate_code={},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")

    with reseed_connection() as alias:
        with pytest.raises(ProgrammingError):
            ReseedLedger.objects.using(alias).filter(pk=row.pk).update(
                question_id=other.pk)

    row.refresh_from_db()
    assert row.question_id == stub.pk


@pytest.mark.django_db(transaction=True)
def test_the_ledger_writer_cannot_forget(stub, batch):
    """Advancing is allowed; deleting the record that a question was touched
    is not."""
    row = ReseedLedger.objects.create(batch=batch, question=stub)

    with reseed_connection() as alias:
        with pytest.raises(ProgrammingError):
            ReseedLedger.objects.using(alias).filter(pk=row.pk).delete()

    assert ReseedLedger.objects.filter(pk=row.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_the_ledger_writer_cannot_fabricate_an_audit_trail(stub, batch,
                                                           operator):
    """
    A coordinator able to append actions could record work it never performed,
    which is worse than no record because it reads as evidence. The audit
    trail is written by the roles that actually change a column.
    """
    with reseed_connection() as alias:
        with pytest.raises(ProgrammingError):
            RemediationAction.objects.using(alias).create(
                batch=batch, question=stub,
                action_class=RemediationAction.CLASS_STATEMENT_GENERATION,
                applied_by=operator, detail="never happened",
                post_digest="y" * 64)

    assert RemediationAction.objects.filter(question=stub).count() == 0


@pytest.mark.django_db(transaction=True)
def test_the_ledger_writer_cannot_touch_the_trust_chain(stub, batch):
    with reseed_connection() as alias:
        with connections[alias].cursor() as cursor:
            for table in ("groups_questionapproval", "groups_referencesolution",
                          "groups_oracleexecution", "groups_questionpreimage"):
                cursor.execute(
                    "select has_table_privilege(current_user, %s, 'INSERT'), "
                    "       has_table_privilege(current_user, %s, 'UPDATE')",
                    [table, table])
                assert cursor.fetchone() == (False, False), table


@pytest.mark.django_db(transaction=True)
def test_the_documented_deny_list_holds_against_the_real_role(stub, batch):
    """Every entry in the deny-list, checked against the live grant."""
    with reseed_connection() as alias:
        ops.gate_write_privilege(alias, required=ops.RESEED_LEDGER_PROBE,
                                 forbidden=ops.RESEED_LEDGER_FORBIDDEN)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("extra", [
    "GRANT UPDATE (content) ON groups_question TO {role}",
    "GRANT UPDATE (hidden_test_cases) ON groups_question TO {role}",
    "GRANT UPDATE (boilerplate_code) ON groups_question TO {role}",
    "GRANT UPDATE (status) ON groups_question TO {role}",
    "GRANT UPDATE (trust_state) ON groups_question TO {role}",
    "GRANT INSERT ON groups_remediationaction TO {role}",
    "GRANT DELETE ON groups_reseedledger TO {role}",
    "GRANT UPDATE (question_id) ON groups_reseedledger TO {role}",
])
def test_an_over_granted_ledger_writer_is_refused(extra, stub, batch):
    """
    An over-granted role is refused rather than trusted — policy as a runtime
    check, not a comment.
    """
    with reseed_connection(list(ops.RESEED_ROLE_GRANTS) + [extra]) as alias:
        with pytest.raises(ops.GateFailure):
            ops.gate_write_privilege(alias, required=ops.RESEED_LEDGER_PROBE,
                                     forbidden=ops.RESEED_LEDGER_FORBIDDEN)


# ═════════════════════════════════════════════════════════════
# 4. The authorities stay separate from each other
# ═════════════════════════════════════════════════════════════

def test_the_reseed_role_is_disjoint_from_every_other_authority():
    for other in (ops.ALLOWED_REMEDIATION_ROLES, ops.ALLOWED_BOILERPLATE_ROLES,
                  ops.ALLOWED_HIDDEN_TEST_ROLES, ops.ALLOWED_CONTRACT_ROLES,
                  ops.ALLOWED_ORACLE_ROLES, ops.ALLOWED_APPROVAL_ROLES,
                  ops.ALLOWED_PROMOTION_ROLES, ops.ALLOWED_STATUS_ROLES,
                  ops.ALLOWED_WRITE_ROLES):
        assert not (ops.ALLOWED_RESEED_ROLES & other)


def test_the_ledger_writer_grants_no_question_write():
    """No proposed grant line writes a question column."""
    for grant in ops.RESEED_ROLE_GRANTS:
        if "groups_question" in grant:
            assert grant.startswith("GRANT SELECT"), grant


def test_the_ledger_is_never_granted_to_a_content_authority():
    """
    The ledger has exactly one writer. If a stage role could write it too, the
    coordinator's record and the coordinator's work would share an authority.
    """
    for grants in (ops.STATUS_ROLE_GRANTS, ops.APPROVAL_ROLE_GRANTS,
                   ops.PROMOTION_ROLE_GRANTS):
        assert not any("reseedledger" in grant for grant in grants)


def test_the_ledger_still_declares_no_trusted_field():
    """
    Restated here because this file is where the authority argument lives: the
    ledger writer is safe to exist ONLY because the ledger cannot carry
    anything worth trusting.
    """
    fields = {field.name for field in ReseedLedger._meta.get_fields()}
    assert fields == {"id", "batch", "question", "stage", "last_error",
                      "attempts", "created_at", "updated_at"}
