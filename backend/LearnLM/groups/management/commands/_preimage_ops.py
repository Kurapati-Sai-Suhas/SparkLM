"""
Shared guards for the pre-image operator commands (M2 P2.7, blocker J8).

Three commands — `preimage_capture`, `preimage_inspect`, `preimage_rollback` —
and one set of gates, here, so a check cannot be present in one command and
forgotten in another.

── The gates ───────────────────────────────────────────────────────────────

    1. operator     staff + active, via the established resolve_operator
    2. identity     the target really is the documented production database
    3. role         NOT census_ro (cannot write) and NOT pilot_rw (wrong scope)
    4. confirmation --confirm, per command, for every production write

Every one ABORTS. None degrades, none warns-and-continues, and there is
deliberately **no `--force`**: a general-purpose override is a gate that is
present in the code and absent in practice, because the one time it matters is
the one time somebody is in a hurry.

── Why the role check refuses two roles for opposite reasons ───────────────

`learnlm_census_ro` holds no write privilege, so a capture through it fails at
the server — late, after the operator believes it started. `learnlm_pilot_rw`
CAN write, and that is the danger: it is scoped to the reference pilot, and
using it here would put pre-image rows outside the privilege boundary that was
reviewed for them. One role is refused because it cannot, the other because it
should not.
"""

from django.core.management.base import CommandError
from django.db import connections

from groups.management.commands import _question_trust as trust

#: The documented production database. A target that is not exactly this is
#: refused rather than assumed — a pre-image captured from the wrong database
#: is worse than none, because it looks like a safety net.
PRODUCTION_DATABASE = "neondb"

#: The ONLY roles permitted to write pre-image rows. An ALLOW-list, not a
#: deny-list (M2 P2.7, hardened once `learnlm_preimage_rw` existed).
#:
#: A deny-list naming the two roles to avoid let every OTHER role through —
#: including `neondb_owner`, which holds full write on every table in the
#: database. That makes the purpose-built least-privilege role decorative: the
#: capture would be *able* to run with the privilege to rewrite grading truth,
#: and the only thing stopping it would be the operator's memory.
#:
#: `learnlm_preimage_rw` is audited to hold SELECT+INSERT on the three
#: pre-image tables and NOTHING on groups_question, groups_questionapproval,
#: groups_referencesolution, groups_oracleexecution, groups_codesubmission or
#: any learner-state table. Requiring it means a capture physically cannot
#: touch grading truth, whatever the code does.
ALLOWED_WRITE_ROLES = frozenset({"learnlm_preimage_rw"})

#: The ONLY roles permitted to write grading truth during remediation.
#:
#: A SEPARATE list from the capture roles, and deliberately so. Capture and
#: remediation are different privileges: the capture role can read a question
#: and append a pre-image, and must never be able to change the thing it is
#: preserving. Sharing one list would mean granting the capture role the write
#: it exists to make unnecessary.
#:
#: The remediation role is expected to hold COLUMN-level UPDATE on exactly the
#: columns its action class touches — `content` for STATEMENT_REPAIR — so the
#: database refuses a write outside the approved scope even if this code asked
#: for one.
ALLOWED_REMEDIATION_ROLES = frozenset({"learnlm_remediate_rw"})

#: The ONLY roles permitted to rewrite a hidden-test suite.
#:
#: A THIRD list. Statement repair and hidden-test repair are different
#: privileges over the same table, and the remediation design fixed an order
#: between them: statement first, keys second. Sharing one role would make that
#: order a matter of discipline; two column-scoped roles make it a matter of
#: privilege, which is the only version that survives a hurried operator.
ALLOWED_HIDDEN_TEST_ROLES = frozenset({"learnlm_hidden_test_rw"})

#: The ONLY roles permitted to change a question's execution contract.
#:
#: A FOURTH list, for the same reason there is a third. A contract migration
#: does not edit a statement or a key — it changes what the stored inputs MEAN
#: when they are executed, which is a distinct authority over the same table.
#: Keeping it separate means the role that repaired a statement cannot re-point
#: a question at a different harness, and this one cannot touch the text or the
#: answers whose interpretation it is changing.
ALLOWED_CONTRACT_ROLES = frozenset({"learnlm_contract_rw"})

#: The ONLY roles permitted to change a question's starter code.
#:
#: A FIFTH list. `boilerplate_code` is the code every learner is HANDED, and it
#: is also where the declared signature lives — so this role's writes decide
#: both what a learner starts from and how the adapter will bind their
#: arguments. That is a distinct authority from repairing a statement, a key or
#: a contract version, and it gets its own role rather than being folded into
#: one that already exists.
ALLOWED_BOILERPLATE_ROLES = frozenset({"learnlm_boilerplate_rw"})

#: The roles permitted to perform a ROLLBACK.
#:
#: A restore writes back whatever a repair changed, so the roles that may undo
#: are exactly the roles that may do: the four column-scoped repair roles. Which
#: ONE of them is sufficient depends on the question — see
#: `rollback_privileges`, which derives the requirement from the fields that
#: actually differ. Capture (`learnlm_preimage_rw`) is deliberately absent: it
#: holds nothing on `groups_question` and could never restore one.
#:
#: The command previously named no list at all and inherited the CAPTURE
#: default, so it demanded the one role that cannot do the job — the same class
#: of bug as the statement gate that demanded INSERT.
ALLOWED_ROLLBACK_ROLES = (ALLOWED_REMEDIATION_ROLES | ALLOWED_HIDDEN_TEST_ROLES
                          | ALLOWED_CONTRACT_ROLES | ALLOWED_BOILERPLATE_ROLES)

#: The ONLY roles permitted to author a reference or record an execution.
#:
#: The first list whose privileges are not on `groups_question`. An oracle run
#: reads a question and writes provenance about it; it must never be able to
#: change the thing it is checking. Kept apart from the repair roles for the
#: same reason they are kept apart from each other — and, unlike them, this one
#: is refused UPDATE on `groups_question` entirely.
ALLOWED_ORACLE_ROLES = frozenset({"learnlm_oracle_rw"})

#: The ONLY roles permitted to record a human's approval of a question.
#:
#: An approval writes `groups_questionapproval` and NOTHING else — not the
#: question, not the keys, not the reference. It is the record that a person
#: read an artifact and vouched for it.
ALLOWED_APPROVAL_ROLES = frozenset({"learnlm_approve_rw"})

#: The ONLY roles permitted to promote a question to ORACLE_VERIFIED.
#:
#: Separate from approval, because the two acts are separate: approving says "I
#: read this artifact", promoting says "this artifact is what is live now, and
#: its answers may count". Promotion is the single most consequential write in
#: the system — after it, a wrong key stops being a bad practice question and
#: starts corrupting a learner model — so it gets its own role and its own
#: pair of columns.
ALLOWED_PROMOTION_ROLES = frozenset({"learnlm_promote_rw"})

#: The ONLY roles permitted to move a question along the status lifecycle.
#:
#: The last column of `groups_question` to get a writer, and the one every
#: other role in this milestone is explicitly denied — `learnlm_promote_rw`
#: most pointedly, because promotion must not be able to publish what it
#: trusts. Status and trust are two independent axes and they get two roles, so
#: no single connection can both verify a question and turn it on.
ALLOWED_STATUS_ROLES = frozenset({"learnlm_status_rw"})

#: Roles refused with a specific explanation rather than the generic message,
#: because these two are the ones an operator is most likely to reach for.
REFUSED_ROLES = {
    "learnlm_census_ro": (
        "the read-only census role holds no write privilege; a capture "
        "through it fails at the server after the operator believes it began"),
    "learnlm_pilot_rw": (
        "the pilot role is scoped to the P2.7 reference pilot; pre-image rows "
        "written through it would sit outside the privilege boundary that was "
        "reviewed for them"),
    "neondb_owner": (
        "the owner can write every table in the database, including "
        "groups_question; performing a capture with it would make the "
        "least-privilege capture role decorative"),
}


class GateFailure(CommandError):
    """A gate refused. Nothing has been written."""


def resolve_operator(username):
    """
    The acting staff operator.

    Delegates to `_question_trust.resolve_operator` — the repository's existing
    convention (`is_staff` + `is_active`, not `User.role`, which is
    self-assignable on an AllowAny registration endpoint). A second
    authorization scheme would mean two definitions of "operator" that drift.
    """
    return trust.resolve_operator(username)


def database_identity(alias):
    """(database, role, version) for the connected alias. Never a password."""
    with connections[alias].cursor() as cursor:
        cursor.execute("select current_database(), current_user, version()")
        database, role, version = cursor.fetchone()
    return database, role, version.split(" on ")[0]


def describe_target(alias):
    """
    Where this connection actually is, WITHOUT refusing either way.

    `gate_production_target` answers "is this the database I demanded?" and
    raises when it is not. Some commands instead need "which database is this?"
    so they can apply the production contract when it applies and stay usable
    on a throwaway database when it does not — the reference and oracle
    commands, which predate the operator aliases and are still run against
    local databases by their own test suites.
    """
    database, role, version = database_identity(alias)
    return {"database": database, "role": role, "server_version": version,
            "is_production": database == PRODUCTION_DATABASE}


def gate_production_target(alias, *, require_production):
    """
    Confirm the connection is (or is not) the documented production database.

    `require_production=False` is for local and test use and does NOT relax any
    other gate — it only inverts what this one is looking for, and the report
    is stamped accordingly.
    """
    database, role, version = database_identity(alias)

    if require_production and database != PRODUCTION_DATABASE:
        raise GateFailure(
            f"target database is {database!r}, not the documented production "
            f"database {PRODUCTION_DATABASE!r}. Refusing: a pre-image captured "
            f"from the wrong database looks like a safety net and is not one.")

    if not require_production and database == PRODUCTION_DATABASE:
        raise GateFailure(
            f"--local was given but the connection is the production database "
            f"{database!r}. Refusing rather than quietly treating production "
            f"as a scratch target.")

    return {"database": database, "role": role, "server_version": version,
            "is_production": database == PRODUCTION_DATABASE}


def gate_writing_role(alias, allowed=None):
    """
    Only an explicitly allowed role may write.

    `allowed` selects WHICH list applies — capture roles or remediation roles.
    They are separate because the privileges are separate.

    Checked BEFORE any write is attempted, so the refusal is a message rather
    than a permission error halfway through a batch — and an allow-list rather
    than a deny-list, so a role nobody considered is refused by default instead
    of permitted by default.
    """
    permitted = ALLOWED_WRITE_ROLES if allowed is None else allowed
    _database, role, _version = database_identity(alias)
    if role in permitted:
        return role

    reason = REFUSED_ROLES.get(role)
    if reason is not None:
        raise GateFailure(
            f"connected as {role!r}, which must not perform this operation: "
            f"{reason}. Reconnect as {' or '.join(sorted(permitted))}.")

    raise GateFailure(
        f"connected as {role!r}, which is not an authorized role for this "
        f"operation. Only {' or '.join(sorted(permitted))} may perform it. "
        f"Refusing a role nobody has reviewed rather than assuming it is safe.")


#: What each operation is about to do, as (table, column-or-None, privilege).
#: The gate proves THIS, rather than a generic "can write" that happens to be
#: true for a different reason.
CAPTURE_PROBE = (("groups_questionpreimage", None, "INSERT"),)
STATEMENT_REPAIR_PROBE = (("groups_question", "content", "UPDATE"),)
HIDDEN_TEST_REPAIR_PROBE = (
    ("groups_question", "hidden_test_cases", "UPDATE"),)
CONTRACT_REPAIR_PROBE = (
    ("groups_question", "execution_contract_version", "UPDATE"),)
BOILERPLATE_REPAIR_PROBE = (
    ("groups_question", "boilerplate_code", "UPDATE"),)

#: Privileges the operation must NOT hold. Checked at the same moment as the
#: required ones, so an over-granted role is refused rather than trusted.
#: Policy becomes a runtime check instead of a comment.
STATEMENT_REPAIR_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
)

#: The mirror image for hidden-test repair: `content` becomes forbidden and
#: `hidden_test_cases` becomes the one permitted write.
HIDDEN_TEST_REPAIR_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
)


#: And the third mirror: the contract role may change which harness runs, and
#: nothing about the text, the answers or the trust boundary it runs under.
CONTRACT_REPAIR_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
)

#: Boilerplate repair's mirror: the starter becomes the one permitted write and
#: everything it might be used to reach — the statement, the keys, the contract
#: it declares a signature for — becomes forbidden.
BOILERPLATE_REPAIR_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
)


#: A status transition writes ONE column of the question.
STATUS_TRANSITION_PROBE = (("groups_question", "status", "UPDATE"),)

#: The status role's mirror of the repair deny-lists, plus the one that matters
#: most: `trust_state`. Status says "this question is available"; trust says
#: "its answers have been proven". A role that could write both could publish
#: a question AND declare it verified, which is the entire separation this
#: milestone is built on, undone by one grant.
STATUS_TRANSITION_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_questionapproval", None, "INSERT"),
    ("groups_questionapproval", None, "UPDATE"),
    ("groups_referencesolution", None, "INSERT"),
    ("groups_referencesolution", None, "UPDATE"),
    ("groups_oracleexecution", None, "INSERT"),
    ("groups_oracleexecution", None, "UPDATE"),
    # The audit trail is append-only in the model; this makes it append-only
    # in the database too. A role that can rewrite the record of what it did
    # is not audited, it is trusted — and a mutation sweep found that granting
    # UPDATE here changed nothing any test could see.
    ("groups_remediationaction", None, "UPDATE"),
    ("groups_remediationaction", None, "DELETE"),
    # The pre-image is what makes this write reversible. The role that relies
    # on one must not be able to create, alter or remove it.
    ("groups_questionpreimage", None, "INSERT"),
    ("groups_questionpreimage", None, "UPDATE"),
    ("groups_questionpreimage", None, "DELETE"),
    ("groups_remediationbatch", None, "INSERT"),
    ("groups_remediationbatch", None, "UPDATE"),
)

#: The COMPLETE production grant list for `learnlm_status_rw` (M2 P2.7h-8).
#:
#: Identical in shape to the four repair roles, because a status transition IS
#: a remediation-family write: it reads a frozen batch and a verified
#: pre-image, changes one column, and appends an append-only action. Those four
#: roles hold exactly SELECT on the question plus UPDATE on their one column,
#: SELECT on the batch and pre-image tables, and SELECT+INSERT on the action
#: table — the pre-image itself is written by `learnlm_preimage_rw` in a
#: separate, earlier command. This role is the fifth of that family.
STATUS_ROLE_GRANTS = (
    "GRANT CONNECT ON DATABASE {database} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT UPDATE (status) ON groups_question TO {role}",
    "GRANT SELECT ON groups_questionpreimage TO {role}",
    "GRANT SELECT ON groups_remediationbatch TO {role}",
    "GRANT SELECT, INSERT ON groups_remediationaction TO {role}",
    # Publication is gated on an approval, which means reading one.
    "GRANT SELECT ON groups_questionapproval TO {role}",
    "GRANT SELECT ON groups_referencesolution TO {role}",
    "GRANT SELECT ON groups_oracleexecution TO {role}",
)


#: Authoring or reviewing a reference writes ONE table.
REFERENCE_WRITE_PROBE = (
    ("groups_referencesolution", None, "INSERT"),
    ("groups_referencesolution", None, "UPDATE"),
)

#: Recording what an execution produced writes ONE table.
ORACLE_EXECUTE_PROBE = (
    ("groups_oracleexecution", None, "INSERT"),
)

#: What an oracle connection must NOT be able to do — shared by both, because
#: the boundary is the same one: it may say what the reference produced, and it
#: may never change the question, approve it, or edit the record of either.
#:
#: `groups_question` UPDATE is checked at TABLE level here on purpose. The
#: repair roles hold column-level grants, so a table-level check is true for
#: them; for this role it must be false for every column, and that is exactly
#: what the table-level question answers.
ORACLE_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "UPDATE"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_questionapproval", None, "INSERT"),
    ("groups_questionapproval", None, "UPDATE"),
    ("groups_remediationaction", None, "INSERT"),
    ("groups_remediationaction", None, "UPDATE"),
    ("groups_questionpreimage", None, "INSERT"),
    ("groups_questionpreimage", None, "UPDATE"),
    ("groups_questionpreimage", None, "DELETE"),
    ("groups_referencesolution", None, "DELETE"),
    ("groups_oracleexecution", None, "UPDATE"),
    ("groups_oracleexecution", None, "DELETE"),
)

#: Recording a human's approval writes ONE table.
APPROVAL_PROBE = (("groups_questionapproval", None, "INSERT"),)

#: The COMPLETE production grant list for `learnlm_approve_rw` (M2 P2.7h-6).
#:
#: Here rather than in a hand-written .sql file so the privileges the role is
#: given and the privileges the code needs cannot drift apart: the DDL is
#: generated from this tuple, and a test runs the real command with SET ROLE
#: against a role built from this tuple and nothing else. Every line is a read
#: the approval path actually performs, traced through the code:
#:
#:   groups_question           `resolve_question`, through the alias
#:   groups_referencesolution  `canonical_reference`, via the question's
#:                             related manager, which inherits the alias
#:   groups_oracleexecution    `collect_case_evidence` and
#:                             `_execution_provenance` — the evidence the
#:                             digest is computed from, read on the SAME
#:                             connection as the question (P2.7h-6)
#:   groups_user (id ONLY)     Django validates FK existence in `full_clean`
#:                             through the object's own connection. It issues
#:                             `SELECT 1 ... WHERE id = %s`, so column-scoped
#:                             SELECT on the primary key is enough — the role
#:                             cannot read an email or a password hash.
#:   groups_questionapproval   INSERT for the row; SELECT because PostgreSQL
#:                             requires it for the `RETURNING id` Django uses
#:                             to populate the pk, and because the duplicate
#:                             check reads existing approvals.
#:
#: Not present, deliberately: any write on groups_question (an approver must
#: not be able to enact its own judgement), any write on the reference or the
#: executions (it must not be able to manufacture the evidence it vouches for),
#: and anything at all on the pre-image, remediation or submission tables.
#:
#: Also NOT present: a grant on `groups_questionapproval_id_seq`. It was in the
#: first draft of this list, on the assumption the pk is a `bigserial`. It is
#: `GENERATED BY DEFAULT AS IDENTITY`, whose sequence is owned by the table and
#: needs no separate privilege — the minimality test proved the grant does
#: nothing, so it came out rather than staying in "to be safe".
APPROVAL_ROLE_GRANTS = (
    "GRANT CONNECT ON DATABASE {database} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT SELECT ON groups_referencesolution TO {role}",
    "GRANT SELECT ON groups_oracleexecution TO {role}",
    "GRANT SELECT (id) ON groups_user TO {role}",
    "GRANT SELECT, INSERT ON groups_questionapproval TO {role}",
)

#: Promotion writes ONE column of the question and TWO of the approval.
#:
#: An earlier version demanded UPDATE on `groups_question.status` as well. The
#: command never writes `status` — it refuses a DRAFT rather than advancing one
#: — so the probe was demanding a privilege nothing uses, and a role built to
#: satisfy it could publish a question as a side effect of being allowed to
#: promote one. `status` is on the forbidden list now instead.
#:
#: The two approval columns are the promotion stamp. `question_promote` writes
#: `approval.promoted_at` and `approval.promoted_by`, which the previous
#: contract simultaneously required (by performing it) and forbade (by listing
#: table-level UPDATE on groups_questionapproval as excess privilege). That
#: contract could not be satisfied by any role: grant it and the forbidden
#: check refuses, withhold it and the write fails mid-transaction.
PROMOTION_PROBE = (
    ("groups_question", "trust_state", "UPDATE"),
    ("groups_questionapproval", "promoted_at", "UPDATE"),
    ("groups_questionapproval", "promoted_by_id", "UPDATE"),
)

#: An approver records a judgement; it must not be able to enact it.
APPROVAL_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "UPDATE"),
    ("groups_question", None, "DELETE"),
    ("groups_questionapproval", None, "UPDATE"),
    ("groups_questionapproval", None, "DELETE"),
    ("groups_referencesolution", None, "INSERT"),
    ("groups_referencesolution", None, "UPDATE"),
    ("groups_oracleexecution", None, "INSERT"),
    ("groups_oracleexecution", None, "UPDATE"),
)

#: A promoter enacts a judgement; it must not be able to author one, nor to
#: reach any of the grading truth the approved artifact pins down.
#: Column-scoped throughout, because a table-level UPDATE check returns TRUE
#: when the role holds UPDATE on ANY column — it cannot tell "may stamp the
#: promotion" from "may rewrite the approval". Every column of
#: `groups_question` except `trust_state`, and every column of
#: `groups_questionapproval` except the two stamp columns, is named here, so
#: the deny-list is complete rather than representative.
PROMOTION_FORBIDDEN = (
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", "title", "UPDATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "base_difficulty", "UPDATE"),
    ("groups_question", "topic_id", "UPDATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    # Promotion does not publish. Advancing `status` is a separate decision
    # with its own consequences — a PUBLISHED + ORACLE_VERIFIED question is
    # the only kind whose submissions teach the adaptive model.
    ("groups_question", "status", "UPDATE"),
    ("groups_questionapproval", None, "INSERT"),
    ("groups_questionapproval", None, "DELETE"),
    ("groups_questionapproval", "question_id", "UPDATE"),
    ("groups_questionapproval", "reference_id", "UPDATE"),
    ("groups_questionapproval", "reference_source_hash", "UPDATE"),
    ("groups_questionapproval", "artifact_digest", "UPDATE"),
    ("groups_questionapproval", "artifact_schema_version", "UPDATE"),
    ("groups_questionapproval", "quality_outcome", "UPDATE"),
    ("groups_questionapproval", "approved_by_id", "UPDATE"),
    ("groups_questionapproval", "approved_at", "UPDATE"),
    ("groups_questionapproval", "reviewed_by_id", "UPDATE"),
    ("groups_questionapproval", "reviewed_at", "UPDATE"),
    ("groups_questionapproval", "executed_by_id", "UPDATE"),
    ("groups_questionapproval", "executed_at", "UPDATE"),
    ("groups_questionapproval", "created_at", "UPDATE"),
    ("groups_referencesolution", None, "INSERT"),
    ("groups_referencesolution", None, "UPDATE"),
    ("groups_oracleexecution", None, "INSERT"),
    ("groups_oracleexecution", None, "UPDATE"),
)

#: The COMPLETE production grant list for `learnlm_promote_rw` (M2 P2.7h-7).
#:
#: Same discipline as `APPROVAL_ROLE_GRANTS`: the DDL is generated from this
#: tuple, and the tests build a role from it over a real second connection and
#: prove it both sufficient and minimal. Every line traced to the code:
#:
#:   groups_question SELECT      `resolve_question`, through the alias
#:   groups_question UPDATE      one column — `trust_state`. The row is also
#:     (trust_state)             locked FOR UPDATE, which PostgreSQL permits on
#:                               the strength of an UPDATE privilege on any
#:                               column of the table.
#:   groups_referencesolution    `canonical_reference`, re-derived live rather
#:     SELECT                    than trusted from the approval
#:   groups_oracleexecution      `collect_case_evidence`, rebuilt so the digest
#:     SELECT                    is recomputed from provenance, not read back
#:   groups_questionapproval     `current_for`, and the promotion stamp. UPDATE
#:     SELECT + UPDATE           is column-scoped to the two stamp columns, so
#:     (promoted_at,             the promoter cannot alter the judgement it is
#:      promoted_by_id)          acting on — not the digest, not the quality
#:                               verdict, not who approved it.
#:
#: Not present, unlike the approval role: SELECT on `groups_user(id)`. The
#: approval path calls `full_clean` on a new row, which makes Django verify
#: every FK through the object's own connection; promotion writes named columns
#: on rows that already exist, so no FK is ever validated. The minimality test
#: proved the grant unnecessary and it came out.
#:
#: Not present: INSERT on groups_questionapproval. A promoter that could author
#: an approval could promote anything it liked; the whole point of splitting
#: the two roles is that the database refuses it.
PROMOTION_ROLE_GRANTS = (
    "GRANT CONNECT ON DATABASE {database} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT UPDATE (trust_state) ON groups_question TO {role}",
    "GRANT SELECT ON groups_referencesolution TO {role}",
    "GRANT SELECT ON groups_oracleexecution TO {role}",
    "GRANT SELECT ON groups_questionapproval TO {role}",
    "GRANT UPDATE (promoted_at, promoted_by_id) ON groups_questionapproval "
    "TO {role}",
)

# ═══════════════════════════════════════════════════════════════════════
# RESEED AUTHORITY (M2 P2.7h-17) — DESIGN ONLY. No role below exists on
# production; nothing here has been granted. These constants are the
# proposal, written as code so the tests can hold them to it.
# ═══════════════════════════════════════════════════════════════════════

#: The orchestrator's own authority. It coordinates the reseed and writes the
#: ledger — and that is ALL it can write. It holds no content authority of any
#: kind, so a compromised or buggy coordinator cannot alter a question, its
#: answers, its trust or its audit trail. Each stage is performed by the
#: existing role that already owns that column, invoked under its own alias.
ALLOWED_RESEED_ROLES = frozenset({"learnlm_reseed_rw"})

#: Statement GENERATION reuses the statement REPAIR role unchanged. Both write
#: exactly `content` and nothing else, so the privilege is already correct and
#: no new grant is proposed. What separates them is the command precondition
#: and the audit class, not the authority — see the two probes below, which
#: are deliberately identical and asserted to be so.
ALLOWED_STATEMENT_GENERATION_ROLES = ALLOWED_REMEDIATION_ROLES

#: Signature DECLARATION reuses the boilerplate role, for a reason worth
#: stating rather than assuming: PostgreSQL grants are per COLUMN, and both an
#: annotation repair and a signature declaration write the same column,
#: `boilerplate_code`. A dedicated role would therefore need identical grants
#: and would have an identical blast radius — it would separate credentials,
#: not capabilities. The separation that actually protects a published
#: question is the command's precondition, and that is where it is placed.
ALLOWED_SIGNATURE_ROLES = ALLOWED_BOILERPLATE_ROLES

#: Generation writes ONE column, the same one repair writes.
STATEMENT_GENERATION_PROBE = STATEMENT_REPAIR_PROBE
STATEMENT_GENERATION_FORBIDDEN = STATEMENT_REPAIR_FORBIDDEN

#: Declaring a signature writes ONE column, the same one an annotation repair
#: writes. `hidden_test_cases` is forbidden for the reason that motivates this
#: whole action class: the declared signature is what every hidden case is
#: bound against, so a role able to move both could redefine the question and
#: the answers together and leave them consistent with each other.
SIGNATURE_DECLARATION_PROBE = BOILERPLATE_REPAIR_PROBE
SIGNATURE_DECLARATION_FORBIDDEN = BOILERPLATE_REPAIR_FORBIDDEN

#: Writing the ledger writes ONE table.
RESEED_LEDGER_PROBE = (
    ("groups_reseedledger", None, "INSERT"),
    ("groups_reseedledger", "stage", "UPDATE"),
)

#: The ledger writer's deny-list, and the longest one in this module on
#: purpose. This role exists to record progress; every capability that could
#: turn a progress record into an authority is refused here.
RESEED_LEDGER_FORBIDDEN = (
    # Not one column of the question. The coordinator coordinates.
    ("groups_question", None, "INSERT"),
    ("groups_question", None, "DELETE"),
    ("groups_question", None, "TRUNCATE"),
    ("groups_question", "content", "UPDATE"),
    ("groups_question", "boilerplate_code", "UPDATE"),
    ("groups_question", "hidden_test_cases", "UPDATE"),
    ("groups_question", "hidden_wrapper_code", "UPDATE"),
    ("groups_question", "execution_contract_version", "UPDATE"),
    ("groups_question", "status", "UPDATE"),
    ("groups_question", "trust_state", "UPDATE"),
    # A ledger row must not be retargeted after it is created. Column-level
    # UPDATE is what makes "this row is about question N" permanent: the
    # writer may advance a row's stage forever and can never move it to a
    # different question or a different batch.
    ("groups_reseedledger", "question_id", "UPDATE"),
    ("groups_reseedledger", "batch_id", "UPDATE"),
    # Advancing is allowed; forgetting is not. A row that can be deleted is a
    # record that a question was touched and then un-recorded.
    ("groups_reseedledger", None, "DELETE"),
    ("groups_reseedledger", None, "TRUNCATE"),
    # No audit authority. The audit trail is written by the roles that
    # actually change something, under their own credentials. A coordinator
    # able to append actions could record work it never performed — which is
    # worse than no record, because it reads as evidence.
    ("groups_remediationaction", None, "INSERT"),
    ("groups_remediationaction", None, "UPDATE"),
    ("groups_remediationaction", None, "DELETE"),
    # Nothing in the trust chain.
    ("groups_questionapproval", None, "INSERT"),
    ("groups_questionapproval", None, "UPDATE"),
    ("groups_referencesolution", None, "INSERT"),
    ("groups_referencesolution", None, "UPDATE"),
    ("groups_oracleexecution", None, "INSERT"),
    ("groups_oracleexecution", None, "UPDATE"),
    # Nor the rollback path it depends on.
    ("groups_questionpreimage", None, "INSERT"),
    ("groups_questionpreimage", None, "UPDATE"),
    ("groups_questionpreimage", None, "DELETE"),
    ("groups_remediationbatch", None, "INSERT"),
    ("groups_remediationbatch", None, "UPDATE"),
    # Learner history is not the coordinator's business either.
    ("groups_codesubmission", None, "INSERT"),
    ("groups_codesubmission", None, "UPDATE"),
    ("groups_codesubmission", None, "DELETE"),
)

#: The COMPLETE proposed grant list for `learnlm_reseed_rw`.
#:
#: No sequence grant: `groups_reseedledger.id` is GENERATED BY DEFAULT AS
#: IDENTITY, so INSERT alone supplies it — the same finding that removed a
#: sequence grant from the approval role. A minimality test proves every line
#: here is load-bearing.
RESEED_ROLE_GRANTS = (
    "GRANT CONNECT ON DATABASE {database} TO {role}",
    "GRANT USAGE ON SCHEMA public TO {role}",
    # Read-only on everything it coordinates: it must be able to SEE whether a
    # stage is already done, and never to do it.
    "GRANT SELECT ON groups_question TO {role}",
    "GRANT SELECT ON groups_remediationbatch TO {role}",
    "GRANT SELECT, INSERT ON groups_reseedledger TO {role}",
    "GRANT UPDATE (stage, last_error, attempts, updated_at) "
    "ON groups_reseedledger TO {role}",
)

#: Tables the reseed authoring PRECONDITIONS must read (M2 P2.7h-30).
#:
#: `reseed_authoring.stub_blockers` proves a question carries no grading truth
#: by querying `QuestionApproval` and `OracleExecution`. Every authoring
#: command runs it before it writes, so every authoring role needs SELECT on
#: both — and none of them had it.
#:
#: ── Why no test caught this ─────────────────────────────────────────────
#:
#: `gate_write_privilege` probes what a command WRITES. Nothing probed what
#: its preconditions READ. And every command test passes `--local`, where the
#: throwaway database is owned by a role holding everything, so the narrow
#: production roles were never the ones executing `stub_blockers`.
#:
#: It surfaced in the Phase 16 dry run — the first time an authoring command
#: was pointed at production under its own role. The dry run is why it cost a
#: refusal instead of a half-written pilot.
#:
#: These are SELECT only. They widen no write authority: these roles already
#: read `groups_question`, which carries status and trust_state, so knowing
#: whether an approval row exists is strictly less than they can already see.
RESEED_AUTHORING_READS = (
    "groups_questionapproval",
    "groups_oracleexecution",
)

#: The roles that run `stub_blockers` and therefore need those reads.
RESEED_AUTHORING_ROLES = (
    "learnlm_remediate_rw",      # reseed_statement
    "learnlm_boilerplate_rw",    # declare_signature
    "learnlm_contract_rw",       # reseed_contract
)

RESEED_AUTHORING_READ_GRANTS = tuple(
    f"GRANT SELECT ON {table} TO {{role}}"
    for table in RESEED_AUTHORING_READS
)


#: Every captured column, for deriving a rollback's forbidden list.
CAPTURED_COLUMNS = ("content", "status", "trust_state",
                    "execution_contract_version", "boilerplate_code",
                    "hidden_wrapper_code", "hidden_test_cases")


def rollback_privileges(required_writes):
    """
    (required, forbidden) for a rollback that will write `required_writes`.

    Derived from the data rather than fixed: restoring q266's answer form needs
    UPDATE on `hidden_test_cases` and must NOT be permitted to touch a statement
    or a contract, while restoring a statement repair needs `content` and must
    not touch the keys. Everything captured but not being restored is forbidden,
    so an over-granted role is refused rather than quietly used.
    """
    writing = {column for _table, column, _privilege in required_writes}
    forbidden = [("groups_question", None, "INSERT"),
                 ("groups_question", None, "DELETE"),
                 ("groups_question", None, "TRUNCATE")]
    forbidden.extend(("groups_question", column, "UPDATE")
                     for column in CAPTURED_COLUMNS if column not in writing)
    return tuple(required_writes), tuple(forbidden)


def _has_privilege(cursor, table, column, privilege):
    """Column privilege when a column is named, table privilege otherwise."""
    if column is None:
        cursor.execute("select has_table_privilege(current_user, %s, %s)",
                       [table, privilege])
    else:
        cursor.execute(
            "select has_column_privilege(current_user, %s, %s, %s)",
            [table, column, privilege])
    return cursor.fetchone()[0]


def gate_write_privilege(alias, required=None, forbidden=()):
    """
    The role can perform exactly the operation about to be performed — and
    cannot perform the ones it must not.

    ── Why this is parameterised by OPERATION and not by table ─────────────

    An earlier version took a table and defaulted the privilege to INSERT,
    because it was written for capture. Remediation reused it with the table
    swapped, so a statement repair demanded INSERT on `groups_question` — a
    privilege it neither needs nor should ever hold. The role was correct and
    the gate was wrong, and it failed in the safe direction only by luck.

    Naming the (table, column, privilege) triple removes the guess: the gate
    asks the database whether the exact write will be permitted.

    `has_column_privilege` for column-scoped grants. A table-level UPDATE check
    returns TRUE when the role holds UPDATE on ANY column, so it cannot
    distinguish "UPDATE on content" from "UPDATE on everything" — using it here
    would report the narrow grant and a dangerous one identically.

    `forbidden` turns the written contract into an enforced one: a role granted
    more than its operation needs is refused, rather than quietly used.
    """
    required = required or CAPTURE_PROBE
    with connections[alias].cursor() as cursor:
        for table, column, privilege in required:
            if not _has_privilege(cursor, table, column, privilege):
                target = f"{table}.{column}" if column else table
                raise GateFailure(
                    f"the connected role cannot {privilege} on {target}. "
                    f"Refusing before starting rather than failing part-way "
                    f"through.")

        excess = []
        for table, column, privilege in forbidden:
            if _has_privilege(cursor, table, column, privilege):
                excess.append(f"{privilege} on "
                              f"{table + '.' + column if column else table}")
    if excess:
        raise GateFailure(
            f"the connected role holds privileges this operation must not "
            f"have: {', '.join(excess)}. Refusing: a role that can do more "
            f"than the operation needs makes the narrow grant decorative.")
    return True


def gate_no_write_privilege(alias, forbidden):
    """
    The role can perform NONE of `forbidden`. No required privilege at all.

    A separate entry point rather than `gate_write_privilege(required=())`,
    because that function reads `required or CAPTURE_PROBE` — so an empty
    tuple silently becomes "must be able to INSERT a pre-image", and a
    read-only caller asking to be checked for excess would be refused for
    lacking a write it must never hold. The same shape of bug the
    `gate_write_privilege` docstring describes, one caller further on.
    """
    with connections[alias].cursor() as cursor:
        excess = [f"{privilege} on "
                  f"{table + '.' + column if column else table}"
                  for table, column, privilege in forbidden
                  if _has_privilege(cursor, table, column, privilege)]
    if excess:
        raise GateFailure(
            f"this operation must hold no write privilege, but the connected "
            f"role holds: {', '.join(sorted(excess))}")


def require_confirmation(confirmed, action, identity):
    """
    Explicit per-command confirmation for a production write.

    Deliberately not a shared `--yes`: the operator confirms THIS action on
    THIS database, and the flag name says which.
    """
    if identity["is_production"] and not confirmed:
        raise GateFailure(
            f"refusing to {action} against production database "
            f"{identity['database']!r} without --confirm. Re-run with "
            f"--confirm once the plan above is what you intend.")
    return True


def run_gates(alias, operator_name, *, action, confirmed, require_production,
              needs_write, allowed_roles=None, required_privileges=None,
              forbidden_privileges=()):
    """
    Every gate, in order, before anything is written.

    Returns (operator, identity). Raises `GateFailure` otherwise — and a raise
    means nothing has been written, because every gate runs first.
    """
    operator = resolve_operator(operator_name)
    identity = gate_production_target(alias, require_production=require_production)
    if needs_write:
        # The ALLOW-list applies to production only. On a local or test
        # database the connected role is whatever that throwaway instance
        # uses, and demanding the production capture role there would make the
        # gates untestable — which is how gates end up unverified.
        # `gate_write_privilege` still runs everywhere: it asks whether this
        # connection can actually do the work, which is true regardless.
        # The least-privilege contract is a PRODUCTION property. A local test
        # database is owned by whatever role that throwaway instance uses —
        # usually one holding everything — so demanding the narrow grant there
        # would make the gates untestable, which is how gates end up
        # unverified. The `forbidden` check is still exercised directly against
        # purpose-made roles in `test_remediation_role_contract`.
        #
        # `required` runs EVERYWHERE: "can this connection do the work" is a
        # fair question on any database.
        if identity["is_production"]:
            gate_writing_role(alias, allowed=allowed_roles)
            gate_write_privilege(alias, required=required_privileges,
                                 forbidden=forbidden_privileges)
        else:
            gate_write_privilege(alias, required=required_privileges)
        require_confirmation(confirmed, action, identity)
    return operator, identity


def render_identity(command, identity, operator):
    """The header every command prints. No password, no connection string."""
    write = command.stdout.write
    write(f"  database        {identity['database']}")
    write(f"  role            {identity['role']}")
    write(f"  server          {identity['server_version']}")
    write(f"  production      {identity['is_production']}")
    write(f"  operator        {operator.username}")
