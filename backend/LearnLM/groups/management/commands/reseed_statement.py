"""
Author a statement onto a reseed stub (M2 P2.7h-18).

    python manage.py reseed_statement --alias remediate \\
        --batch reseed-slice-1 --question 9830 \\
        --content-file statement.html --expect-digest <sha256> \\
        --reason "reseed slice 1" --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column of ONE question and records an
append-only `RemediationAction(STATEMENT_GENERATION)`.

── Why this is not `remediate_statement` ───────────────────────────────────

`STATEMENT_REPAIR` means a human adjudicated a defective statement.
`STATEMENT_GENERATION` means text was authored where a templated placeholder
stood and no statement had ever existed. A reader disputing what a question
asked needs to tell those apart, so they are different action classes written
by different commands with complementary preconditions:

    remediate_statement   placeholder ABSENT   — corrects a real statement
    reseed_statement      placeholder PRESENT  — authors where none existed

Every question is eligible for exactly one. Neither can be used as the other's
back door.

The ROLE is deliberately the same: `learnlm_remediate_rw` already holds
column-level UPDATE on `content` and nothing else, which is exactly this
command's authority. Reusing it adds no privilege to the system.
"""

import difflib
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image, reseed_authoring
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
AUTHORED_FIELD = "content"


class Command(BaseCommand):
    help = ("Author a statement onto a reseed stub. Dry-run by default. "
            "Changes the statement and nothing else.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--content-file", required=True, metavar="PATH",
            help="File holding the authored statement. A file, not an "
                 "argument: a statement is long, and shell quoting is not a "
                 "safe way to move grading content around.")
        parser.add_argument(
            "--expect-digest", required=True, metavar="SHA256",
            help="The question's current state digest. REQUIRED here — unlike "
                 "a repair, generation is driven by an orchestrator, and an "
                 "unattended write must prove it is acting on the state it "
                 "was planned against.")
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
            action="author a statement onto a reseed stub",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_STATEMENT_GENERATION_ROLES,
            required_privileges=ops.STATEMENT_GENERATION_PROBE,
            forbidden_privileges=ops.STATEMENT_GENERATION_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "STATEMENT GENERATION" + ("" if writing else "  (DRY RUN)")))
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

        blockers = reseed_authoring.statement_blockers(question, using=alias)
        if blockers:
            raise ops.GateFailure(
                f"question {question.pk} is not a virgin reseed stub:\n  - "
                + "\n  - ".join(blockers))

        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)
        if options["expect_digest"] != before_digest:
            raise ops.GateFailure(
                f"the question is at {before_digest} but this write was "
                f"planned against {options['expect_digest']}. Refusing: it "
                f"has moved since.")

        new_content = self._read_statement(options["content_file"])
        if Question.PLACEHOLDER_MARKER in new_content:
            raise ops.GateFailure(
                "the authored statement still contains the placeholder "
                "marker; that would leave the question a candidate for its "
                "own reseed forever")
        if before_state[AUTHORED_FIELD] == new_content:
            raise ops.GateFailure(
                "the file is byte-identical to the current statement; "
                "refusing to record a generation that changes nothing")

        self._render_plan(batch, question, record, before_digest,
                          before_state[AUTHORED_FIELD], new_content)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator,
                                   new_content, before_state,
                                   options["expect_digest"],
                                   options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Statement authored for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image still holds the ORIGINAL placeholder; this "
            "question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, new_content,
               before_state, expected_digest, reason):
        """
        One column, one question, one transaction. The row is locked and the
        preconditions are re-checked INSIDE the lock: everything above was
        read without one, and a stub can stop being a stub between the two.
        """
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))

            blockers = reseed_authoring.statement_blockers(locked, using=alias)
            if blockers:
                raise ops.GateFailure(
                    f"question {locked.pk} stopped being a virgin stub "
                    f"between the plan and the write:\n  - "
                    + "\n  - ".join(blockers))
            if pre_image.live_digest(locked) != expected_digest:
                raise ops.GateFailure(
                    "the question changed between the plan and the lock; the "
                    "write has been abandoned")

            setattr(locked, AUTHORED_FIELD, new_content)
            locked.save(using=alias, update_fields=[AUTHORED_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)
            for name, value in before_state.items():
                if name == AUTHORED_FIELD:
                    continue
                if after_state[name] != value:
                    # Cannot happen with update_fields — which is why it is
                    # checked. Raising inside the block reverts the write.
                    raise ops.GateFailure(
                        f"{name} changed during statement generation; the "
                        f"write has been reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_STATEMENT_GENERATION,
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
                f"{path} is not valid UTF-8 ({exc.reason} at byte {exc.start})")
        if not content.strip():
            raise ops.GateFailure(f"{path} is empty or only whitespace")
        return content

    def _render_plan(self, batch, question, record, before_digest, before,
                     after):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  field           {AUTHORED_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  size            {len(before)} -> {len(after)} bytes")
        write("")
        write("  diff:")
        diff = list(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            "placeholder", "authored", lineterm="", n=1))
        for line in diff[:40]:
            style = (self.style.ERROR if line.startswith("-")
                     else self.style.SUCCESS if line.startswith("+")
                     else lambda text: text)
            write("    " + style(line))
        if len(diff) > 40:
            write(f"    ... {len(diff) - 40} more diff lines")
        write("")
