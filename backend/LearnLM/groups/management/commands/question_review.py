"""
Review a question's grading artifact (M2 P2.7g-3).

READ-ONLY, unconditionally. There is no --apply, no --approve and no flag that
writes anything; the command has no import that can reach a write path, and a
structural test asserts that rather than trusting this sentence.

Its whole job is to show a human everything they are about to vouch for, and
to print the artifact digest they will supply to `question_approve`. The
digest is the link between "what I read" and "what I signed": approving
requires pasting it back, so approving something other than what was displayed
is not expressible.

    python manage.py question_review --question 42 \\
        --quality-report reports/q42-quality.json --operator alice
"""

from django.core.management.base import BaseCommand

from groups.management.commands import _question_trust as trust


class Command(BaseCommand):
    help = ("Show a question's complete grading artifact and its digest. "
            "Read-only; writes nothing.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--quality-report", required=True, metavar="PATH",
                            help="JSON report from the P2.7h-1 quality gate.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection for the READ. This command writes "
                 "nothing; the alias only decides where the artifact is read "
                 "from.")
        parser.add_argument(
            "--show-source", action="store_true",
            help="Also print the reference source. It is the answer key — "
                 "withheld by default, as in reference_review.")

    def handle(self, *args, **options):
        # Staff is required even to LOOK. The artifact discloses per-case
        # output digests and, with --show-source, the answer key itself.
        trust.resolve_operator(options["operator"])

        alias = options["alias"]
        question = trust.resolve_question(options["question"], alias)
        reference = trust.resolve_reference(question)
        quality = trust.load_quality_outcome(options["quality_report"])
        artifact = trust.build(question, reference, quality, using=alias)

        trust.render_artifact(self, question, reference, artifact,
                              show_source=options["show_source"])

        self.stdout.write("")
        if artifact.blockers:
            self.stdout.write(self.style.ERROR(
                "This artifact is NOT approvable. Resolve the blockers above; "
                "the digest will change when you do."))
        else:
            self.stdout.write(
                "To approve, supply this digest back — it is checked against a "
                "freshly recomputed one:\n\n"
                f"  python manage.py question_approve --question {question.pk} "
                f"\\\n      --digest {artifact.digest()} \\\n"
                f"      --quality-report <same report> "
                f"--operator <you> --confirm\n")
