"""
Apply an approved STATEMENT_REPAIR to one question (M2 P2.7).

    python manage.py remediate_statement --alias remediate \\
        --batch p27-pilot-1 --question 963 --content-file statement.html \\
        --reason "adjudication record §B: any-orientation reading" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column of ONE question, and records an
append-only `RemediationAction` describing what changed.

── Why the statement, and only the statement ───────────────────────────────

The remediation design fixed an order that is not negotiable: statement repair
precedes key repair, because a key derived from a contradictory statement is a
confidently wrong answer with fresh provenance. This command is the first half
of that order and is deliberately incapable of the second — it cannot touch
`hidden_test_cases`, `expected_output`, the contract, the boilerplate, the
wrapper, `status` or `trust_state`.

Three independent things enforce that:

  1. the save names `update_fields=["content"]`;
  2. every other captured field is compared before and after, and a difference
     aborts the transaction;
  3. the remediation role is expected to hold COLUMN-level UPDATE on `content`
     alone, so the database refuses anything wider even if this code asked.

Belt, braces, and a second pair of braces held by someone else — because this
is the first command in the system that can change grading truth.

── Write-ahead ─────────────────────────────────────────────────────────────

`pre_image.require_pre_image` raises unless the batch is frozen AND this
question has a pre-image AND that pre-image still verifies. No pre-image, no
modification.
"""

import difflib
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "content"


class Command(BaseCommand):
    help = ("Apply an approved statement repair to one question. Dry-run by "
            "default. Changes the statement and nothing else.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--content-file", required=True, metavar="PATH",
            help="File holding the approved replacement statement. A file, not "
                 "an argument: a statement is long, and shell quoting is not a "
                 "safe way to move grading content around.")
        parser.add_argument(
            "--reason", required=True,
            help="Why, referencing the adjudication record. Stored on the "
                 "action; a change nobody can explain later is not auditable.")
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
            action="repair a question statement",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_REMEDIATION_ROLES,
            required_privileges=ops.STATEMENT_REPAIR_PROBE,
            forbidden_privileges=ops.STATEMENT_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "STATEMENT REPAIR" + ("" if writing else "  (DRY RUN)")))
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

        # WRITE-AHEAD. Raises unless frozen, captured and verifying.
        record = pre_image.require_pre_image(batch, question)

        # A REPAIR corrects a statement that exists. A question still carrying
        # the templated placeholder has never had one, so authoring it here
        # would file generation under `STATEMENT_REPAIR` and lose the
        # distinction the audit trail exists to keep. `reseed_statement` is
        # the command for that, and its precondition is the exact complement
        # of this one — every question is eligible for exactly one of them.
        if Question.PLACEHOLDER_MARKER in (question.content or ""):
            raise ops.GateFailure(
                f"question {question.pk} still carries the placeholder "
                f"marker, so it has no statement to repair. Authoring one is "
                f"STATEMENT_GENERATION: use `reseed_statement`, which records "
                f"the correct action class and checks the stub preconditions "
                f"this command does not.")

        new_content = self._read_statement(options["content_file"])
        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)

        if before_state[REPAIRABLE_FIELD] == new_content:
            raise ops.GateFailure(
                "the file is byte-identical to the current statement; refusing "
                "to record a repair that changes nothing")

        self._render_plan(batch, question, record, before_digest,
                          before_state[REPAIRABLE_FIELD], new_content)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator,
                                   new_content, before_state,
                                   options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Statement repaired for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL statement; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, new_content,
               before_state, reason):
        """
        One column, one question, one transaction, with the untouched fields
        re-checked inside it so a surprise reverts rather than persists.
        """
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            setattr(locked, REPAIRABLE_FIELD, new_content)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    # Cannot happen with update_fields — which is why it is
                    # checked. Raising inside the block reverts the write.
                    raise ops.GateFailure(
                        f"{name} changed during a statement repair; the write "
                        f"has been reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_STATEMENT_REPAIR,
                operator, detail=reason)
        return after_digest

    # ── input and reporting ───────────────────────────────────────────

    def _read_statement(self, path):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such content file: {path}")
        try:
            content = location.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(
                f"{path} is not valid UTF-8 ({exc.reason} at byte {exc.start}); "
                f"refusing to store a statement that would have to be altered "
                f"to be read")
        if not content.strip():
            raise ops.GateFailure(f"{path} is empty or only whitespace")
        return content

    def _render_plan(self, batch, question, record, before_digest, before, after):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  field           {REPAIRABLE_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  size            {len(before)} -> {len(after)} bytes")
        write("")
        write("  diff:")
        diff = list(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            "current", "proposed", lineterm="", n=1))
        for line in diff[:40]:
            style = (self.style.ERROR if line.startswith("-")
                     else self.style.SUCCESS if line.startswith("+")
                     else lambda text: text)
            write("    " + style(line))
        if len(diff) > 40:
            write(f"    ... {len(diff) - 40} more diff lines")
        write("")
