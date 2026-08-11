"""
Tier-A execution-contract reconciliation report (M2 P2.7a-1).

READ-ONLY and pure static analysis: no Judge0, no oracle, no learner code, no
writes of any kind. Safe to point at production, which is the only place the
real inventory lives.

    python manage.py reconcile_execution_contracts
    python manage.py reconcile_execution_contracts --json --database replica
    python manage.py reconcile_execution_contracts --recommendation ELIGIBLE_FOR_V2_REVIEW

It RECOMMENDS and never migrates. `V2_ONLY` means "this question's stored data
appears compatible with v2 and incompatible with v1" — not "flip the version".
A real migration needs boilerplate, an approved oracle, hidden tests and the
publishability contract, all later phases.

Exit codes, for CI and the daily job:

    0  every question is currently gradable under its configured contract
    1  at least one question is not
    2  the inventory could not be established — empty bank, or the database is
       unreachable. NEVER interpreted as zero questions: a connection failure
       that reported "0 problems, 0 failures" would look identical to a clean
       bank, which is how an outage becomes a green dashboard.

The report never contains a hidden input, an expected output, or reference
source. It is designed to be archived by a scheduled job, and those three are
grading truth.
"""

import json

from django.core.management.base import BaseCommand
from django.db import OperationalError, connections

from groups import contract_reconciliation as tier_a
from groups.models import Question


class Command(BaseCommand):
    help = (
        "Read-only. Classifies every question's stored test data against the "
        "execution contract it is configured for. Recommends; never migrates."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable report for a future census.")
        parser.add_argument("--database", default="default",
                            help="Django database alias to inspect.")
        parser.add_argument("--recommendation", default=None,
                            help="Only list questions with this recommendation.")
        parser.add_argument("--limit", type=int, default=0,
                            help="List only the first N non-gradable questions.")

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

        questions = (
            Question.objects.using(alias)
            .only("id", "title", "hidden_test_cases", "boilerplate_code",
                  "hidden_wrapper_code", "execution_contract_version")
            .prefetch_related("reference_solutions")
            .order_by("id")
        )

        records = []
        for question in questions:
            has_reference = any(
                solution.is_active for solution in question.reference_solutions.all()
            )
            records.append(tier_a.classify_question(question, has_reference))

        report = self._report(records, alias)

        if emit_json:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._print(report, options["recommendation"], options["limit"])

        if not records:
            raise SystemExit(2)
        raise SystemExit(0 if report["currently_gradable"] == report["total_questions"] else 1)

    # ── reporting ────────────────────────────────────────────

    def _report(self, records, alias):
        def count(key, value):
            return sum(1 for r in records if r.get(key) == value)

        return {
            "database": alias,
            "census": "VERIFIED" if records else "BLOCKED",
            "tier": "A (static)",
            "total_questions": len(records),
            "currently_gradable": sum(1 for r in records if r["currently_gradable"] is True),
            "not_gradable": sum(1 for r in records if r["currently_gradable"] is False),
            "gradability_unknown": sum(1 for r in records if r["currently_gradable"] is None),
            "implied_contract": {
                name: count("implied_contract", name)
                for name in (
                    tier_a.V1_ONLY, tier_a.V2_ONLY, tier_a.AMBIGUOUS_CONTRACT,
                    tier_a.NEITHER, tier_a.CONTRACT_MISMATCH, tier_a.MISSING_TESTS,
                    tier_a.INVALID_TESTS, tier_a.CUSTOM_WRAPPER,
                )
            },
            "boilerplate": {
                tier_a.MISSING_BOILERPLATE: sum(
                    1 for r in records if tier_a.MISSING_BOILERPLATE in r["blockers"]),
                tier_a.INVALID_BOILERPLATE: sum(
                    1 for r in records if tier_a.INVALID_BOILERPLATE in r["blockers"]),
            },
            "recommendation": {
                name: count("recommendation", name)
                for name in (tier_a.KEEP_V1, tier_a.ELIGIBLE_FOR_V2_REVIEW,
                             tier_a.CUSTOM_WRAPPER_REVIEW, tier_a.BLOCKED)
            },
            "with_active_reference": sum(1 for r in records if r["has_active_reference"]),
            "questions": records,
        }

    def _blocked(self, emit_json, alias, reason):
        payload = {"database": alias, "census": "BLOCKED", "tier": "A (static)",
                   "reason": reason, "total_questions": None, "questions": []}
        if emit_json:
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self.stdout.write(self.style.ERROR(f"CENSUS BLOCKED: {reason}"))
        raise SystemExit(2)

    def _print(self, report, only_recommendation, limit):
        if not report["total_questions"]:
            self.stdout.write(self.style.ERROR(
                f"CENSUS BLOCKED: database {report['database']!r} holds no "
                f"questions - nothing to reconcile."
            ))
            return

        rows = [r for r in report["questions"] if r["currently_gradable"] is not True]
        if only_recommendation:
            rows = [r for r in rows if r["recommendation"] == only_recommendation]
        shown = rows[:limit] if limit else rows

        self.stdout.write(
            f"{'ID':>6}  {'Problem':<34} {'cfg':<4} {'implied':<19} "
            f"{'tests':>5}  {'recommendation':<24} blockers"
        )
        self.stdout.write("-" * 126)
        for r in shown:
            title = (r["title"][:31] + "...") if len(r["title"]) > 34 else r["title"]
            style = (self.style.ERROR if r["recommendation"] == tier_a.BLOCKED
                     else self.style.WARNING)
            self.stdout.write(style(
                f"{r['id']:>6}  {title:<34} {r['configured_contract']:<4} "
                f"{str(r['implied_contract']):<19} {r['hidden_test_count']:>5}  "
                f"{str(r['recommendation']):<24} {'; '.join(r['blockers'])[:40]}"
            ))
        if limit and len(rows) > limit:
            self.stdout.write(f"... and {len(rows) - limit} more")

        self.stdout.write("")
        self.stdout.write(
            f"  database {report['database']!r} | census {report['census']} | "
            f"tier {report['tier']} | {report['total_questions']} questions"
        )
        self.stdout.write(
            f"  gradable {report['currently_gradable']} | "
            f"NOT gradable {report['not_gradable']} | "
            f"unknown {report['gradability_unknown']}"
        )
        self.stdout.write("  implied contract: " + " | ".join(
            f"{k} {v}" for k, v in report["implied_contract"].items() if v
        ) or "  implied contract: -")
        self.stdout.write("  recommendation:   " + " | ".join(
            f"{k} {v}" for k, v in report["recommendation"].items() if v
        ))
        self.stdout.write(
            f"  boilerplate missing {report['boilerplate'][tier_a.MISSING_BOILERPLATE]} | "
            f"invalid {report['boilerplate'][tier_a.INVALID_BOILERPLATE]} | "
            f"with an active reference {report['with_active_reference']}"
        )
        if report["not_gradable"]:
            self.stdout.write(self.style.ERROR(
                f"  {report['not_gradable']} question(s) cannot be graded under "
                f"their configured contract. Nothing was changed - migration is "
                f"a separate, explicit operation."
            ))
