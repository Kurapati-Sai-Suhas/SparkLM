"""
Production question-bank census (M2 P2.7e).

SELECT-ONLY. No write path exists in this command or in `groups.census`, and a
structural test asserts it — but the load-bearing protection is
`census_gates`, which refuses to run at all unless the database role holds no
write privilege.

    # production, with a read-only role
    python manage.py question_bank_census --alias default --json

    # synthetic/test database; the report is stamped non-production
    python manage.py question_bank_census --allow-non-production

Every gate aborts rather than degrading. A census that reports development
numbers under a production heading converts "we don't know" into a false "we
do", which is worse than having no census at all.
"""

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from groups import census, census_gates


class Command(BaseCommand):
    help = ("Read-only census of the question bank, its trust states, hidden "
            "tests, references, provenance and reseed blast radius.")

    def add_arguments(self, parser):
        parser.add_argument("--alias", default="default",
                            help="Database alias to census.")
        parser.add_argument(
            "--allow-non-production", action="store_true",
            help="Permit a loopback/private database. The report is stamped "
                 "is_production=false; it does not relax any other gate.")
        parser.add_argument(
            "--allow-write-role", action="store_true",
            help="Proceed even if the role can write. Intended ONLY for the "
                 "test database, which is created and destroyed per run.")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--out", metavar="PATH",
                            help="Write the JSON report to a file.")

    def handle(self, *args, **options):
        alias = options["alias"]

        try:
            identity = census_gates.run_all(
                alias,
                allow_non_production=options["allow_non_production"],
                require_read_only=not options["allow_write_role"])
        except census_gates.GateFailure as failure:
            raise CommandError(
                f"CONNECTION GATE FAILED — no census was run and no counts "
                f"are reported.\n\n{failure}")

        report = self._collect(identity)

        payload = report.as_dict()
        if options["out"]:
            import pathlib
            pathlib.Path(options["out"]).write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                encoding="utf-8")

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True,
                                         default=str))
        else:
            self._render(report)

    # ── collection ────────────────────────────────────────────────────

    def _collect(self, identity):
        report = census.CensusReport(
            generated_at=timezone.now().isoformat(),
            database_identity=identity,
            schema_state={"latest_migration": identity.get("latest_migration"),
                          "missing_migrations": identity.get(
                              "missing_migrations", [])},
        )

        def timed(name, function):
            started = time.perf_counter()
            result = function()
            report.timings_ms[name] = round(
                (time.perf_counter() - started) * 1000, 2)
            return result

        report.question_counts = timed("question_counts",
                                       census.question_counts)
        report.contradiction_counts = timed("contradictions",
                                            census.contradiction_counts)

        hidden, duplicates, _classes, malformed = timed(
            "hidden_tests", census.hidden_test_census)
        report.hidden_test_counts = hidden
        report.duplicate_counts = {
            "questions_with_normalized_duplicate_inputs":
                hidden.get("questions_with_normalized_duplicates", 0),
            "duplicate_cases_total": duplicates,
            "definition": "NORMALIZED stdin (the stricter of the two "
                          "definitions in the repository)",
        }
        report.gradability_counts = {
            "gradable": hidden.get("gradable", 0),
            "not_gradable": hidden.get("not_gradable", 0),
            "meets_minimum_count": hidden.get("meets_minimum_count", 0),
            "below_minimum_count": hidden.get("below_minimum_count", 0),
            "malformed_examples": malformed,
        }

        report.language_counts = timed("languages", census.language_census)
        report.reference_counts = timed("references", census.reference_counts)
        report.provenance_counts = timed("provenance", census.provenance_counts)
        report.approval_counts = timed("approvals", census.approval_counts)
        report.grading_data_counts = timed("grading_data",
                                           census.grading_data_counts)
        report.adaptive_eligibility_counts = {
            "submissions_eligible":
                report.grading_data_counts.get("adaptive_eligible_true", 0),
            "submissions_ineligible":
                report.grading_data_counts.get("adaptive_eligible_false", 0),
            "questions_adaptively_eligible":
                report.question_counts.get("matrix", {}).get(
                    "PUBLISHED+ORACLE_VERIFIED", 0),
        }

        report.reseed_candidates = timed("blast_radius",
                                         census.reseed_blast_radius)
        report.safe_to_leave_untouched = report.reseed_candidates.get(
            census.SAFE, 0)

        self._assess(report)
        report.report_hash = census.report_hash(report)
        return report

    def _assess(self, report):
        contradictions = report.contradiction_counts
        if contradictions.get("draft_oracle_verified"):
            report.blockers.append(
                f"{contradictions['draft_oracle_verified']} question(s) are "
                f"DRAFT + ORACLE_VERIFIED. Migration 0042's CHECK would FAIL "
                f"to apply. `trust_state` has no writer in this codebase, so "
                f"these were written by an unaudited path — investigate before "
                f"deciding anything.")

        if report.provenance_counts.get("oracle_execution_rows", 0) == 0:
            report.blockers.append(
                "zero OracleExecution rows: no expected_output anywhere has "
                "provenance. Every stored answer key is LEGACY/UNPROVENANCED.")

        if report.reference_counts.get("total", 0) == 0:
            report.blockers.append(
                "zero ReferenceSolution rows: nothing can act as an oracle, so "
                "no question can be verified.")

        if report.approval_counts.get("total", 0) == 0:
            report.blockers.append(
                "zero QuestionApproval rows: no question has been human-"
                "approved, which P2.7g-3 makes a precondition of trust.")

        report.warnings.append(
            "DUPLICATE DEFINITION DIVERGENCE: hidden_tests.validate_suite "
            "compares RAW stdin; reseed_questions and the P2.7h-1 quality gate "
            "compare NORMALIZED. This census reports the NORMALIZED count "
            "(stricter). Both definitions remain in the repository, unchanged "
            "by this phase.")

        if not report.database_identity.get("is_production"):
            report.warnings.append(
                "NON-PRODUCTION DATABASE — these counts describe a "
                "development or synthetic database and must never be quoted "
                "as production figures.")

    # ── rendering ─────────────────────────────────────────────────────

    def _render(self, report):
        write, style = self.stdout.write, self.style
        identity = report.database_identity

        heading = ("QUESTION-BANK CENSUS (M2 P2.7e) — READ-ONLY"
                   if identity.get("is_production")
                   else "QUESTION-BANK CENSUS — NON-PRODUCTION DATABASE")
        write(style.MIGRATE_HEADING(heading))
        if not identity.get("is_production"):
            write(style.ERROR(
                "  These are NOT production numbers. Do not quote them as "
                "such."))
        write("")

        write(style.MIGRATE_HEADING("Database identity"))
        write(f"  alias             {identity.get('alias')}")
        write(f"  database          {identity.get('database')}")
        write(f"  role              {identity.get('role')}")
        write(f"  host class        {identity.get('host_class')}")
        write(f"  server            {identity.get('server_version')}")
        write(f"  role is read-only {identity.get('read_only')}")
        write(f"  latest migration  {identity.get('latest_migration')}")
        write("")

        for title, data in (
                ("Questions", report.question_counts),
                ("Contradictions", report.contradiction_counts),
                ("Hidden tests", report.hidden_test_counts),
                ("Duplicates", report.duplicate_counts),
                ("Gradability", {k: v for k, v in
                                 report.gradability_counts.items()
                                 if k != "malformed_examples"}),
                ("Languages", report.language_counts),
                ("Reference solutions", report.reference_counts),
                ("Output provenance", report.provenance_counts),
                ("Question approvals", report.approval_counts),
                ("Grading data", report.grading_data_counts),
                ("Reseed blast radius", report.reseed_candidates)):
            write(style.MIGRATE_HEADING(title))
            for key, value in sorted(data.items()):
                write(f"  {key:48} {value}")
            write("")

        if report.blockers:
            write(style.ERROR("BLOCKERS"))
            for blocker in report.blockers:
                write(style.ERROR(f"  • {blocker}"))
            write("")
        if report.warnings:
            write(style.WARNING("WARNINGS"))
            for warning in report.warnings:
                write(style.WARNING(f"  • {warning}"))
            write("")

        write(style.MIGRATE_HEADING("Reproducibility"))
        write(f"  report hash  {report.report_hash}")
        write(f"  timings (ms) {report.timings_ms}")
