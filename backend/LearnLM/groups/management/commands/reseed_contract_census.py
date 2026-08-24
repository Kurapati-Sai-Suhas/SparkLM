"""
Report the contract position of every reseed candidate (M2 P2.7h-26).

READ-ONLY BY CONSTRUCTION. The command opens no transaction, calls no writing
helper and touches no external service. It answers a counting question, and a
counting question that modifies its subject is not a census.
"""

import json

from django.core.management.base import BaseCommand

from groups import reseed_contract_census as census
from groups.models import OracleExecution, Question, QuestionApproval

#: The clauses of `reseed_authoring.statement_blockers`, expressed over a values
#: dict so the whole bank can be classified in one query instead of 2,926.
#:
#: Kept as named clauses rather than a single boolean because the candidate
#: count has already been reported under two different predicates: Phase 6 said
#: 1,141, this says 1,140. Naming the clauses is what let that be explained
#: (see `--near-miss`) instead of argued about.
CLAUSE_NAMES = ("draft", "unverified", "no_cases", "no_approval",
                "no_execution", "placeholder_marker")


def clause_results(row, approved, executed):
    return {
        "draft": row["status"] == Question.STATUS_DRAFT,
        "unverified": row["trust_state"] == Question.TRUST_UNVERIFIED,
        "no_cases": not row["hidden_test_cases"],
        "no_approval": row["id"] not in approved,
        "no_execution": row["id"] not in executed,
        "placeholder_marker": Question.PLACEHOLDER_MARKER in (
            row["content"] or ""),
    }


class Command(BaseCommand):
    help = "Contract census of the reseed candidate population (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--json", dest="as_json", action="store_true",
            help="emit the census as JSON instead of a report")
        parser.add_argument(
            "--near-miss", action="store_true",
            help="list questions that fail exactly one candidate clause")
        parser.add_argument(
            "--sample", type=int, default=0,
            help="show this many classified candidates individually")

    def handle(self, *args, **options):
        approved = set(QuestionApproval.objects.values_list(
            "question_id", flat=True))
        executed = set(OracleExecution.objects.values_list(
            "question_id", flat=True))

        rows = list(Question.objects.values(
            "id", "title", "status", "trust_state",
            "execution_contract_version", "boilerplate_code",
            "hidden_test_cases", "hidden_wrapper_code", "content"))

        candidates, others, near_miss = [], [], []
        for row in rows:
            clauses = clause_results(row, approved, executed)
            failed = [name for name, ok in clauses.items() if not ok]
            if not failed:
                candidates.append(row)
            else:
                others.append(row)
                if len(failed) == 1:
                    near_miss.append((row, failed[0]))

        classified = [census.classify(row) for row in candidates]
        summary = census.summarise(classified)

        # The reference class: everything that is NOT a candidate and does
        # declare a signature. Measured, not assumed.
        reference = {}
        for row in others:
            verdict = census.classify(row)["v3_requirement"]
            reference[verdict] = reference.get(verdict, 0) + 1

        projected = census.projection(reference, summary["total"])

        payload = {
            "questions_total": len(rows),
            "candidates": summary,
            "reference_class": reference,
            "projection": projected,
            "near_miss": [{"id": row["id"], "title": row["title"],
                           "fails": clause} for row, clause in near_miss],
        }

        if options["as_json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        write = self.stdout.write
        write(f"Questions in bank        {len(rows)}")
        write(f"Reseed candidates        {summary['total']}")
        write(f"  declaring a signature  {summary['declares_signature']}")
        write("")
        write("Stored contract")
        for key, count in sorted(summary["by_contract"].items()):
            write(f"  {key:<22} {count:>5}")
        write("Harness that would run")
        for key, count in sorted(summary["by_harness"].items()):
            write(f"  {key:<22} {count:>5}")
        write("")
        write("Signature shape")
        for letter, name, description in census.SHAPE_CLASSES:
            count = summary["by_shape"].get(letter, 0)
            if count:
                write(f"  {letter}  {name:<22} {count:>5}   {description}")
        write("")
        write("v3 requirement")
        for key in (census.V3_REQUIRED, census.V1_SUFFICIENT, census.UNKNOWN):
            write(f"  {key:<22} {summary['by_v3_requirement'].get(key, 0):>5}")

        write("")
        write(f"Reference class ({sum(reference.values())} non-candidates)")
        for key in (census.V3_REQUIRED, census.V1_SUFFICIENT, census.UNKNOWN):
            write(f"  {key:<22} {reference.get(key, 0):>5}")
        if projected:
            write("")
            write(f"Projected v3 need among the {summary['total']} candidates: "
                  f"{projected['estimate']} "
                  f"({projected['low']}–{projected['high']} at 95%), from a "
                  f"base rate of {projected['reference_rate']:.1%} over "
                  f"{projected['reference_population']} declared signatures.")
            write("This is a POPULATION estimate. No candidate is individually "
                  "classified by it.")

        if options["near_miss"] and near_miss:
            write("")
            write("Fails exactly one candidate clause")
            for row, clause in near_miss:
                write(f"  q{row['id']:<6} {(row['title'] or '')[:44]:<44} "
                      f"{clause}")

        if options["sample"]:
            write("")
            write(f"First {options['sample']} classified candidates")
            for record in classified[:options["sample"]]:
                write(f"  q{record['id']:<6} {record['contract']:<4} "
                      f"{record['shape']} {record['shape_name']:<22} "
                      f"{record['v3_requirement']}")
