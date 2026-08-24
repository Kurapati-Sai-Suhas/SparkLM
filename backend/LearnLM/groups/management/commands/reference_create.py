"""
Author a reference solution (M2 P2.7 pilot).

The missing first link. `reference_review` moves a reference through its
lifecycle but cannot create one, and `ReferenceSolution` is deliberately absent
from Django admin (P2.7d-2 secrecy), so before this command the only code that
ever created a reference lived in the test suite.

    python manage.py reference_create --question 1779 --language python \\
        --source-file solution.py --operator alice --confirm

── The source arrives by file, never on the command line ───────────────────

There is no `--source`. A reference solution IS the answer key, and a value
passed as an argument is written to shell history and visible in the process
table to every user on the machine. `--source-file` keeps it out of both, and
the command never echoes the contents back.

── What this command deliberately does NOT do ──────────────────────────────

It creates a DRAFT and stops. It does not submit for review, approve, activate,
or compute `source_hash`.

`source_hash` is APPROVAL PROVENANCE, not a property of the text: it records
what a named human reviewed at a named moment. Computing it at creation would
manufacture that claim, and the `reference_approval_provenance` CHECK enforces
the same thing at the database level — the hash must be NULL while the row is
not APPROVED.

Nothing here touches `Question`, its hidden tests, its expected outputs, or its
trust state. A structural test asserts that rather than trusting this
paragraph.

── The connection is named, never assumed (M2 P2.7) ────────────────────────

`--alias` selects the database connection, and every read and write is routed
through it. Before this the command used the default manager throughout, which
on this deployment is the READ-ONLY census role — so the write could only ever
have failed, and the least-privilege role that should perform it had no way to
be selected.

There is no fallback: naming an alias that is not configured raises rather than
quietly using `default`. That is the same defect class as the alias threading
already fixed inside `pre_image.py`, where a batch was created on one connection
and its pre-images on another.
"""

import hashlib
import pathlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common import languages
from groups import execution_contract
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.models import Question, ReferenceSolution


class Command(BaseCommand):
    help = ("Create a DRAFT reference solution for one question. Does not "
            "submit, approve, activate, or hash it.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument("--language", required=True, metavar="LANG")
        parser.add_argument(
            "--source-file", required=True, metavar="PATH",
            help="File containing the reference solution. There is no inline "
                 "--source: the answer key must not enter shell history.")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required. Creates a persistent row attributed to you.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection. `oracle` on production; every read and "
                 "write is routed through it, with no fallback.")

    def handle(self, *args, **options):
        alias = options["alias"]

        # ── Every check runs before any write. A partially-validated insert
        # would leave a row that later commands must special-case.
        operator = trust.resolve_operator(options["operator"])
        # Which database this IS, rather than which one was demanded: the
        # production contract applies on production, and this command is also
        # run against throwaway databases by its own tests.
        identity = ops.describe_target(alias)
        if identity["is_production"]:
            ops.gate_writing_role(alias, allowed=ops.ALLOWED_ORACLE_ROLES)
            ops.gate_write_privilege(alias, required=ops.REFERENCE_WRITE_PROBE,
                                     forbidden=ops.ORACLE_FORBIDDEN)
        else:
            ops.gate_write_privilege(alias, required=ops.REFERENCE_WRITE_PROBE)

        if not options["confirm"]:
            raise CommandError(
                "refusing to create without --confirm: this stores a reference "
                "solution, which becomes this question's candidate answer key")

        question = self._resolve_question(options["question"], alias)
        language = self._resolve_language(options["language"])
        contract = self._resolve_contract(question)
        source = self._read_source(options["source_file"])
        self._refuse_duplicate(question, language, alias)
        self._warn_other_active(question, language, alias)

        # Lifecycle columns are omitted, not set: the model defaults give
        # review_state=DRAFT, is_active=False, and NULL provenance. Passing
        # them explicitly would be this command asserting a lifecycle state it
        # has no standing to assert.
        with transaction.atomic(using=alias):
            reference = ReferenceSolution.objects.using(alias).create(
                question_id=question.pk, language=language, source_code=source)

        ops.render_identity(self, identity, operator)
        self.stdout.write("")
        self._render(reference, question, language, contract, source, operator)

    # ── validation ────────────────────────────────────────────────────

    def _resolve_question(self, question_id, alias):
        """
        The question, read through the SELECTED connection.

        `_question_trust.resolve_question` uses the default manager; reading the
        question from one connection and writing its reference to another is
        how a row ends up attached to something that is not there.
        """
        question = Question.objects.using(alias).filter(pk=question_id).first()
        if question is None:
            raise CommandError(f"no such question: {question_id}")
        return question

    def _resolve_language(self, requested):
        """
        The canonical spelling, or a CommandError.

        Checked against `common.languages`, which is the same registry the
        executor uses to pick a Judge0 language id. A reference in a language
        the runner cannot map is unexecutable, so it is refused at creation
        rather than discovered at oracle time.
        """
        normalised = (requested or "").strip().lower()
        if normalised not in languages.LANGUAGE_IDS:
            raise CommandError(
                f"unknown language {requested!r}; the executor maps "
                f"{sorted(languages.LANGUAGE_IDS)}")
        return normalised

    def _resolve_contract(self, question):
        """
        The question's execution contract, or a CommandError.

        A reference is written against a specific harness. If the question
        declares a contract this codebase cannot execute, the reference cannot
        be validated later and should not be stored now.
        """
        try:
            return execution_contract.contract_version(question)
        except execution_contract.UnknownExecutionContract as exc:
            raise CommandError(str(exc))

    def _read_source(self, path):
        """
        The reference source, verbatim.

        Decoded strictly: a file that is not valid UTF-8 is refused rather than
        repaired. Replacing an undecodable byte would silently alter the answer
        key, and the resulting reference would differ from the file the author
        reviewed.
        """
        location = pathlib.Path(path)
        if not location.is_file():
            raise CommandError(f"no such source file: {path}")
        try:
            source = location.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"{path} is not valid UTF-8 ({exc.reason} at byte "
                f"{exc.start}); refusing to store a source that would have to "
                f"be altered to be read")
        except OSError as exc:
            raise CommandError(f"cannot read {path}: {exc.strerror}")

        if not source.strip():
            raise CommandError(f"{path} is empty or contains only whitespace")
        return source

    def _refuse_duplicate(self, question, language, alias):
        """
        One live reference per (question, language).

        A REJECTED row is not live and does not block a replacement — that is
        the normal "try again after review" path. Anything else would leave two
        candidate answer keys for one problem in one language, and
        `canonical_reference()` returns None when it cannot choose, which turns
        an authoring mistake into a silently unverifiable question.
        """
        existing = (ReferenceSolution.objects.using(alias)
                    .filter(question=question, language=language)
                    .exclude(review_state=ReferenceSolution.REVIEW_REJECTED)
                    .order_by("pk"))
        if existing.exists():
            rows = ", ".join(
                f"#{r.pk} ({r.review_state}"
                f"{', ACTIVE' if r.is_active else ''})" for r in existing)
            raise CommandError(
                f"question {question.pk} already has a non-REJECTED "
                f"{language} reference: {rows}. Reject or supersede it through "
                f"reference_review before authoring another.")

    def _warn_other_active(self, question, language, alias):
        """
        An active reference in a DIFFERENT language is a warning, not a block.

        The schema permits one active reference per language, but
        `canonical_reference()` returns None when more than one is active
        overall — so a second language is legitimate to author and would need a
        deliberate activation decision later. Worth saying out loud; not worth
        refusing.
        """
        others = (ReferenceSolution.objects.using(alias)
                  .filter(question=question, is_active=True)
                  .exclude(language=language))
        for other in others:
            self.stdout.write(self.style.WARNING(
                f"  note: question {question.pk} already has an ACTIVE "
                f"{other.language} reference (#{other.pk}). Activating a "
                f"second language would leave the question with no single "
                f"canonical oracle."))

    # ── output ────────────────────────────────────────────────────────

    def _render(self, reference, question, language, contract, source, operator):
        """
        Safe metadata only.

        The source is never echoed. A digest prefix and a byte count let the
        author confirm the right file was stored without the answer key
        appearing in a terminal, a CI log, or a screenshot.
        """
        encoded = source.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()

        write, style = self.stdout.write, self.style
        write(style.SUCCESS(f"Created reference #{reference.pk}."))
        write(f"  question        {question.pk} — {question.title[:48].strip()}")
        write(f"  language        {language}")
        write(f"  contract        {contract}")
        write(f"  source bytes    {len(encoded)}")
        write(f"  source sha256   {digest[:16]}…  (content digest, NOT "
              f"approval provenance)")
        write(f"  review_state    {reference.review_state}")
        write(f"  is_active       {reference.is_active}")
        write(f"  authored by     {operator.username}")
        write("")
        write("The source code is deliberately not shown. It is the answer key.")
        write("")
        write(style.WARNING(
            "NOT reviewed, NOT approved, NOT active, and NOT hashed — "
            "`source_hash`\nis approval provenance and is set by "
            "reference_review approve, which\nrecords who reviewed it and "
            "when. Next:\n\n"
            f"  python manage.py reference_review submit {reference.pk} "
            f"--operator <you>\n"
            f"  python manage.py reference_review approve {reference.pk} "
            f"--operator <reviewer> --confirm\n"
            f"  python manage.py reference_review activate {reference.pk} "
            f"--operator <you>"))
