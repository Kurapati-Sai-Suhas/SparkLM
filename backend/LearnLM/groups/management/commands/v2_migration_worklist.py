"""
The authoritative v1 -> v2 structural migration worklist (Phase 1 M14).

READ-ONLY. No --apply, no flag that writes, no Oracle call, no execution of
learner or reference code. It reads the bank and prints a verdict.

    python manage.py v2_migration_worklist
    python manage.py v2_migration_worklist --json
    python manage.py v2_migration_worklist --bucket SAFE_TO_MIGRATE
    python manage.py v2_migration_worklist --question 100

It replaces the hand-derived table in docs/V2_MIGRATION_WAVE.md. That table
said 22 SAFE_TO_MIGRATE and enumerated 16 of them, and nothing implemented the
rule, so it could not be re-derived or corrected as content moved underneath
it. Re-deriving it found four defect classes -- structures that never reach
the graded method, collections of structures, arity mismatches, and answer
keys that stop matching once output is serialised canonically.

The classification itself lives in `groups.migration_readiness`, which never
touches the ORM, so it can be tested on literal starters rather than fixtures.
This command is the only part that reads the database.
"""

import json

from django.core.management.base import BaseCommand

from common import languages
from groups import execution_contract, language_readiness, migration_readiness
from groups.models import Question


class Command(BaseCommand):
    help = ("Classify every structural question by whether it can move to the "
            "v2 execution contract. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="machine-readable output on stdout")
        parser.add_argument("--bucket", default=None,
                            help="only report this bucket")
        parser.add_argument("--question", type=int, default=None,
                            help="only report this question id")
        parser.add_argument("--readiness", action="store_true",
                            help="include per-language v2 readiness (slower)")

    def handle(self, *args, **options):
        rows = list(
            Question.objects.all().order_by("id").values(
                "id", "title", "base_difficulty", "boilerplate_code",
                "hidden_test_cases", "execution_contract_version"))

        results = []
        for row in rows:
            starter = row["boilerplate_code"] or {}
            if not isinstance(starter, dict):
                starter = {}
            readiness = (self._readiness(starter) if options["readiness"]
                         else None)
            verdict = migration_readiness.classify(
                row["id"], starter.get("python") or "",
                row["hidden_test_cases"] or [],
                version=row["execution_contract_version"] or "",
                readiness=readiness)
            if verdict is None:
                continue
            verdict.title = (row["title"] or "").strip()
            verdict.difficulty = row["base_difficulty"]
            results.append(verdict)

        if options["question"] is not None:
            results = [r for r in results
                       if r.question_id == options["question"]]
        if options["bucket"]:
            results = [r for r in results if r.bucket == options["bucket"]]

        if options["json"]:
            self.stdout.write(json.dumps(self._payload(results), indent=2,
                                         sort_keys=True))
        else:
            self._render(results)

    @staticmethod
    def _readiness(starter):
        out = {}
        for language in languages.REGISTRY:
            verdict = language_readiness.assess_source(
                starter.get(language.key) or "", language.key,
                version=execution_contract.CONTRACT_V2)
            out[language.key] = verdict.verdict
        return out

    @staticmethod
    def _payload(results):
        by_bucket = {}
        for result in results:
            by_bucket.setdefault(result.bucket, []).append(result.question_id)
        return {
            "structural_total": len(results),
            "counts": {bucket: len(by_bucket.get(bucket, []))
                       for bucket in migration_readiness.BUCKETS},
            "ids": {bucket: sorted(by_bucket.get(bucket, []))
                    for bucket in migration_readiness.BUCKETS},
            "questions": [dict(result.as_dict(),
                               title=getattr(result, "title", ""),
                               difficulty=getattr(result, "difficulty", None))
                          for result in results],
        }

    def _render(self, results):
        by_bucket = {}
        for result in results:
            by_bucket.setdefault(result.bucket, []).append(result)

        self.stdout.write(f"structural questions: {len(results)}\n")
        for bucket in migration_readiness.BUCKETS:
            rows = by_bucket.get(bucket, [])
            if not rows:
                continue
            self.stdout.write(f"\n{bucket}: {len(rows)}")
            self.stdout.write(
                f"  ids: {sorted(r.question_id for r in rows)}")

        safe = by_bucket.get(migration_readiness.SAFE_TO_MIGRATE, [])
        if safe:
            self.stdout.write("\n" + "=" * 68)
            self.stdout.write("SAFE_TO_MIGRATE detail")
            for result in sorted(safe, key=lambda r: r.question_id):
                kinds = ",".join(result.parameter_kinds) or "-"
                self.stdout.write(
                    f"  q{result.question_id:<5} {getattr(result,'title','')[:34]:<34} "
                    f"in=({kinds}) out={result.return_kind or 'scalar'}")
                for note in result.notes:
                    self.stdout.write(f"        note: {note}")
