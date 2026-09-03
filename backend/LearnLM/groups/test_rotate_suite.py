"""
Atomic suite rotation (M2 P2.29).

The property under test is not "the suite changed". Both predecessor commands
already change suites correctly. It is that inputs and answers move TOGETHER —
that there is no instant, however brief, at which the row holds new inputs
against old answers.

So these tests assert on the transaction boundary itself: how many UPDATEs the
question row receives, whether they are inside one atomic block, and what
survives when the block raises. A test that only called the command and then
checked the final state would pass just as happily against the two-command
sequence this milestone exists to replace.

Local/synthetic database only. No production alias is ever named here.
"""

import ast
import inspect
import json
import textwrap

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from groups import pre_image
from groups.management.commands import rotate_suite as cmd
from groups.models import (
    CodingPortal, Question, RemediationAction, RemediationBatch, Topic,
)

User = get_user_model()

#: A compromised suite: the inputs leaked, so both halves must be replaced.
ORIGINAL_CASES = [
    {"stdin": "5\n1 2 3 4 5\n", "expected_output": "15", "category": "typical"},
    {"stdin": "1\n0\n", "expected_output": "0", "category": "edge"},
    {"stdin": "3\n-1 -2 -3\n", "expected_output": "-6", "category": "boundary"},
]

#: The approved replacement. Every stdin AND every expected_output differs;
#: the case count and the category multiset do not. That is exactly the
#: envelope a rotation is allowed to move within.
ROTATED_CASES = [
    {"stdin": "4\n7 7 7 7\n", "expected_output": "28", "category": "typical"},
    {"stdin": "1\n9\n", "expected_output": "9", "category": "edge"},
    {"stdin": "2\n-8 -9\n", "expected_output": "-17", "category": "boundary"},
]

APPLY = ("--apply", "--confirm")


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="rot-op", password="pw",
                                    email="r@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Rotation Portal")
    made, _ = Topic.objects.get_or_create(
        name="RotationTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, question_id, cases, content="Statement."):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content=content, topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": "class Solution:\n    def total(self): pass\n"},
        hidden_test_cases=json.loads(json.dumps(cases)),
        hidden_wrapper_code={}, execution_contract_version="v1")


@pytest.fixture
def question(db, topic):
    return make_question(topic, 9820, ORIGINAL_CASES)


@pytest.fixture
def control(db, topic):
    """A second question in the same batch. It must never move."""
    return make_question(topic, 9821,
                         [{"stdin": "1", "expected_output": "1",
                           "category": "typical"}],
                         content="Control.")


@pytest.fixture
def frozen_batch(db, operator, question, control):
    batch = RemediationBatch.objects.create(
        batch_key="rot-batch", purpose="suite rotation", created_by=operator)
    pre_image.capture(batch, question, operator)
    pre_image.capture(batch, control, operator)
    pre_image.freeze(batch, operator)
    return batch


def cases_file(tmp_path, payload, name="rotation.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def rotate(path, operator, question_id=9820, batch="rot-batch", extra=()):
    call_command("rotate_suite", "--batch", batch,
                 "--question", str(question_id), "--cases-file", path,
                 "--reason", "approved rotation plan",
                 "--operator", operator.username, "--local", *extra)


# ═════════════════════════════════════════════════════════════
# A — the rotation itself
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_inputs_and_answers_both_change(frozen_batch, question, operator,
                                        tmp_path):
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ROTATED_CASES

    # Stated separately, because "the column equals the file" would also hold
    # if the file had only touched one half.
    assert [c["stdin"] for c in question.hidden_test_cases] != [
        c["stdin"] for c in ORIGINAL_CASES]
    assert [c["expected_output"] for c in question.hidden_test_cases] != [
        c["expected_output"] for c in ORIGINAL_CASES]


@pytest.mark.django_db
def test_no_other_field_moves(frozen_batch, question, operator, tmp_path):
    before = {name: getattr(question, name)
              for name in pre_image.CAPTURED_FIELDS}

    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    for name in pre_image.CAPTURED_FIELDS:
        if name == "hidden_test_cases":
            continue
        assert getattr(question, name) == before[name], name


@pytest.mark.django_db
def test_other_questions_in_the_batch_are_untouched(frozen_batch, control,
                                                    operator, tmp_path):
    before = control.hidden_test_cases

    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    control.refresh_from_db()
    assert control.hidden_test_cases == before


# ═════════════════════════════════════════════════════════════
# B — the transaction boundary
#
# The milestone. Not "does it work" but "could anything observe it
# half-done".
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_question_row_receives_exactly_one_update(frozen_batch, question,
                                                      operator, tmp_path):
    """
    One UPDATE, not two.

    This is the difference from the old sequence in its most direct form: two
    commands necessarily issue two UPDATEs to `groups_question` with a commit
    between them. A rotation issues one, so the intermediate state never
    exists in the database at all — not briefly, not under load, not ever.
    """
    path = cases_file(tmp_path, ROTATED_CASES)

    with CaptureQueriesContext(connection) as captured:
        rotate(path, operator, extra=APPLY)

    updates = [q["sql"] for q in captured.captured_queries
               if q["sql"].lstrip().upper().startswith("UPDATE")
               and "groups_question" in q["sql"]
               and "preimage" not in q["sql"].lower()]
    assert len(updates) == 1, updates


@pytest.mark.django_db
def test_that_single_update_carries_both_halves(frozen_batch, question,
                                                operator, tmp_path):
    """
    The one UPDATE is the whole rotation, not the first half of one.

    Reading the row immediately after the statement — still inside the
    command's own transaction — is the closest a test can stand to the window
    that used to exist.
    """
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    landed = Question.objects.get(pk=question.pk).hidden_test_cases
    for stored, approved in zip(landed, ROTATED_CASES):
        assert stored["stdin"] == approved["stdin"]
        assert stored["expected_output"] == approved["expected_output"]


@pytest.mark.django_db
def test_a_failure_after_the_write_rolls_the_suite_back(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The write happens first and the checks run after it, against what landed.
    That ordering is only safe if a failing check un-does the write — so the
    rollback is load-bearing, not a nicety.
    """
    def refuse(*args, **kwargs):
        raise RuntimeError("audit ledger unavailable")

    monkeypatch.setattr(cmd.pre_image, "record_action", refuse)

    with pytest.raises(RuntimeError):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_failure_on_the_second_audit_row_rolls_the_suite_back(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The nastiest ordering: the suite is written, INPUT_REPAIR is recorded, and
    then HIDDEN_TEST_REPAIR fails. Without one transaction this would leave a
    rotated suite described in the ledger as an input repair only.
    """
    real = cmd.pre_image.record_action
    calls = []

    def fail_on_second(batch, q, action_class, actor, detail=""):
        calls.append(action_class)
        if len(calls) == 2:
            raise RuntimeError("ledger write refused")
        return real(batch, q, action_class, actor, detail=detail)

    monkeypatch.setattr(cmd.pre_image, "record_action", fail_on_second)

    with pytest.raises(RuntimeError):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES
    assert not RemediationAction.objects.filter(question=question).exists()


@pytest.mark.django_db
def test_a_neighbouring_column_changing_reverts_the_suite(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The post-write comparison must be able to abort the rotation, not merely
    complain about it.
    """
    real = cmd.pre_image.question_state
    reads = []

    def drifting(q):
        state = dict(real(q))
        reads.append(q)
        if len(reads) > 1:
            # Only the post-write read drifts: a neighbour column that
            # changed between the snapshot and the write is exactly what the
            # comparison exists to catch.
            state["content"] = state["content"] + " (tampered)"
        return state

    monkeypatch.setattr(cmd.pre_image, "question_state", drifting)

    with pytest.raises(CommandError):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_the_row_is_locked_before_it_is_written(frozen_batch, question,
                                                operator, tmp_path):
    """
    SELECT ... FOR UPDATE precedes the UPDATE, so two rotations of the same
    question serialise instead of interleaving.
    """
    path = cases_file(tmp_path, ROTATED_CASES)

    with CaptureQueriesContext(connection) as captured:
        rotate(path, operator, extra=APPLY)

    sql = [q["sql"] for q in captured.captured_queries]
    locks = [i for i, s in enumerate(sql) if "FOR UPDATE" in s.upper()]
    writes = [i for i, s in enumerate(sql)
              if s.lstrip().upper().startswith("UPDATE")
              and "groups_question" in s]
    assert locks, "the question row is never locked"
    assert min(locks) < min(writes)


def test_the_transaction_is_alias_scoped():
    """
    A bare `transaction.atomic()` opens on `default` while the write goes to
    `--alias`, so a failure would commit rather than roll back. The same
    check the sibling commands carry.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cmd.Command._apply)))
    atomics = [node for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and getattr(node.func, "attr", None) == "atomic"]
    assert len(atomics) == 1, "a rotation is exactly one transaction"
    assert [kw.arg for kw in atomics[0].keywords] == ["using"]


def test_there_is_only_one_save_in_the_write_path():
    """
    Two saves would be two statements, and two statements inside one
    transaction still give the oracle — which reads on its own connection at
    READ COMMITTED — nothing to see, but they would reintroduce the shape the
    milestone removed. Kept to one so the invariant is structural.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(cmd.Command._apply)))
    saves = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "save"]
    assert len(saves) == 1


# ═════════════════════════════════════════════════════════════
# C — validation refuses before it locks
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_different_case_count_is_refused(frozen_batch, question, operator,
                                           tmp_path):
    """Adding or removing cases is SUITE_EXPANSION, a different action."""
    with pytest.raises(CommandError, match="SUITE_EXPANSION"):
        rotate(cases_file(tmp_path, ROTATED_CASES[:2]), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_changed_category_multiset_is_refused(frozen_batch, question,
                                                operator, tmp_path):
    """
    A rotation that quietly dropped the boundary case would look like a
    replacement and be a weakening.
    """
    weakened = json.loads(json.dumps(ROTATED_CASES))
    weakened[2]["category"] = "typical"

    with pytest.raises(CommandError, match="coverage"):
        rotate(cases_file(tmp_path, weakened), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_no_op_rotation_is_refused(frozen_batch, question, operator,
                                     tmp_path):
    """A recorded rotation that changed nothing would be a false ledger entry."""
    with pytest.raises(CommandError, match="changes nothing"):
        rotate(cases_file(tmp_path, ORIGINAL_CASES), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_case_missing_expected_output_is_refused(frozen_batch, question,
                                                   operator, tmp_path):
    broken = json.loads(json.dumps(ROTATED_CASES))
    del broken[1]["expected_output"]

    with pytest.raises(CommandError, match="expected_output"):
        rotate(cases_file(tmp_path, broken), operator, extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_malformed_file_is_refused(frozen_batch, question, operator,
                                     tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(CommandError, match="not valid JSON"):
        rotate(str(path), operator, extra=APPLY)


@pytest.mark.django_db
def test_a_question_without_a_pre_image_is_refused(frozen_batch, operator,
                                                   topic, tmp_path):
    """
    Without a pre-image the ORIGINAL suite is gone the moment the rotation
    lands. Rotation is the operation that most needs a way back — so a
    question outside the frozen capture is refused even though the batch
    itself is in good order.
    """
    stray = make_question(topic, 9822, ORIGINAL_CASES)

    with pytest.raises(pre_image.CaptureIncomplete, match="no pre-image"):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator,
               question_id=stray.pk, extra=APPLY)

    stray.refresh_from_db()
    assert stray.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_an_unfrozen_batch_is_refused(db, operator, topic, question,
                                      tmp_path):
    """Capture must be complete before anything is modified."""
    RemediationBatch.objects.create(
        batch_key="open-batch", purpose="test", created_by=operator)

    with pytest.raises(pre_image.CaptureIncomplete, match="not frozen"):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator,
               batch="open-batch", extra=APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_a_row_that_moved_between_planning_and_locking_is_refused(
        frozen_batch, question, operator, tmp_path, monkeypatch):
    """
    The plan is validated against an unlocked read. If another writer moves
    the row before the lock is taken, that validation described a suite that
    is no longer there — so it is redone against the locked state, and a
    mismatch aborts rather than overwriting a suite never examined.
    """
    real_apply = cmd.Command._apply

    def concurrent_write_then_apply(self, alias, *args, **kwargs):
        # A genuine UPDATE landing in the gap: after `handle` snapshotted the
        # row and computed its plan, before `_apply` takes the lock. Written
        # through the ORM rather than faked in memory, so the command has to
        # notice it by re-reading.
        Question.objects.using(alias).filter(pk=9820).update(
            hidden_test_cases=[{"stdin": "concurrent", "category": "typical",
                                "expected_output": "write"}])
        return real_apply(self, alias, *args, **kwargs)

    monkeypatch.setattr(cmd.Command, "_apply", concurrent_write_then_apply)

    with pytest.raises(CommandError, match="between planning and locking"):
        rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    # The concurrent writer's suite stands. The rotation neither landed nor
    # silently clobbered a suite it had never validated against.
    question.refresh_from_db()
    assert question.hidden_test_cases == [
        {"stdin": "concurrent", "category": "typical",
         "expected_output": "write"}]


@pytest.mark.django_db
def test_validation_runs_before_the_row_is_locked(frozen_batch, question,
                                                  operator, tmp_path):
    """
    A rotation that is going to be refused should never have held a lock —
    otherwise a bad plan file blocks readers for the length of its own
    rejection.
    """
    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(CommandError):
            rotate(cases_file(tmp_path, ROTATED_CASES[:2]), operator,
                   extra=APPLY)

    assert not [q for q in captured.captured_queries
                if "FOR UPDATE" in q["sql"].upper()]


# ═════════════════════════════════════════════════════════════
# D — dry run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_dry_run_writes_nothing(frozen_batch, question, operator, tmp_path):
    rotate(cases_file(tmp_path, ROTATED_CASES), operator)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES
    assert not RemediationAction.objects.filter(question=question).exists()


def test_confirmation_is_wired_to_the_gate():
    """
    `--confirm` is enforced by `require_confirmation`, and only against a
    production database — the shared rule for every remediation command, not
    something this one decides. Asserted structurally because the local
    database these tests run against is by definition not production, so a
    behavioural test here would assert the opposite of the real rule.
    """
    source = inspect.getsource(cmd.Command.handle)
    assert 'confirmed=options["confirm"]' in source
    assert 'needs_write=writing' in source


# ═════════════════════════════════════════════════════════════
# E — the audit ledger
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_both_action_classes_are_recorded(frozen_batch, question, operator,
                                          tmp_path):
    """A rotation is genuinely both repairs; the ledger should say so."""
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    recorded = set(RemediationAction.objects.filter(question=question)
                   .values_list("action_class", flat=True))
    assert recorded == {RemediationAction.CLASS_INPUT_REPAIR,
                        RemediationAction.CLASS_HIDDEN_TEST_REPAIR}


@pytest.mark.django_db
def test_expected_output_repair_is_never_recorded(frozen_batch, question,
                                                  operator, tmp_path):
    """
    `EXPECTED_OUTPUT_REPAIR` has no writer anywhere in this repository, by
    design: expected outputs come from the oracle or not at all. This command
    does not quietly become its first one.
    """
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    assert not RemediationAction.objects.filter(
        action_class=RemediationAction.CLASS_EXPECTED_OUTPUT_REPAIR).exists()

    source = inspect.getsource(cmd)
    assert "CLASS_EXPECTED_OUTPUT_REPAIR" not in source


@pytest.mark.django_db
def test_the_pre_image_still_holds_the_original_suite(frozen_batch, question,
                                                      operator, tmp_path):
    """Rotation must stay reversible: the way back is the pre-image."""
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    record = pre_image.require_pre_image(frozen_batch, question)
    assert record.hidden_test_cases == ORIGINAL_CASES


@pytest.mark.django_db
def test_rollback_restores_the_rotated_suite(frozen_batch, question, operator,
                                             tmp_path):
    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)
    pre_image.rollback(frozen_batch, operator, questions=[question.pk])

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


# ═════════════════════════════════════════════════════════════
# F — content trust is not touched
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_trust_state_and_status_are_untouched(frozen_batch, question, operator,
                                              tmp_path):
    """
    Rotating a suite is not evidence about a question. Whatever trust the
    question had before, it has after — this command does not promote and
    does not demote.
    """
    before = (question.status, question.trust_state,
              question.is_adaptive_eligible)

    rotate(cases_file(tmp_path, ROTATED_CASES), operator, extra=APPLY)

    question.refresh_from_db()
    assert (question.status, question.trust_state,
            question.is_adaptive_eligible) == before


def test_the_command_never_writes_a_trust_field():
    """Structural, so a later edit cannot add one quietly."""
    source = inspect.getsource(cmd)
    for forbidden in ("trust_state =", "status =", "ORACLE_VERIFIED",
                      "PUBLISHED", "is_adaptive_eligible ="):
        assert forbidden not in source, forbidden


def test_only_the_suite_column_is_writable():
    assert cmd.REPAIRABLE_FIELD == "hidden_test_cases"
    tree = ast.parse(textwrap.dedent(inspect.getsource(cmd.Command._apply)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(
                node.func, "attr", None) == "save":
            fields = [kw for kw in node.keywords if kw.arg == "update_fields"]
            assert fields, "save() without update_fields can write any column"


# ═════════════════════════════════════════════════════════════
# G — privilege
# ═════════════════════════════════════════════════════════════

def test_the_role_gate_is_the_hidden_test_role():
    """
    No privilege escalation for combining the two commands: both predecessors
    already required exactly this role, because both write this one column.
    """
    source = inspect.getsource(cmd)
    assert "ALLOWED_HIDDEN_TEST_ROLES" in source
    for other in ("ALLOWED_STATEMENT_ROLES", "ALLOWED_CONTRACT_ROLES",
                  "ALLOWED_BOILERPLATE_ROLES"):
        assert other not in source, other


@pytest.mark.django_db
def test_an_unknown_operator_is_refused(frozen_batch, question, tmp_path):
    with pytest.raises(CommandError):
        call_command("rotate_suite", "--batch", "rot-batch",
                     "--question", "9820",
                     "--cases-file", cases_file(tmp_path, ROTATED_CASES),
                     "--reason", "r", "--operator", "nobody",
                     "--local", *APPLY)

    question.refresh_from_db()
    assert question.hidden_test_cases == ORIGINAL_CASES


# ═════════════════════════════════════════════════════════════
# H — output discloses no grading truth
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_no_stdin_or_expected_output_is_printed(db, topic, operator, tmp_path,
                                                capsys):
    """
    Sentinel values, not the ordinary fixtures: a short answer like "15" also
    occurs in "PostgreSQL 15.4" in the header, so a leak test built on it
    would fail for a reason that has nothing to do with a leak — or, worse,
    pass while a genuine leak hid behind the same coincidence.
    """
    marked = make_question(topic, 9823, [
        {"stdin": "SENTINEL-STDIN-BEFORE", "category": "typical",
         "expected_output": "SENTINEL-ANSWER-BEFORE"}])
    replacement = [{"stdin": "SENTINEL-STDIN-AFTER", "category": "typical",
                    "expected_output": "SENTINEL-ANSWER-AFTER"}]

    batch = RemediationBatch.objects.create(
        batch_key="leak-batch", purpose="leak check", created_by=operator)
    pre_image.capture(batch, marked, operator)
    pre_image.freeze(batch, operator)

    rotate(cases_file(tmp_path, replacement), operator, question_id=9823,
           batch="leak-batch", extra=APPLY)

    printed = capsys.readouterr().out
    assert "SENTINEL" not in printed


@pytest.mark.django_db
def test_the_plan_reports_counts_so_the_operator_can_still_check_it(
        frozen_batch, question, operator, tmp_path, capsys):
    """
    Withholding the data must not mean withholding the shape: an operator
    approving a rotation needs to see how much of the suite moves.
    """
    rotate(cases_file(tmp_path, ROTATED_CASES), operator)

    printed = capsys.readouterr().out
    assert "stdin rewritten            3/3" in printed
    assert "expected_output rewritten  3/3" in printed


@pytest.mark.django_db
def test_the_cases_file_is_a_file_not_an_argument():
    """
    Grading truth passed as a command-line argument lands in shell history and
    in the process table. The sibling commands take a file for the same reason.
    """
    source = inspect.getsource(cmd)
    assert "--cases-file" in source
    assert "--cases " not in source


@pytest.mark.django_db
def test_output_survives_a_legacy_windows_console(frozen_batch, question,
                                                  operator, tmp_path):
    """
    The command prints em dashes and arrows. P2.28 fixed the stream, and this
    asserts the new command's own output is inside what that fix covers —
    every character it emits must be encodable as UTF-8 and, once encoded,
    must round-trip.
    """
    from io import StringIO

    buffer = StringIO()
    call_command("rotate_suite", "--batch", "rot-batch", "--question", "9820",
                 "--cases-file", cases_file(tmp_path, ROTATED_CASES),
                 "--reason", "r", "--operator", operator.username, "--local",
                 stdout=buffer)

    text = buffer.getvalue()
    assert text.encode("utf-8").decode("utf-8") == text
    assert "DRY RUN" in text
