"""
Apply an approved HIDDEN_TEST_REPAIR to one question (M2 P2.7).

    python manage.py remediate_hidden_tests --alias hiddentest \\
        --batch p27-pilot-1 --question 17 \\
        --cases-file remediation/q17_approved_cases.json \\
        --reason "adjudication record: serialise two list values" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `hidden_test_cases` — of one
question, and records an append-only `RemediationAction`.

── Separate from statement repair, deliberately ────────────────────────────

The remediation design fixed an order: statement first, keys second. A single
command able to do both would make that order a convention. Two commands with
two column-scoped roles make it a privilege boundary: the statement role cannot
touch a key, and this role cannot touch a statement, so neither can happen out
of order even by mistake.

── The stdin invariant ─────────────────────────────────────────────────────

This command REFUSES to change any `stdin`, add a case, or remove one.

A "hidden test repair" that altered an input would be changing the question
being asked, not the answer being recorded — and it would do so under a name
that sounds mechanical. The approved repairs in this batch (serialising a list,
correcting boolean casing) touch stored FORM only, so the invariant costs
nothing and forecloses the dangerous version.

Adding or removing cases is a different action class and needs its own review;
this command says so rather than quietly permitting it.
"""

import json
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image, provenance
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "hidden_test_cases"


class Command(BaseCommand):
    help = ("Apply an approved hidden-test repair to one question. Dry-run by "
            "default. Changes the stored suite and nothing else, and cannot "
            "change any stdin.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--cases-file", required=True, metavar="PATH",
            help="JSON file holding the approved replacement suite. A file, "
                 "not an argument: grading data must not pass through shell "
                 "history.")
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
            action="repair a hidden-test suite",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_HIDDEN_TEST_ROLES,
            required_privileges=ops.HIDDEN_TEST_REPAIR_PROBE,
            forbidden_privileges=ops.HIDDEN_TEST_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "HIDDEN-TEST REPAIR" + ("" if writing else "  (DRY RUN)")))
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

        self._check_inputs_unchanged(current, proposed)

        if current == proposed:
            raise ops.GateFailure(
                "the file is identical to the stored suite; refusing to record "
                "a repair that changes nothing")

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
            f"Hidden tests repaired for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL suite; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, proposed, before_state,
               reason):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            setattr(locked, REPAIRABLE_FIELD, proposed)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a hidden-test repair; the "
                        f"write has been reverted")

            # Re-check the invariant against what actually landed, not against
            # what was proposed — the two are the same only if the write did
            # what it claimed.
            self._check_inputs_unchanged(before_state[REPAIRABLE_FIELD],
                                         after_state[REPAIRABLE_FIELD])

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_HIDDEN_TEST_REPAIR,
                operator, detail=reason)
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

    def _check_inputs_unchanged(self, current, proposed):
        """
        Same cases, same inputs, same order. See the module docstring.

        Compared by `provenance.case_identity` where possible so the check uses
        the repository's one definition of "the same input" rather than a
        second one invented here.
        """
        if len(current) != len(proposed):
            raise ops.GateFailure(
                f"the suite has {len(current)} case(s) and the file has "
                f"{len(proposed)}. This command repairs stored FORM; adding or "
                f"removing cases is a different action class and needs its own "
                f"review.")

        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            old_stdin = was.get("stdin") if isinstance(was, dict) else None
            new_stdin = now.get("stdin")
            if old_stdin == new_stdin:
                continue
            if (isinstance(old_stdin, str) and isinstance(new_stdin, str)
                    and provenance.case_identity(old_stdin)
                    == provenance.case_identity(new_stdin)):
                continue
            raise ops.GateFailure(
                f"case {index} changes stdin from {old_stdin!r} to "
                f"{new_stdin!r}. Changing an input changes the question being "
                f"asked, not the answer being recorded; refusing.")

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, projected,
                     current, proposed):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write(f"  field           {REPAIRABLE_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  cases           {len(current)} (unchanged count; stdin values "
              f"are held fixed)")
        write("")

        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            old_expected = was.get("expected_output") if isinstance(was, dict) else was
            new_expected = now.get("expected_output")
            if old_expected == new_expected:
                write(f"    case {index}: unchanged   "
                      f"expected={old_expected!r} ({type(old_expected).__name__})")
                continue
            write(self.style.WARNING(f"    case {index}: CHANGED"))
            write(f"      stdin     {now.get('stdin')!r}  (held fixed)")
            write(self.style.ERROR(
                f"      before    {old_expected!r}  "
                f"({type(old_expected).__name__})"))
            write(self.style.SUCCESS(
                f"      after     {new_expected!r}  "
                f"({type(new_expected).__name__})"))
        write("")
