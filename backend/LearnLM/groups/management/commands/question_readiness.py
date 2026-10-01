"""
Canonical question readiness over the bank, or a subset of it (QP1).

    python manage.py question_readiness                     # the whole bank
    python manage.py question_readiness --questions 121 132 --json
    python manage.py question_readiness --topic Array --category D
    python manage.py question_readiness --out readiness.json

READ-ONLY, three ways over:

  1. the census connection gates (`census_gates.run_all`) run first: against
     production the role must hold no write privilege, or nothing runs;
  2. a statement guard is installed on the connection for the whole run, and
     any statement that is not a read aborts the command;
  3. `question_readiness` itself has no write path.

The per-question JSON quotes stored inputs inside refusal details, so it is
grading data: write it outside the repository.
"""

import json
import pathlib
import time
import tracemalloc
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from groups import census_gates
from groups import question_readiness as readiness
from groups.models import Question

#: Statement prefixes a read-only run may issue. Anything else is a write, a
#: lock or DDL, and stops the command.
READ_PREFIXES = ("SELECT", "WITH", "SHOW", "SET ")


class ReadOnlyViolation(CommandError):
    """The run tried to issue a statement that is not a read."""


class _StatementGuard:
    """Counts statements and refuses any that is not a read."""

    def __init__(self):
        self.count = 0

    def __call__(self, execute, sql, params, many, context):
        statement = (sql or "").lstrip().upper()
        if not statement.startswith(READ_PREFIXES):
            raise ReadOnlyViolation(
                f"question_readiness is read-only and refused to issue: "
                f"{statement[:80]!r}")
        self.count += 1
        return execute(sql, params, many, context)


class Command(BaseCommand):
    help = ("Read-only readiness report: stage, blockers, one category A-I, "
            "per-language readiness and adaptive eligibility per question.")

    def add_arguments(self, parser):
        parser.add_argument("--questions", nargs="+", type=int, metavar="ID")
        parser.add_argument("--topic", metavar="NAME")
        parser.add_argument("--category", choices=sorted(readiness.CATEGORIES),
                            help="Only report questions in this category.")
        parser.add_argument("--json", action="store_true",
                            help="Print the JSON report instead of a summary.")
        parser.add_argument("--out", metavar="PATH",
                            help="Write the JSON report, with every question, "
                                 "to a file.")
        parser.add_argument("--measure-memory", action="store_true",
                            help="Track peak Python allocation (slower).")
        parser.add_argument(
            "--allow-non-production", action="store_true",
            help="Permit a loopback/private database (census gate).")
        parser.add_argument(
            "--allow-write-role", action="store_true",
            help="Proceed with a role that can write. Test databases only; the "
                 "statement guard still refuses every write.")

    def handle(self, *args, **options):
        try:
            identity = census_gates.run_all(
                "default",
                allow_non_production=options["allow_non_production"],
                require_read_only=not options["allow_write_role"])
        except census_gates.GateFailure as failure:
            raise CommandError(f"CONNECTION GATE FAILED - nothing was read.\n\n"
                               f"{failure}")

        guard = _StatementGuard()
        if options["measure_memory"]:
            tracemalloc.start()
        started = time.monotonic()
        with connection.execute_wrapper(guard):
            reports, errors = self._assess(options)
        elapsed = time.monotonic() - started
        peak = None
        if options["measure_memory"]:
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()

        payload = self._payload(identity, options, reports, elapsed,
                                guard.count, peak)
        payload["errors"] = errors
        if options["out"]:
            pathlib.Path(options["out"]).write_text(
                json.dumps(payload, indent=1, sort_keys=True, default=str),
                encoding="utf-8")
        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=1, sort_keys=True,
                                         default=str))
        else:
            self._render(payload)
        if errors:
            # Reported AFTER the rest of the bank, so one malformed row costs
            # one question's verdict, not the whole census; and still a
            # failure, so a run with a gap in it can never pass as complete.
            raise CommandError(
                f"{len(errors)} question(s) could not be assessed: "
                f"{[e['question_id'] for e in errors][:20]}")

    # ── reading ───────────────────────────────────────────────────────

    def _assess(self, options):
        queryset = Question.objects.select_related("topic").order_by("pk")
        question_ids = None
        if options["questions"]:
            question_ids = sorted(set(options["questions"]))
            queryset = queryset.filter(pk__in=question_ids)
        if options["topic"]:
            queryset = queryset.filter(topic__name__iexact=options["topic"])
            question_ids = list(queryset.values_list("pk", flat=True))

        context = readiness.BankContext.load(question_ids=question_ids)
        reports, errors = [], []
        for question in queryset.iterator(chunk_size=200):
            try:
                reports.append(readiness.assess(question, context))
            except ReadOnlyViolation:
                raise
            except Exception as exc:                          # noqa: BLE001
                errors.append({"question_id": question.pk,
                               "error": repr(exc)[:300]})
        if options["category"]:
            reports = [r for r in reports if r.category == options["category"]]
        return reports, errors

    # ── reporting ─────────────────────────────────────────────────────

    def _payload(self, identity, options, reports, elapsed, queries, peak):
        languages = {}
        for report in reports:
            for entry in report.language_readiness:
                bucket = languages.setdefault(entry.language, Counter())
                bucket[f"{entry.verdict}" + (f":{entry.cause}" if entry.cause else "")] += 1
                if entry.served:
                    bucket["served"] += 1
                if entry.adaptive_eligible:
                    bucket["adaptive_eligible"] += 1
        return {
            "schema_version": readiness.SCHEMA_VERSION,
            "generated_at": timezone.now().isoformat(),
            "database": {k: identity.get(k) for k in (
                "database", "role", "is_production", "read_only",
                "latest_migration")},
            "filters": {k: options[k] for k in ("questions", "topic", "category")},
            "questions": len(reports),
            "categories": dict(sorted(Counter(r.category for r in reports).items())),
            "reasons": dict(sorted(Counter(f"{r.category}:{r.reason}"
                                           for r in reports).items())),
            "stages": {stage: n for stage in readiness.STAGES
                       if (n := sum(1 for r in reports if r.stage == stage))},
            "blockers": dict(sorted(Counter(b.code for r in reports
                                            for b in r.blockers).items())),
            "servable": sum(1 for r in reports if r.servable),
            "adaptive_eligible": sum(1 for r in reports if r.is_adaptive_eligible),
            "languages": {lang: dict(sorted(c.items()))
                          for lang, c in sorted(languages.items())},
            "measurements": {"elapsed_seconds": round(elapsed, 1),
                             "queries": queries,
                             "peak_python_bytes": peak},
            "reports": [r.as_dict() for r in reports],
        }

    def _render(self, payload):
        write = self.stdout.write
        write(self.style.MIGRATE_HEADING("QUESTION READINESS (QP1) - READ-ONLY"))
        db = payload["database"]
        write(f"  database {db['database']}  role {db['role']}  production "
              f"{db['is_production']}  read-only role {db['read_only']}")
        write(f"  questions {payload['questions']}   servable "
              f"{payload['servable']}   adaptive-eligible "
              f"{payload['adaptive_eligible']}")
        m = payload["measurements"]
        write(f"  elapsed {m['elapsed_seconds']}s   queries {m['queries']}"
              + (f"   peak {m['peak_python_bytes'] / 1e6:.1f} MB"
                 if m["peak_python_bytes"] else ""))
        write("\nCategories")
        for letter, n in payload["categories"].items():
            write(f"  {letter} {readiness.CATEGORIES[letter].name:24} {n:6}")
        write("\nCategory reasons")
        for reason, n in payload["reasons"].items():
            write(f"  {reason:60} {n:6}")
        write("\nStages")
        for stage, n in payload["stages"].items():
            write(f"  {stage:24} {n:6}")
        write("\nBlockers")
        for code, n in payload["blockers"].items():
            write(f"  {code:32} {n:6}")
        write("\nLanguages")
        for lang, counts in payload["languages"].items():
            write(f"  {lang:11} {counts}")
