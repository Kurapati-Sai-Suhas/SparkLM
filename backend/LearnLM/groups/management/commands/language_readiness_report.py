"""
Per-language execution readiness across the servable bank (Phase 1 M4).

READ-ONLY. No --apply, no flag that writes.

    python manage.py language_readiness_report
    python manage.py language_readiness_report --language cpp --sample 10
    python manage.py language_readiness_report --json

M1 built the predicate; this is the measurement over real content. It exists
because "C++ readiness is 0/1788" is a number nobody can act on. A repair
worklist needs the CAUSES separated: a question with no C++ starter at all
needs content authored, while one whose starter is a `Solution` class needs
that starter replaced with a complete program. Those are different jobs with
different costs, and reporting them as one figure hid that.
"""

import collections
import json

from django.core.management.base import BaseCommand

from common import languages
from groups import language_readiness as readiness


class Command(BaseCommand):
    help = ("Measure execution readiness per language over every servable "
            "question, broken down by cause. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--language", metavar="KEY",
                            help="Restrict to one language.")
        parser.add_argument("--sample", type=int, default=0, metavar="N",
                            help="Show N example question ids per cause.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        from groups.coding_views import _servable_questions

        selected = ([options["language"]] if options["language"]
                    else [lang.key for lang in languages.REGISTRY])

        verdicts = collections.defaultdict(collections.Counter)
        causes = collections.defaultdict(collections.Counter)
        examples = collections.defaultdict(list)

        questions = list(_servable_questions().only(
            "id", "boilerplate_code", "execution_contract_version"))
        for question in questions:
            for key in selected:
                result = readiness.assess(question, key)
                verdicts[key][result.verdict] += 1
                if result.verdict == readiness.NOT_READY:
                    causes[key][result.cause or "other"] += 1
                    bucket = examples[(key, result.cause or "other")]
                    if len(bucket) < options["sample"]:
                        bucket.append(question.pk)

        payload = {
            "servable_questions": len(questions),
            "languages": {
                key: {
                    "ready": verdicts[key][readiness.READY],
                    "unknown": verdicts[key][readiness.UNKNOWN],
                    "not_ready": verdicts[key][readiness.NOT_READY],
                    "causes": dict(causes[key]),
                    "examples": {cause: examples[(key, cause)]
                                 for cause in causes[key]
                                 if examples[(key, cause)]},
                }
                for key in selected
            },
        }

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2))
            return

        self._render(payload, selected)

    def _render(self, payload, selected):
        write = self.stdout.write

        write(self.style.MIGRATE_HEADING(
            "LANGUAGE EXECUTION READINESS — read-only"))
        write(f"  servable questions {payload['servable_questions']}")
        write("")
        write(f"  {'language':<12}{'READY':>8}{'UNKNOWN':>9}{'NOT_READY':>11}")
        for key in selected:
            row = payload["languages"][key]
            write(f"  {key:<12}{row['ready']:>8}{row['unknown']:>9}"
                  f"{row['not_ready']:>11}")
        write("")

        for key in selected:
            row = payload["languages"][key]
            if not row["causes"]:
                continue
            write(self.style.MIGRATE_LABEL(f"{key} — why not ready"))
            for cause, count in sorted(row["causes"].items(),
                                       key=lambda item: -item[1]):
                write(f"    {cause:<24} {count}")
                sample = row["examples"].get(cause)
                if sample:
                    write(f"      e.g. {', '.join(f'q{i}' for i in sample)}")
            write("")

        write("UNKNOWN is not a failure: the starter shape is right and the")
        write("checker cannot decide more without a compiler. It counts as")
        write("servable, because refusing what a checker cannot prove would")
        write("hide failures behind the checker's limits.")
        write("")
        write("Nothing was written. This command has no write path.")
