"""
Hidden-test and oracle validator (M2 P2.5, Phases 6 + 8).

Read-only. It opens no write transaction, so it is safe to point at
production — which is the only place the real coverage numbers live, since the
development database is empty.

    python manage.py validate_question_bank
    python manage.py validate_question_bank --json
    python manage.py validate_question_bank --database replica

Exit codes are the contract for CI and cron:

    0  every problem passed every check that could be RUN
    1  at least one problem FAILED or is BLOCKED
    2  the census could not be established at all — empty bank, or the
       requested database is unreachable

2 is not "no news". Zero problems means zero failures, which a naive tool
reports as success; that is exactly how "all green" gets announced about
nothing, and it is the state the development database is in right now.

Three-state reporting, never rounded:

    VERIFIED   checked against an executed oracle and agreed
    UNKNOWN    could not be checked — no active reference solution, or
               oracle execution was not requested
    BLOCKED    cannot be graded at all

`UNKNOWN` is deliberately not `PASS`. Every expected output in this bank was
produced by a language model and has never been confirmed by executing a
trusted implementation; reporting that as passing would launder the precise
defect P2.5 exists to fix.
"""

import json

from django.core.management.base import BaseCommand
from django.db import OperationalError, connections

from common import languages
from groups.hidden_tests import MIN_HIDDEN_TESTS, is_gradable, validate_suite
from groups.models import Question, ReferenceSolution

BLOCKED = "BLOCKED"
FAIL = "FAIL"
PASS = "PASS"

VERIFIED = "VERIFIED"
UNKNOWN = "UNKNOWN"


class Command(BaseCommand):
    help = (
        "Read-only audit of hidden-test coverage and reference-solution "
        "availability. Exit 1 on validation failure, 2 if the census cannot "
        "be established."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable report instead of a table.")
        parser.add_argument("--database", default="default",
                            help="Django database alias to audit (default: 'default').")
        parser.add_argument("--min", type=int, default=MIN_HIDDEN_TESTS,
                            help=f"Coverage floor (default {MIN_HIDDEN_TESTS}).")
        parser.add_argument("--limit", type=int, default=0,
                            help="List only the first N non-passing problems.")

    def handle(self, *args, **options):
        alias = options["database"]
        floor = options["min"]
        emit_json = options["json"]

        # A database we cannot reach is UNKNOWN, never an implicit zero.
        # Without this the command reports "0 problems" against a bad DSN and
        # a scheduled job would treat that as a clean bank.
        if alias not in connections:
            return self._unavailable(
                emit_json, alias, f"no database alias {alias!r} is configured")
        try:
            connections[alias].ensure_connection()
        except OperationalError as exc:
            return self._unavailable(
                emit_json, alias, f"database {alias!r} is unreachable: {exc}")

        rows = [
            self._inspect(question, floor)
            for question in Question.objects.using(alias)
                                    .only("id", "title", "hidden_test_cases")
                                    .prefetch_related("reference_solutions")
                                    .order_by("id")
        ]

        report = self._report(rows, floor, alias, census="VERIFIED" if rows else "BLOCKED")

        if emit_json:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._print_table(report, options["limit"])

        if not rows:
            raise SystemExit(2)
        raise SystemExit(1 if any(r["status"] != PASS for r in rows) else 0)

    # ── inspection ───────────────────────────────────────────

    def _inspect(self, question, floor):
        cases = question.hidden_test_cases
        problems = [str(p) for p in validate_suite(cases, floor)]

        active = [
            s for s in question.reference_solutions.all() if s.is_active
        ]
        oracle_languages = sorted(s.language for s in active)

        for language in oracle_languages:
            if language not in languages.ACCEPTED_SPELLINGS:
                problems.append(
                    f"reference solution language {language!r} is not a "
                    f"supported Judge0 language"
                )

        if not active:
            # Not a FAIL on its own — no problem in the bank has one yet, and
            # flagging all of them as failures says nothing useful. It is what
            # makes the outputs UNVERIFIED, which the summary reports loudly.
            problems.append("no active reference solution (outputs unverified)")

        gradable = is_gradable(cases)
        status = BLOCKED if not gradable else (PASS if not problems else FAIL)

        return {
            "id": question.id,
            "title": question.title,
            "hidden": len(cases) if isinstance(cases, list) else 0,
            "oracle": "YES" if active else "NO",
            "oracle_languages": oracle_languages,
            # Oracle EXECUTION is Phase 7 and is not run here, so agreement
            # between stored outputs and a trusted run is not yet knowable.
            "verified": UNKNOWN,
            "status": status,
            "problems": problems,
        }

    # ── reporting ────────────────────────────────────────────

    def _report(self, rows, floor, alias, census):
        return {
            "database": alias,
            "census": census,
            "floor": floor,
            "total_problems": len(rows),
            "passing": sum(r["status"] == PASS for r in rows),
            "failing": sum(r["status"] == FAIL for r in rows),
            "blocked": sum(r["status"] == BLOCKED for r in rows),
            "zero_hidden_tests": sum(r["hidden"] == 0 for r in rows),
            "below_floor": sum(r["hidden"] < floor for r in rows),
            "with_active_oracle": sum(r["oracle"] == "YES" for r in rows),
            "verified_outputs": sum(r["verified"] == VERIFIED for r in rows),
            "problems": rows,
        }

    def _unavailable(self, emit_json, alias, reason):
        payload = {
            "database": alias, "census": "BLOCKED", "reason": reason,
            "total_problems": None, "problems": [],
        }
        if emit_json:
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self.stdout.write(self.style.ERROR(f"CENSUS BLOCKED: {reason}"))
        raise SystemExit(2)

    def _print_table(self, report, limit):
        if not report["total_problems"]:
            self.stdout.write(self.style.ERROR(
                f"CENSUS BLOCKED: database {report['database']!r} holds no "
                f"questions - nothing to validate."
            ))
            return

        failing = [r for r in report["problems"] if r["status"] != PASS]
        shown = failing[:limit] if limit else failing

        self.stdout.write(
            f"{'ID':>6}  {'Problem':<40} {'Hidden':>6}  {'Oracle':<7}"
            f"{'Verified':<10}{'Status':<9} Issues"
        )
        self.stdout.write("-" * 120)
        for r in shown:
            title = (r["title"][:37] + "...") if len(r["title"]) > 40 else r["title"]
            style = self.style.ERROR if r["status"] == BLOCKED else self.style.WARNING
            self.stdout.write(style(
                f"{r['id']:>6}  {title:<40} {r['hidden']:>6}  {r['oracle']:<7}"
                f"{r['verified']:<10}{r['status']:<9} {'; '.join(r['problems'])}"
            ))
        if limit and len(failing) > limit:
            self.stdout.write(f"... and {len(failing) - limit} more non-passing problem(s)")

        self.stdout.write("")
        self.stdout.write(
            f"  database {report['database']!r} | census {report['census']} | "
            f"{report['total_problems']} problems"
        )
        self.stdout.write(
            f"  PASS {report['passing']} | FAIL {report['failing']} | "
            f"BLOCKED {report['blocked']}"
        )
        self.stdout.write(
            f"  zero hidden tests: {report['zero_hidden_tests']} | "
            f"below floor of {report['floor']}: {report['below_floor']} | "
            f"with an active oracle: {report['with_active_oracle']}"
        )
        if report["verified_outputs"] == 0:
            self.stdout.write(self.style.ERROR(
                "  0 problems have outputs verified against an executed "
                "oracle. Every expected output in this bank is unverified "
                "(P2.5 Phase 7 pending)."
            ))
