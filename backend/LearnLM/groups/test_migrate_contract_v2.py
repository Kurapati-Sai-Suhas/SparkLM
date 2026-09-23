"""
The v1 -> v2 contract migration command (Phase 1 M14).

WHAT IS BEING PROTECTED
    `migrate_contract_v2` changes what a question's stored inputs MEAN: under
    v1 a tree argument arrives as a string, under v2 it is built into a node.
    Every stored expected output is then read under a representation it was
    never checked against. A wrong migration does not fail loudly -- it
    produces a question that grades differently while every record says
    nothing moved.

HOW THE TESTS ARE BUILT
    Each refusal test arranges a question that passes EVERY gate except the
    one under test, so a refusal can only have come from that gate -- and so
    the mutation tests can attribute a kill to a specific protection rather
    than to whichever other gate happened to fire first.

    Every refusal test then asserts the WHOLE observable database is
    unchanged: the question's seven captured fields, every audit row, every
    pre-image and every batch. "It raised" is not the property; "it raised
    and nothing moved" is.

Local/synthetic database only. Nothing here touches production, and no real
question -- q100 included -- is migrated.
"""

import copy
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, connections, transaction

from groups import contract_migration, migration_readiness, pre_image
from groups.management.commands import migrate_contract_v2 as cmd
from groups.models import (
    CodingPortal, Question, QuestionPreImage, RemediationAction,
    RemediationBatch, Topic,
)

User = get_user_model()

# ── q100's real shape ─────────────────────────────────────────────────────

SAME_TREE = ("class Solution:\n"
             "    def isSameTree(self, tree1: TreeNode | None, "
             "tree2: TreeNode | None) -> bool:\n"
             "        pass\n")

#: q100's four stored cases, verbatim.
SAME_TREE_CASES = [
    {"stdin": "[1,2,3]\n[1,2,3]", "expected_output": "true"},
    {"stdin": "[1,2]\n[1,null,2]", "expected_output": "false"},
    {"stdin": "[1,2,1]\n[1,1,2]", "expected_output": "false"},
    {"stdin": "[1]\n[1]", "expected_output": "true"},
]

#: Returns a tree -- output canonicality is checked.
INSERT_INTO_BST = ("class Solution:\n"
                   "    def insertIntoBST(self, root: 'TreeNode', val: int) "
                   "-> 'TreeNode':\n"
                   "        pass\n")

NODE_STARTER = ("class Solution:\n"
                "    def cloneGraph(self, node: 'Node') -> 'Node':\n"
                "        pass\n")

COLLECTION_STARTER = ("class Solution:\n"
                      "    def generateTrees(self, n: int) -> list[TreeNode]:\n"
                      "        pass\n")

NOT_STRUCTURAL = ("class Solution:\n"
                  "    def twoSum(self, nums: list[int], target: int) "
                  "-> list[int]:\n"
                  "        pass\n")

APPLY = ("--apply", "--confirm")
BATCH = "m14-test"


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def operator(db):
    return User.objects.create_user(username="m14-op", password="pw",
                                    email="m14@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M14 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M14Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, *, starter=SAME_TREE, cases=None,
                  version="v1", wrapper=None, content="Statement."):
    return Question.objects.create(
        id=question_id, title=f" Q{question_id}", content=content, topic=topic,
        base_difficulty=1000.0,
        boilerplate_code={"python": starter},
        hidden_test_cases=copy.deepcopy(
            list(cases if cases is not None else SAME_TREE_CASES)),
        hidden_wrapper_code=wrapper or {},
        execution_contract_version=version)


def freeze(operator, *questions, key=BATCH):
    batch = RemediationBatch.objects.create(
        batch_key=key, purpose="m14 test", created_by=operator)
    for question in questions:
        pre_image.capture(batch, question, operator)
    pre_image.freeze(batch, operator)
    return batch


@pytest.fixture
def q100(db, topic):
    """A q100 stand-in: same starter, same four cases, v1, UNVERIFIED, DRAFT."""
    return make_question(topic, 100)


@pytest.fixture
def control(db, topic):
    """Stands in for q110 -- captured in the same batch, and must never move."""
    return make_question(topic, 110, content="Control.")


@pytest.fixture
def frozen(db, operator, q100, control):
    return freeze(operator, q100, control)


def migrate(operator, question_id=100, *, batch=BATCH, version="v2",
            extra=()):
    call_command("migrate_contract_v2", "--batch", batch,
                 "--question", str(question_id), "--to-version", version,
                 "--operator", operator.username, "--local", *extra)


def world(*question_ids):
    """Everything a migration could conceivably touch, as plain data."""
    return {
        "questions": {
            qid: copy.deepcopy(pre_image.question_state(
                Question.objects.get(pk=qid)))
            for qid in question_ids},
        "actions": sorted(RemediationAction.objects.values_list(
            "pk", "action_class", "question_id")),
        "pre_images": sorted(QuestionPreImage.objects.values_list(
            "pk", "question_id", "state_digest")),
        "batches": sorted(RemediationBatch.objects.values_list(
            "batch_key", "state", "frozen_at")),
    }


def assert_refused_and_untouched(operator, question_ids, *, match,
                                 question_id=100, **kwargs):
    before = world(*question_ids)
    with pytest.raises(CommandError, match=match):
        migrate(operator, question_id, **kwargs)
    assert world(*question_ids) == before


# ═════════════════════════════════════════════════════════════
# Phase 10 -- the positive q100 path, on a FIXTURE
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_q100_fixture_migrates_and_only_the_contract_moves(
        frozen, q100, control, operator):
    before = world(100, 110)

    migrate(operator, extra=APPLY)

    after = world(100, 110)
    migrated = after["questions"][100]
    assert migrated["execution_contract_version"] == "v2"
    for name, value in before["questions"][100].items():
        if name != "execution_contract_version":
            assert migrated[name] == value, f"{name} moved"
    assert after["questions"][110] == before["questions"][110]


@pytest.mark.django_db
def test_q100_fixture_keeps_every_expected_output_byte_for_byte(
        frozen, q100, operator):
    migrate(operator, extra=APPLY)

    q100.refresh_from_db()
    assert q100.hidden_test_cases == SAME_TREE_CASES
    assert q100.trust_state == Question.TRUST_UNVERIFIED
    assert q100.status == Question.STATUS_DRAFT


@pytest.mark.django_db
def test_a_migration_writes_exactly_one_audit_row(frozen, q100, operator):
    migrate(operator, extra=APPLY)

    actions = RemediationAction.objects.filter(question=q100)
    assert actions.count() == 1
    action = actions.get()
    assert action.action_class == RemediationAction.CLASS_CONTRACT_MIGRATION
    assert action.batch.batch_key == BATCH
    assert action.applied_by == operator
    assert action.pre_image == QuestionPreImage.objects.get(
        batch=frozen, question=q100)
    assert action.applied_at is not None
    for field in ("reason=M14_STRUCTURAL_MIGRATION", "from=v1", "to=v2",
                  "classifier=SAFE_TO_MIGRATE", "parameter_kinds=tree,tree",
                  "expected_outputs_digest=", "hidden_test_cases_digest=",
                  "content_digest=", "boilerplate_code_digest="):
        assert field in action.detail


@pytest.mark.django_db
def test_the_audit_digests_describe_the_stored_answers(frozen, q100, operator):
    expected = contract_migration.component_digests(
        pre_image.question_state(q100))["expected_outputs"]

    migrate(operator, extra=APPLY)

    detail = RemediationAction.objects.get(question=q100).detail
    assert f"expected_outputs_digest={expected}" in detail


@pytest.mark.django_db
def test_the_pre_image_still_holds_v1_for_rollback(frozen, q100, operator):
    migrate(operator, extra=APPLY)

    record = QuestionPreImage.objects.get(batch=frozen, question=q100)
    assert record.captured_state()["execution_contract_version"] == "v1"
    pre_image.verify(record)


# ═════════════════════════════════════════════════════════════
# Phase 6 -- dry run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_dry_run_writes_nothing_and_reports_the_plan(
        frozen, q100, control, operator, capsys):
    before = world(100, 110)

    migrate(operator)

    assert world(100, 110) == before
    out = capsys.readouterr().out
    for fragment in ("DRY RUN", "current version 'v1'", "target version  'v2'",
                     "SAFE_TO_MIGRATE", "cases           4",
                     "2/2 ok", "expected_outputs", "pre-image",
                     "planned mutation", "planned audit row",
                     "CONTRACT_MIGRATION"):
        assert fragment in out, fragment


@pytest.mark.django_db
def test_dry_run_before_any_pre_image_reports_it_missing(q100, operator,
                                                         capsys):
    # The dry run is useful BEFORE the freeze: it validates eligibility, then
    # names the missing pre-image as the blocker -- and still writes nothing.
    assert_refused_and_untouched(operator, [100], match="no batch")
    assert "MISSING" in capsys.readouterr().out


# ═════════════════════════════════════════════════════════════
# Phase 9 -- every refusal, and nothing moves
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_1_refuses_a_question_already_on_v2(operator, topic):
    question = make_question(topic, 100, version="v2")
    freeze(operator, question)
    assert_refused_and_untouched(operator, [100], match="grades under v2",
                                 extra=APPLY)


@pytest.mark.django_db
def test_2_refuses_a_v3_question(operator, topic):
    question = make_question(topic, 100, version="v3")
    freeze(operator, question)
    assert_refused_and_untouched(operator, [100], match="grades under v3",
                                 extra=APPLY)


@pytest.mark.django_db
def test_3_refuses_a_question_the_classifier_does_not_call_safe(operator,
                                                                topic):
    # Structural return whose stored answer has a trailing null -- q450's
    # defect. The answer key would stop matching once output is canonical.
    question = make_question(topic, 100, starter=INSERT_INTO_BST, cases=[
        {"stdin": "[5,3,6,2,4,null,7]\n7", "expected_output": "[5,3,6,2,4,null]"}])
    freeze(operator, question)
    assert_refused_and_untouched(
        operator, [100], match="BLOCKED_BY_TEST_CONTRACT_OUTPUT", extra=APPLY)


@pytest.mark.django_db
def test_4_refuses_the_quarantined_question(operator, topic):
    # q98's SHAPE is perfectly migratable -- the hold is about whether its
    # stored answer is true, which nothing structural can settle.
    question = make_question(topic, 98, cases=[
        {"stdin": "[1]\n[1]", "expected_output": "true"}])
    freeze(operator, question)
    assert_refused_and_untouched(operator, [98], match="QUARANTINED",
                                 question_id=98, extra=APPLY)


@pytest.mark.django_db
def test_5_refuses_without_a_pre_image_for_this_question(operator, q100,
                                                         control):
    freeze(operator, control)                 # the batch exists; q100 is not in it
    assert_refused_and_untouched(operator, [100, 110], match="no pre-image",
                                 extra=APPLY)


@pytest.mark.django_db
def test_5b_refuses_an_unfrozen_batch(operator, q100):
    batch = RemediationBatch.objects.create(
        batch_key=BATCH, purpose="m14 test", created_by=operator)
    pre_image.capture(batch, q100, operator)  # captured, never frozen
    assert_refused_and_untouched(operator, [100], match="not frozen",
                                 extra=APPLY)


@pytest.mark.django_db
def test_5c_refuses_a_pre_image_that_no_longer_verifies(frozen, q100,
                                                        operator):
    # The third thing `require_pre_image` guarantees: the anchor still matches
    # its OWN recorded digest. A tampered or corrupted pre-image restores the
    # wrong question, which is worse than having none.
    #
    # The DIGEST is tampered, not the captured content. Tampering `content`
    # would also be caught by the staleness gate (the pre-image would no
    # longer match the live row), so this test would pass even with digest
    # verification gone -- which mutation testing showed it did.
    record = QuestionPreImage.objects.get(batch=frozen, question=q100)
    QuestionPreImage.objects.filter(pk=record.pk).update(state_digest="0" * 64)
    assert_refused_and_untouched(
        operator, [100], match="does not match its recorded digest",
        extra=APPLY)


@pytest.mark.django_db
def test_6_refuses_a_stale_pre_image(frozen, q100, operator):
    Question.objects.filter(pk=100).update(content="Edited after the freeze.")
    assert_refused_and_untouched(operator, [100], match="changed since its "
                                 "pre-image was frozen: content", extra=APPLY)


@pytest.mark.django_db
def test_7_refuses_when_a_stored_input_changed_after_the_freeze(frozen, q100,
                                                                operator):
    cases = copy.deepcopy(SAME_TREE_CASES)
    cases[0]["stdin"] = "[1,2,3]\n[1,2,4]"
    Question.objects.filter(pk=100).update(hidden_test_cases=cases)
    assert_refused_and_untouched(operator, [100], match="stored inputs",
                                 extra=APPLY)


@pytest.mark.django_db
def test_8_refuses_when_an_expected_output_changed_after_the_freeze(
        frozen, q100, operator):
    cases = copy.deepcopy(SAME_TREE_CASES)
    cases[1]["expected_output"] = "true"
    Question.objects.filter(pk=100).update(hidden_test_cases=cases)
    assert_refused_and_untouched(operator, [100], match="expected outputs",
                                 extra=APPLY)


@pytest.mark.django_db
def test_9_refuses_a_question_with_no_test_cases(operator, topic):
    question = make_question(topic, 100, cases=[])
    freeze(operator, question)
    assert_refused_and_untouched(operator, [100], match="no hidden test cases",
                                 extra=APPLY)


@pytest.mark.django_db
def test_10_refuses_a_genuine_arity_mismatch(operator, topic):
    question = make_question(topic, 100, cases=[
        {"stdin": "[1]\n[1]\n6", "expected_output": "true"}])
    freeze(operator, question)
    assert_refused_and_untouched(
        operator, [100], match="BLOCKED_BY_TEST_CONTRACT_ARITY", extra=APPLY)


@pytest.mark.django_db
def test_11_refuses_when_the_verdict_changes_before_the_transaction(
        frozen, q100, operator, monkeypatch):
    # The plan sees SAFE; the locked re-check inside the transaction sees
    # something else. Without the in-transaction re-run, the write proceeds
    # on a verdict that no longer holds.
    real = migration_readiness.classify
    calls = []

    def verdict_flips(question_id, *args, **kwargs):
        calls.append(question_id)
        verdict = real(question_id, *args, **kwargs)
        if len(calls) >= 2:
            return migration_readiness.Classification(
                question_id, migration_readiness.BLOCKED_BY_TEST_CONTRACT_OUTPUT,
                "changed underneath the plan")
        return verdict

    monkeypatch.setattr(migration_readiness, "classify", verdict_flips)
    assert_refused_and_untouched(
        operator, [100], match="stopped being migratable between the plan",
        extra=APPLY)
    assert len(calls) == 2


@pytest.mark.django_db
def test_an_edit_between_the_plan_and_the_lock_is_caught(
        frozen, q100, operator, monkeypatch):
    # The plan's staleness check has already PASSED when someone edits the
    # statement. Only the re-check on the locked row can see it -- and a
    # content edit does not change the classifier's verdict, so this proves
    # the in-transaction staleness gate specifically, not the eligibility one.
    real = contract_migration.component_digests
    calls = []

    def edit_after_the_plan(state):
        calls.append(1)
        if len(calls) == 1:             # the plan's call, before the lock
            Question.objects.filter(pk=100).update(content="Edited mid-run.")
        return real(state)

    monkeypatch.setattr(contract_migration, "component_digests",
                        edit_after_the_plan)
    with pytest.raises(CommandError, match="stopped being migratable"):
        migrate(operator, extra=APPLY)

    q100.refresh_from_db()
    assert q100.execution_contract_version == "v1"
    assert not RemediationAction.objects.filter(question=q100).exists()


@pytest.mark.django_db
def test_12_refuses_a_missing_batch(operator, q100):
    assert_refused_and_untouched(operator, [100], match="no batch 'nope'",
                                 batch="nope", extra=APPLY)


@pytest.mark.django_db
def test_13_refuses_apply_without_confirm_even_locally(frozen, q100,
                                                       operator):
    assert_refused_and_untouched(operator, [100], match="requires --confirm",
                                 extra=("--apply",))


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["v3", "v1", "V2", ""])
def test_14_refuses_any_target_but_v2(frozen, q100, operator, target):
    assert_refused_and_untouched(operator, [100], match="is refused",
                                 version=target, extra=APPLY)


@pytest.mark.django_db
@pytest.mark.parametrize("starter,cases,match", [
    (NODE_STARTER, [{"stdin": "[[2]]", "expected_output": "[[2]]"}],
     "UNSUPPORTED_NODE"),
    (COLLECTION_STARTER, [{"stdin": "1", "expected_output": "[[1]]"}],
     "UNSUPPORTED_COLLECTION"),
    (NOT_STRUCTURAL, [{"stdin": "[2,7]\n9", "expected_output": "[0,1]"}],
     "not a structural question"),
])
def test_15_refuses_an_unexpected_structural_type(operator, topic, starter,
                                                  cases, match):
    question = make_question(topic, 100, starter=starter, cases=cases)
    freeze(operator, question)
    assert_refused_and_untouched(operator, [100], match=match, extra=APPLY)


@pytest.mark.django_db
def test_16_refuses_a_repeated_question_flag(frozen, q100, control, operator):
    before = world(100, 110)
    with pytest.raises(CommandError, match="more than once"):
        call_command("migrate_contract_v2", "--batch", BATCH,
                     "--question", "100", "--question", "110",
                     "--to-version", "v2", "--operator", operator.username,
                     "--local", *APPLY)
    assert world(100, 110) == before


@pytest.mark.django_db
def test_16b_refuses_two_ids_on_one_flag(frozen, q100, control, operator):
    before = world(100, 110)
    with pytest.raises(CommandError):
        call_command("migrate_contract_v2", "--batch", BATCH,
                     "--question", "100", "110", "--to-version", "v2",
                     "--operator", operator.username, "--local", *APPLY)
    assert world(100, 110) == before


@pytest.mark.django_db
def test_17_a_database_failure_midway_leaves_no_partial_state(
        frozen, q100, operator, monkeypatch):
    # The contract has ALREADY been written when the audit insert fails. The
    # transaction must take the write back with it: a v2 question with no
    # audit row is exactly the state a rollback cannot explain.
    def audit_insert_fails(*args, **kwargs):
        raise DatabaseError("simulated failure after the contract write")

    monkeypatch.setattr(pre_image, "record_action", audit_insert_fails)
    before = world(100)
    with pytest.raises(DatabaseError, match="simulated failure"):
        migrate(operator, extra=APPLY)
    assert world(100) == before


@pytest.mark.django_db
def test_refuses_a_question_carrying_its_own_wrapper(operator, topic):
    question = make_question(topic, 100, wrapper={"python": "print(1)"})
    freeze(operator, question)
    assert_refused_and_untouched(operator, [100], match="own wrapper",
                                 extra=APPLY)


@pytest.mark.django_db
def test_a_write_that_touches_a_second_column_is_reverted(
        frozen, q100, operator, monkeypatch):
    # A defect that widened the write -- "while we're here, publish it" --
    # must not survive. The after-write re-read catches it and the whole
    # transaction goes.
    real_save = Question.save

    def widened_save(self, *args, **kwargs):
        if kwargs.get("update_fields") == ["execution_contract_version"]:
            self.status = Question.STATUS_PUBLISHED
            kwargs["update_fields"] = ["execution_contract_version", "status"]
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Question, "save", widened_save)
    assert_refused_and_untouched(operator, [100], match="status changed",
                                 extra=APPLY)


# ═════════════════════════════════════════════════════════════
# Phase 3 -- the row is actually locked
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db(transaction=True)
def test_the_row_is_locked_between_the_recheck_and_the_write(
        operator, topic, monkeypatch):
    """A real second connection, not an inspection of the SQL.

    While the migration holds the row -- after its locked re-read, before its
    write -- another connection asks for the same row with FOR UPDATE NOWAIT.
    With the lock held that must fail immediately. Without it, the other
    connection would get the row, and an edit made through it could land
    between the check and the write.
    """
    question = make_question(topic, 100)
    freeze(operator, question)
    observed = {}
    real = contract_migration.eligibility

    def contend_during_the_transaction(question_id, state):
        if connection.in_atomic_block:        # only the locked re-check
            def contender():
                try:
                    with transaction.atomic():
                        Question.objects.select_for_update(nowait=True).get(
                            pk=question_id)
                    observed["contender_got_the_row"] = True
                except DatabaseError:
                    observed["contender_got_the_row"] = False
                finally:
                    connections.close_all()
            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(timeout=30)
        return real(question_id, state)

    monkeypatch.setattr(contract_migration, "eligibility",
                        contend_during_the_transaction)
    migrate(operator, extra=APPLY)

    assert observed == {"contender_got_the_row": False}
    question.refresh_from_db()
    assert question.execution_contract_version == "v2"


# ═════════════════════════════════════════════════════════════
# The interface has no bypass
# ═════════════════════════════════════════════════════════════

def test_there_is_no_flag_that_relaxes_a_gate():
    parser = cmd.Command().create_parser("manage.py", "migrate_contract_v2")
    flags = {s for action in parser._actions for s in action.option_strings}

    for forbidden in ("--force", "--skip-checks", "--unsafe", "--yes",
                      "--no-verify", "--allow-stale", "--questions"):
        assert forbidden not in flags


# ═════════════════════════════════════════════════════════════
# The eligibility module, directly
# ═════════════════════════════════════════════════════════════

def _state(**overrides):
    state = {"content": "S", "status": "DRAFT", "trust_state": "UNVERIFIED",
             "execution_contract_version": "v1",
             "boilerplate_code": {"python": SAME_TREE},
             "hidden_wrapper_code": {},
             "hidden_test_cases": copy.deepcopy(SAME_TREE_CASES)}
    state.update(overrides)
    return state


def test_eligibility_accepts_the_q100_shape():
    verdict, refusals = contract_migration.eligibility(100, _state())
    assert refusals == []
    assert verdict.bucket == migration_readiness.SAFE_TO_MIGRATE


def test_a_blank_contract_is_v1_by_the_existing_rule():
    # "Blank means v1" has one definition, in execution_contract; the
    # migration delegates to it rather than restating it.
    _, refusals = contract_migration.eligibility(
        100, _state(execution_contract_version=""))
    assert refusals == []


def test_an_unknown_contract_is_refused_not_guessed():
    _, refusals = contract_migration.eligibility(
        100, _state(execution_contract_version="v9"))
    assert any("unknown execution contract" in r for r in refusals)


def test_eligibility_passes_the_real_question_id_to_the_classifier():
    # Quarantine is keyed by id. Classify under any other id and q98's hold
    # silently disappears.
    _, refusals = contract_migration.eligibility(98, _state())
    assert any("QUARANTINED" in r for r in refusals)
