"""
Reconcile stored hidden tests against the trusted oracle (M2 P2.5, Phase 7).

Every expected output currently in the bank was produced by a language model
and has never been confirmed by executing a trusted implementation. Some are
probably wrong, which means learners are being marked Wrong Answer today for
correct solutions. This command finds out which, and does NOT fix them.

READ-ONLY, unconditionally. There is no --apply, no --fix and no flag that
makes it write: a first reconciliation run whose output nobody has read yet is
the worst possible moment to rewrite grading truth. Overwriting a MISMATCH is
only correct if the oracle is right, and the oracle has not itself been
reviewed yet. Deciding what to do with the report is a separate, human step.

    python manage.py reconcile_hidden_tests
    python manage.py reconcile_hidden_tests --json --database replica
    python manage.py reconcile_hidden_tests --question 42

Per-case classifications:

    MATCH                   stored output equals oracle output
    MISMATCH                they differ - stored grading truth is suspect
    MALFORMED               the case violates the hidden-test contract
    DUPLICATE               same stdin as an earlier case in this problem
    ORACLE_ERROR            the reference did not run cleanly on this input
    OUTPUT_CONTRACT_ERROR   output is not language-agnostic
    NO_ORACLE               no single canonical reference solution exists

Exit codes:

    0  every case reconciled and MATCHed
    1  at least one case did not
    2  the census could not be established (empty bank, unreachable database)
"""

import json

from django.core.management.base import BaseCommand
from django.db import OperationalError, connections

from groups.hidden_tests import validate_case
from groups.models import Question
from groups.oracle import (
    OracleError, OracleFailed, OracleNondeterministic, OracleService,
    OracleUnavailable, canonical_reference, canonical_reference_problem,
)
from groups.output_contract import (
    LANGUAGE_AGNOSTIC, UNKNOWN, classify_outputs,
)

MATCH = "MATCH"
MISMATCH = "MISMATCH"
MALFORMED = "MALFORMED"
DUPLICATE = "DUPLICATE"
ORACLE_ERROR = "ORACLE_ERROR"
OUTPUT_CONTRACT_ERROR = "OUTPUT_CONTRACT_ERROR"
NO_ORACLE = "NO_ORACLE"


def build_runner():
    """
    The real Judge0 runner, resolved late.

    Imported from `coding_views` at call time rather than at module import so
    that this module has no import-time dependency on the view layer, and so a
    test can substitute a stub without the network being reachable.
    """
    from groups.coding_views import _run_on_judge0
    return _run_on_judge0


class Command(BaseCommand):
    help = (
        "Read-only. Executes each problem's canonical reference solution "
        "against its stored hidden inputs and reports where the stored "
        "expected outputs disagree. Never modifies test data."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--database", default="default")
        parser.add_argument("--question", type=int, default=None,
                            help="Reconcile a single question id.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Stop after N problems (Judge0 is a blocking call).")

    def handle(self, *args, **options):
        alias = options["database"]
        emit_json = options["json"]

        if alias not in connections:
            return self._blocked(emit_json, alias,
                                 f"no database alias {alias!r} is configured")
        try:
            connections[alias].ensure_connection()
        except OperationalError as exc:
            return self._blocked(emit_json, alias,
                                 f"database {alias!r} is unreachable: {exc}")

        questions = (Question.objects.using(alias)
                     .prefetch_related("reference_solutions").order_by("id"))
        if options["question"] is not None:
            questions = questions.filter(pk=options["question"])
        if options["limit"]:
            questions = questions[:options["limit"]]

        service = OracleService(runner=self.get_runner())
        results = [self._reconcile(q, service) for q in questions]

        report = self._report(results, alias)
        if emit_json:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._print(report)

        if not results:
            raise SystemExit(2)
        raise SystemExit(0 if report["mismatched"] == 0
                         and report["unreconciled"] == 0 else 1)

    def get_runner(self):
        """Overridden in tests to inject a stub."""
        return build_runner()

    # ── reconciliation ───────────────────────────────────────

    def _reconcile(self, question, service):
        cases = question.hidden_test_cases
        entry = {
            "id": question.id, "title": question.title,
            "oracle_language": None, "cases": [],
        }

        if not isinstance(cases, list) or not cases:
            entry["cases"] = [{"case": None, "status": MALFORMED,
                               "detail": "no hidden tests"}]
            return entry

        reference = canonical_reference(question)
        if reference is None:
            detail = canonical_reference_problem(question)
            entry["cases"] = [{"case": i + 1, "status": NO_ORACLE,
                               "detail": detail} for i in range(len(cases))]
            return entry
        entry["oracle_language"] = reference.language

        seen = {}
        for index, case in enumerate(cases):
            entry["cases"].append(
                self._reconcile_case(question, reference, service, case, index, seen)
            )
        return entry

    def _reconcile_case(self, question, reference, service, case, index, seen):
        number = index + 1

        problems = validate_case(case, index)
        if problems:
            return {"case": number, "status": MALFORMED,
                    "detail": "; ".join(str(p) for p in problems)}

        stdin = case["stdin"]
        if stdin in seen:
            return {"case": number, "status": DUPLICATE,
                    "detail": f"same stdin as case {seen[stdin]}"}
        seen[stdin] = number

        try:
            actual = service.run(question, reference, stdin)
        except OracleUnavailable as exc:
            return {"case": number, "status": ORACLE_ERROR,
                    "detail": f"execution service unavailable: {exc}"}
        except OracleNondeterministic as exc:
            return {"case": number, "status": ORACLE_ERROR,
                    "detail": f"nondeterministic: {exc}"}
        except OracleFailed as exc:
            return {"case": number, "status": ORACLE_ERROR,
                    "detail": f"reference solution failed: {exc}"}
        except OracleError as exc:
            return {"case": number, "status": ORACLE_ERROR, "detail": str(exc)}

        contract, reasons = classify_outputs([actual])
        if contract not in (LANGUAGE_AGNOSTIC, UNKNOWN):
            return {"case": number, "status": OUTPUT_CONTRACT_ERROR,
                    "detail": "; ".join(reasons)}

        # Compare through the SAME normalization the grader uses. The oracle
        # already returned normalized output; normalizing the stored value the
        # same way is what makes MATCH mean "the grader would accept this".
        from groups.utils import normalize_output
        if normalize_output(case["expected_output"]) == actual:
            return {"case": number, "status": MATCH, "detail": ""}

        # The stored value is NOT echoed into the report: this output is
        # designed to be archived by a scheduled job, and the stored value is
        # the answer key. The case number is enough to find it.
        return {"case": number, "status": MISMATCH,
                "detail": "stored expected_output disagrees with the oracle"}

    # ── reporting ────────────────────────────────────────────

    def _report(self, results, alias):
        counts = {}
        for entry in results:
            for case in entry["cases"]:
                counts[case["status"]] = counts.get(case["status"], 0) + 1

        total_cases = sum(len(e["cases"]) for e in results)
        return {
            "database": alias,
            "census": "VERIFIED" if results else "BLOCKED",
            "total_problems": len(results),
            "total_cases": total_cases,
            "counts": counts,
            "matched": counts.get(MATCH, 0),
            "mismatched": counts.get(MISMATCH, 0),
            "unreconciled": total_cases - counts.get(MATCH, 0) - counts.get(MISMATCH, 0),
            "problems": results,
        }

    def _blocked(self, emit_json, alias, reason):
        payload = {"database": alias, "census": "BLOCKED", "reason": reason,
                   "total_problems": None, "problems": []}
        if emit_json:
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self.stdout.write(self.style.ERROR(f"CENSUS BLOCKED: {reason}"))
        raise SystemExit(2)

    def _print(self, report):
        if not report["total_problems"]:
            self.stdout.write(self.style.ERROR(
                f"CENSUS BLOCKED: database {report['database']!r} holds no "
                f"questions - nothing to reconcile."
            ))
            return

        self.stdout.write(f"{'problem':>8}  {'case':>5}  {'status':<22} detail")
        self.stdout.write("-" * 100)
        for entry in report["problems"]:
            for case in entry["cases"]:
                if case["status"] == MATCH:
                    continue
                number = case["case"] if case["case"] is not None else "-"
                self.stdout.write(self.style.WARNING(
                    f"{entry['id']:>8}  {number:>5}  {case['status']:<22} "
                    f"{case['detail'][:60]}"
                ))

        self.stdout.write("")
        self.stdout.write(
            f"  {report['total_problems']} problems | {report['total_cases']} cases"
        )
        for status, count in sorted(report["counts"].items()):
            self.stdout.write(f"    {status:<24} {count}")
        if report["mismatched"]:
            self.stdout.write(self.style.ERROR(
                f"  {report['mismatched']} case(s) disagree with the oracle. "
                f"Nothing was changed - decide per problem whether the stored "
                f"output or the reference solution is wrong."
            ))
