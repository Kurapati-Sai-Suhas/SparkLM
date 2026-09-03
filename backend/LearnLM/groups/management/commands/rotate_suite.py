"""
Rotate a compromised hidden-test suite atomically (M2 P2.29).

Dry-run by default. Writes exactly ONE column — `hidden_test_cases` — of one
question, replacing inputs AND answers together in a single transaction.

── The window this closes ──────────────────────────────────────────────────

Rotation was two commands:

    remediate_inputs         rewrites stdin,           holds expected_output fixed
    remediate_hidden_tests   rewrites expected_output, holds stdin fixed

Each is individually correct and each guards the other's field, which is why
they are shaped that way. But a ROTATION needs both, and two commands are two
transactions. Between the first commit and the second the row holds the NEW
inputs against the OLD answers — a suite that grades nothing correctly. Any
read in that window, by a learner or by the oracle, sees a question whose
answer key does not belong to its inputs.

P2.20 rotated q3309 and q1436 through exactly that sequence and recorded the
gap plainly: both questions had zero submissions across all time and the two
commands ran back to back, so nothing was harmed — but "the window is real
and the current tooling cannot eliminate it." This is the command that
report asked for.

── Why one write, not two ─────────────────────────────────────────────────

Inputs and answers live in the SAME column. A rotation is therefore one
assignment to `hidden_test_cases`, not a sequence — the intermediate state
was never a database requirement, only an artifact of splitting the work
across two commands. Writing the finished suite once means the inconsistent
state is not merely unlikely, it is unrepresentable.

── What is deliberately NOT relaxed ───────────────────────────────────────

The two-command split exists so neither command can silently touch the
other's field. Combining them gives up that specific guard, so it is replaced
rather than dropped:

  * the same role, `learnlm_hidden_test_rw` — no new privilege, because both
    predecessors already required exactly this one (both write one column);
  * a pre-image must exist, so the ORIGINAL suite is recoverable;
  * case COUNT is held fixed — adding or removing cases is SUITE_EXPANSION,
    a different action class with its own review;
  * the category multiset is held fixed, which is the invariant P2.20
    actually preserved when it rotated by hand;
  * every field other than the suite is re-read after the write and must be
    byte-identical, or the transaction is rolled back.

Two audit rows are written, INPUT_REPAIR and HIDDEN_TEST_REPAIR, because a
rotation genuinely is both and a reader of the ledger should see both.
`EXPECTED_OUTPUT_REPAIR` is NOT recorded: that class deliberately has no
writer anywhere in this repository — expected outputs come from the oracle or
not at all — and this command does not become its first one.
"""

import json
import pathlib
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column a rotation may touch.
REPAIRABLE_FIELD = "hidden_test_cases"


class Command(BaseCommand):
    help = ("Replace a question's hidden-test suite — inputs and answers "
            "together — in one transaction. Dry-run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--cases-file", required=True, metavar="PATH",
            help="JSON file holding the complete approved replacement suite. "
                 "A file, not an argument: grading data must not pass through "
                 "shell history.")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="rotate a hidden-test suite",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_HIDDEN_TEST_ROLES,
            required_privileges=ops.HIDDEN_TEST_REPAIR_PROBE,
            forbidden_privileges=ops.HIDDEN_TEST_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "SUITE ROTATION" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        question = Question.objects.using(alias).filter(
            pk=options["question"]).first()
        if question is None:
            raise ops.GateFailure(f"no such question: {options['question']}")

        record = pre_image.require_pre_image(batch, question)

        proposed = self._read_cases(options["cases_file"])
        before_state = pre_image.question_state(question)
        current = before_state[REPAIRABLE_FIELD]

        # Every check runs BEFORE the transaction opens. A rotation that is
        # going to be refused must never have held a row lock.
        self._check_rotatable(current, proposed)

        before_digest = pre_image.live_digest(question)
        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: proposed}))

        self._render_plan(batch, question, record, before_digest, projected,
                          current, proposed)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, proposed,
                                   before_state, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Suite rotated for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "Inputs and answers moved together in one transaction; no reader "
            "could observe a half-rotated suite.")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL suite; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, proposed, before_state,
               reason):
        """
        One transaction, one assignment, both audit rows.

        The row is locked FOR UPDATE first, so a concurrent rotation of the
        same question serialises behind this one rather than interleaving.
        Any failure inside — a changed neighbour column, a refused audit
        write — rolls the whole thing back, including the suite.
        """
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            locked_state = pre_image.question_state(locked)

            # The plan was validated against an UNLOCKED read, so between
            # that read and this lock another writer may have moved the row.
            # Re-checking against the locked state is what makes the
            # validation authoritative rather than advisory: without it a
            # rotation could overwrite a suite whose shape it never examined.
            if locked_state != before_state:
                raise ops.GateFailure(
                    "the question changed between planning and locking; "
                    "re-run so the plan is validated against current state")
            self._check_rotatable(locked_state[REPAIRABLE_FIELD], proposed)

            setattr(locked, REPAIRABLE_FIELD, proposed)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            # Nothing but the suite may have moved. Checked against what
            # LANDED rather than what was proposed: the two are the same only
            # if the write did what it claimed.
            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a suite rotation; the write "
                        f"has been reverted")

            landed = after_state[REPAIRABLE_FIELD]
            if landed != proposed:
                raise ops.GateFailure(
                    "the stored suite does not match the approved file; the "
                    "write has been reverted")

            # Both, because a rotation genuinely is both. Not
            # EXPECTED_OUTPUT_REPAIR — that class has no writer by design.
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_INPUT_REPAIR,
                operator, detail=reason)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_HIDDEN_TEST_REPAIR,
                operator, detail=reason)

            after_digest = pre_image.live_digest(locked)
        return after_digest

    # ── validation ────────────────────────────────────────────────────

    def _read_cases(self, path):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such cases file: {path}")
        try:
            parsed = json.loads(location.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid UTF-8 ({exc.reason})")
        except json.JSONDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid JSON: {exc}")

        if not isinstance(parsed, list) or not parsed:
            raise ops.GateFailure(
                f"{path} must hold a non-empty JSON array of test cases")
        for index, case in enumerate(parsed, start=1):
            if not isinstance(case, dict):
                raise ops.GateFailure(
                    f"{path} case {index} is a {type(case).__name__}, not an "
                    f"object")
            if "stdin" not in case or "expected_output" not in case:
                raise ops.GateFailure(
                    f"{path} case {index} is missing stdin or expected_output")
        return parsed

    def _check_rotatable(self, current, proposed):
        """
        What a rotation may and may not change.

        It may change every stdin and every expected_output — that is the
        point, and it is why neither is held fixed here. It may not change the
        SHAPE of the suite, because that is a different action class.
        """
        if not isinstance(current, list):
            raise ops.GateFailure(
                "the stored suite is not a list; refusing to rotate a suite "
                "this command cannot read")

        if len(current) != len(proposed):
            raise ops.GateFailure(
                f"the suite has {len(current)} case(s) and the file has "
                f"{len(proposed)}. Rotation replaces a suite in place; adding "
                f"or removing cases is SUITE_EXPANSION and needs its own "
                f"review.")

        if current == proposed:
            raise ops.GateFailure(
                "the file is identical to the stored suite; refusing to record "
                "a rotation that changes nothing")

        # The invariant P2.20 preserved by hand. Categories are how the
        # quality gate reasons about coverage, so a rotation that quietly
        # dropped one would weaken the suite while appearing to replace it.
        before = Counter(
            case.get("category") for case in current if isinstance(case, dict))
        after = Counter(case.get("category") for case in proposed)
        if before != after:
            missing = sorted(
                str(k) for k in (before - after) if k is not None)
            added = sorted(str(k) for k in (after - before) if k is not None)
            raise ops.GateFailure(
                "the category multiset changed: "
                f"missing {missing or 'none'}, added {added or 'none'}. A "
                f"rotation must preserve coverage exactly.")

    # ── output ────────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, projected,
                     current, proposed):
        write = self.stdout.write

        write(f"  batch           {batch.batch_key}")
        write(f"  question        {question.pk}  {question.title.strip()[:48]}")
        write(f"  pre-image       {record.pk} (holds the ORIGINAL suite)")
        write(f"  cases           {len(current)} -> {len(proposed)}")
        write(f"  before digest   {before_digest}")
        write(f"  projected       {projected}")
        write("")

        changed_in = sum(
            1 for was, now in zip(current, proposed)
            if isinstance(was, dict) and was.get("stdin") != now.get("stdin"))
        changed_out = sum(
            1 for was, now in zip(current, proposed)
            if isinstance(was, dict)
            and was.get("expected_output") != now.get("expected_output"))

        # Counts only. Inputs and expected outputs are grading truth and are
        # never echoed, which is why rotation plans are gitignored files.
        write("Cases changing (counts only — no grading data is printed)")
        write(f"  stdin rewritten            {changed_in}/{len(current)}")
        write(f"  expected_output rewritten  {changed_out}/{len(current)}")
        write("")
        write("Fields guaranteed unchanged")
        for name in ("content", "status", "trust_state",
                     "execution_contract_version", "boilerplate_code",
                     "hidden_wrapper_code"):
            write(f"  groups_question.{name}")
        write("")
