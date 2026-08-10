"""
Hidden-test validator (M2 P2.5, Phase 8).

Read-only by design: it opens no write transaction and is safe to point at
production, which is the only place the real answer to "how many problems are
actually gradable?" lives. The P2.5 audit could not answer that question
because the development database is empty.

Exit codes are meaningful so CI and cron can gate on them:

    0  every problem meets the contract
    1  at least one problem FAILS or is BLOCKED
    2  the bank is empty (nothing to validate — treated as a failure, because
       a silent zero is how "all green" gets reported about nothing)

`--json` emits a machine-readable report for the scheduled job to archive.

Checks that depend on the oracle architecture (Phase 5) are reported as
UNKNOWN rather than silently passing — there is no reference-solution storage
in the schema yet, so no expected output in this system has ever been verified
by executing a trusted implementation. That is the single most important fact
this report can surface, and rounding it to PASS would defeat the purpose.
"""

import json

from django.core.management.base import BaseCommand

from groups.models import Question

#: Minimum coverage floor from the P2.5 contract. A floor, not a target.
MIN_HIDDEN_TESTS = 12

# Verdicts, worst first — a problem takes the worst that applies.
BLOCKED = "BLOCKED"   # cannot be graded at all
FAIL = "FAIL"         # gradable, but below the contract
PASS = "PASS"


class Command(BaseCommand):
    help = (
        "Read-only audit of hidden-test coverage. Reports problems with zero "
        "or too few hidden tests, malformed cases, duplicate inputs and "
        "missing expected outputs. Exit 1 if any problem fails."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Emit the machine-readable report instead of a table.")
        parser.add_argument("--min", type=int, default=MIN_HIDDEN_TESTS,
                            help=f"Coverage floor (default {MIN_HIDDEN_TESTS}).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Only list the first N non-passing problems in table mode.")

    def handle(self, *args, **options):
        floor = options["min"]
        rows = [self._inspect(q, floor) for q in Question.objects.all().only(
            "id", "title", "hidden_test_cases"
        ).order_by("id")]

        if options["json"]:
            self.stdout.write(json.dumps(self._report(rows, floor), indent=2))
        else:
            self._print_table(rows, floor, options["limit"])

        if not rows:
            raise SystemExit(2)
        raise SystemExit(1 if any(r["status"] != PASS for r in rows) else 0)

    # ── inspection ───────────────────────────────────────────

    def _inspect(self, question, floor):
        cases = question.hidden_test_cases
        problems = []

        if not isinstance(cases, list):
            return self._row(question, 0, BLOCKED,
                             ["hidden_test_cases is not a list"])
        if not cases:
            return self._row(question, 0, BLOCKED, ["no hidden tests"])

        well_formed = [c for c in cases if isinstance(c, dict)]
        if len(well_formed) != len(cases):
            problems.append(f"{len(cases) - len(well_formed)} case(s) are not objects")

        missing_expected = [
            c for c in well_formed
            if "expected_output" not in c or c.get("expected_output") is None
        ]
        if missing_expected:
            problems.append(f"{len(missing_expected)} case(s) missing expected_output")

        # stdin is REQUIRED to be non-empty by the generation contract in
        # ai_services: "stdin must NEVER be empty".
        blank_stdin = [c for c in well_formed if not str(c.get("stdin", "")).strip()]
        if blank_stdin:
            problems.append(f"{len(blank_stdin)} case(s) have empty stdin")

        stdins = [str(c.get("stdin", "")) for c in well_formed]
        duplicates = len(stdins) - len(set(stdins))
        if duplicates:
            # A duplicate input is not merely redundant: it inflates the count
            # toward the floor while testing nothing new.
            problems.append(f"{duplicates} duplicate stdin value(s)")

        count = len(cases)
        if count < floor:
            problems.append(f"{count} hidden test(s), floor is {floor}")

        # Cannot be graded at all: every case unusable.
        if len(missing_expected) == len(well_formed) or not well_formed:
            return self._row(question, count, BLOCKED, problems)

        return self._row(question, count, PASS if not problems else FAIL, problems)

    @staticmethod
    def _row(question, count, status, problems):
        return {
            "id": question.id,
            "title": question.title,
            "hidden": count,
            # No reference-solution storage exists yet (Phase 5), so no
            # expected output in this system has been verified by execution.
            "oracle": "NO",
            "verified": "UNKNOWN",
            "status": status,
            "problems": problems,
        }

    # ── output ───────────────────────────────────────────────

    def _report(self, rows, floor):
        return {
            "floor": floor,
            "total_problems": len(rows),
            "passing": sum(r["status"] == PASS for r in rows),
            "failing": sum(r["status"] == FAIL for r in rows),
            "blocked": sum(r["status"] == BLOCKED for r in rows),
            "zero_hidden_tests": sum(r["hidden"] == 0 for r in rows),
            "below_floor": sum(r["hidden"] < floor for r in rows),
            "with_verified_oracle": sum(r["verified"] == "YES" for r in rows),
            "problems": rows,
        }

    def _print_table(self, rows, floor, limit):
        if not rows:
            # ASCII only: this runs on a Windows console and in CI logs, and a
            # non-ASCII dash renders as a replacement character in cp1252.
            self.stdout.write(self.style.ERROR(
                "Question bank is EMPTY - nothing to validate."
            ))
            return

        failing = [r for r in rows if r["status"] != PASS]
        shown = failing[:limit] if limit else failing

        self.stdout.write(
            f"{'ID':>6}  {'Problem':<44} {'Hidden':>6}  {'Oracle':<7}"
            f"{'Verified':<10}{'Status':<8} Issues"
        )
        self.stdout.write("-" * 118)
        for r in shown:
            title = (r["title"][:41] + "...") if len(r["title"]) > 44 else r["title"]
            style = self.style.ERROR if r["status"] == BLOCKED else self.style.WARNING
            self.stdout.write(style(
                f"{r['id']:>6}  {title:<44} {r['hidden']:>6}  {r['oracle']:<7}"
                f"{r['verified']:<10}{r['status']:<8} {'; '.join(r['problems'])}"
            ))
        if limit and len(failing) > limit:
            self.stdout.write(f"... and {len(failing) - limit} more non-passing problem(s)")

        summary = self._report(rows, floor)
        self.stdout.write("")
        self.stdout.write(
            f"  {summary['total_problems']} problems | "
            f"PASS {summary['passing']} | FAIL {summary['failing']} | "
            f"BLOCKED {summary['blocked']}"
        )
        self.stdout.write(
            f"  zero hidden tests: {summary['zero_hidden_tests']} | "
            f"below floor of {floor}: {summary['below_floor']} | "
            f"with a verified oracle: {summary['with_verified_oracle']}"
        )
        if summary["with_verified_oracle"] == 0 and summary["total_problems"]:
            self.stdout.write(self.style.ERROR(
                "  No problem has a trusted reference solution: every expected "
                "output in the bank is unverified (P2.5 Phase 5 pending)."
            ))
