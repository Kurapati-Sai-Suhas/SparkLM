"""
Run the P2.7h-1 hidden-test quality gate against one question (M2 P2.7h-2).

READ-ONLY. It executes mutants on Judge0 and writes a JSON report to a file;
it touches no row in the database, and a structural test asserts that rather
than trusting this sentence.

    python manage.py quality_gate --question 3309 --alias oracle \\
        --spec quality/q3309.json --report-out reports/q3309-quality.json

── The gap this fills ──────────────────────────────────────────────────────

`hidden_test_quality.evaluate_suite` has existed since P2.7h-1 and
`question_review` / `question_approve` both require a `--quality-report` in
`QualityOutcome` shape — but NOTHING in the repository produced one. The gate
was a library with no runner, so the approval path could not be walked even
with perfect oracle evidence. This command is that runner and nothing more.

── Why a spec file, and why a human writes it ──────────────────────────────

The gate cannot be fully derived from the question. Two of its inputs are
judgements:

  * the INPUT CONTRACT — "negative values" is a real coverage gap for a problem
    over integers and nonsense for one over string lengths, so every generic
    category is gated on a property of THIS problem's inputs;
  * the TIER-1 MUTANTS — realistic algorithmic misconceptions, written by a
    human for this problem. Generating them automatically would measure whether
    the suite catches the mistakes a machine thought of, which is not the
    question being asked.

So the spec is an operator artifact, supplied by file (never inline: mutant
sources are wrong answers to a graded problem, and arguments end up in shell
history). The command reads it, runs the gate, and reports.

── What the report is, and is not ──────────────────────────────────────────

A PASS here is `QUALITY_GATE_PASS` and nothing else. It is not oracle evidence,
not approval, and not ORACLE_VERIFIED — those are separate states with separate
commands, and this report is one input to the first of them.
"""

import json
import pathlib

from django.core.management.base import BaseCommand, CommandError

from groups import execution_contract
from groups import hidden_test_quality as quality
from groups import oracle as oracle_module
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import _question_trust as trust
from groups.models import Question
from groups.services import GradingService

#: Report shape consumed by `_question_trust.load_quality_outcome`.
REPORT_KEYS = ("tier1_kill_rate", "tier2_kill_rate", "blockers",
               "mutant_identifiers")


class Command(BaseCommand):
    help = ("Run the hidden-test quality gate for one question and write the "
            "JSON report the approval path consumes. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument(
            "--spec", required=True, metavar="PATH",
            help="JSON holding this question's input contract and mutants. "
                 "A file: mutant sources are wrong answers to a graded "
                 "problem and must not enter shell history.")
        parser.add_argument(
            "--report-out", metavar="PATH",
            help="Where to write the QualityOutcome JSON. Omit to print the "
                 "verdict without saving a report.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection for the READ. This command never "
                 "writes; the alias only decides where the question is read "
                 "from.")
        parser.add_argument(
            "--structural-only", action="store_true",
            help="Skip mutant execution and report only the checks that need "
                 "no runner: contract, structure, duplicates, categories, "
                 "floor. Never produces a PASS.")

    def handle(self, *args, **options):
        alias = options["alias"]
        operator = trust.resolve_operator(options["operator"])
        identity = ops.describe_target(alias)

        question = Question.objects.using(alias).filter(
            pk=options["question"]).first()
        if question is None:
            raise CommandError(f"no such question: {options['question']}")

        contract, mutants, substitutions = self._read_spec(
            options["spec"], question.pk)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "QUALITY GATE" + ("  (STRUCTURAL ONLY)"
                              if options["structural_only"] else "")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        report = quality.evaluate_suite(
            question.hidden_test_cases or [],
            [] if options["structural_only"] else mutants,
            self._runner(options["structural_only"]),
            contract,
            plan=GradingService.quality_execution_plan(question),
            substitutions=substitutions)

        self._render(question, report, options["structural_only"])

        if options["structural_only"]:
            self.stdout.write(self.style.WARNING(
                "STRUCTURAL ONLY — no mutant was executed, so this run cannot "
                "produce a PASS and no report was written."))
            return

        if options["report_out"]:
            self._write_report(options["report_out"], report, question)

    # ── inputs ────────────────────────────────────────────────────────

    def _read_spec(self, path, question_id):
        location = pathlib.Path(path)
        if not location.is_file():
            raise CommandError(f"no such spec: {path}")
        try:
            parsed = json.loads(location.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON: {exc}")
        if not isinstance(parsed, dict):
            raise CommandError(f"{path} must hold an object")
        if parsed.get("question") != question_id:
            raise CommandError(
                f"{path} names question {parsed.get('question')!r} but "
                f"--question is {question_id}; refusing to measure one "
                f"question's suite against another's mutants")

        try:
            contract = quality.InputContract(**parsed.get("input_contract", {}))
        except TypeError as exc:
            raise CommandError(f"{path}: bad input_contract ({exc})")

        raw_mutants = parsed.get("mutants")
        if not isinstance(raw_mutants, list) or not raw_mutants:
            raise CommandError(
                f"{path} has no mutants. A gate with nothing to kill measures "
                f"nothing; write at least one Tier-1 misconception for this "
                f"problem.")
        try:
            mutants = [quality.Mutant(**entry) for entry in raw_mutants]
        except (TypeError, ValueError) as exc:
            raise CommandError(f"{path}: bad mutant ({exc})")

        try:
            substitutions = tuple(
                quality.CategorySubstitution(**entry)
                for entry in parsed.get("substitutions", []))
        except (TypeError, ValueError) as exc:
            raise CommandError(f"{path}: bad substitution ({exc})")

        return contract, mutants, substitutions

    def _runner(self, structural_only):
        if structural_only:
            def refuse(source, language, stdin=""):
                raise AssertionError(
                    "structural-only run attempted to execute a mutant")
            return refuse

        from groups import coding_views

        def runner(source, language, stdin=""):
            return coding_views._run_on_judge0(source, language, stdin)

        return runner

    def _provenance(self, question, report):
        """What this verdict was measured against, recomputed from live state."""
        from groups import pre_image, provenance as prov

        reference = oracle_module.canonical_reference(question)
        suite = question.hidden_test_cases or []
        return {
            "question_id": question.pk,
            "question_state_digest": pre_image.live_digest(question),
            "execution_contract_version":
                execution_contract.contract_version(question),
            "reference_id": reference.pk if reference else None,
            "reference_source_hash": reference.source_hash if reference else None,
            "reference_language": reference.language if reference else None,
            "case_count": len(suite),
            "case_identities": [prov.case_identity(case.get("stdin", ""))
                                for case in suite],
            "categories": sorted({str(case.get("category", "")).strip()
                                  for case in suite if case.get("category")}),
            "verdict": report.verdict,
            "results": [{"identifier": r.identifier, "tier": r.tier,
                         "outcome": r.outcome,
                         "killed_by_case": r.killed_by_case}
                        for r in sorted(report.results,
                                        key=lambda r: (r.tier, r.identifier))],
        }

    # ── output ────────────────────────────────────────────────────────

    def _render(self, question, report, structural_only):
        write = self.stdout.write
        write(f"  question            {question.pk} — {question.title[:44].strip()}")
        write(f"  hidden tests        {report.total_cases}")
        write(f"  malformed           {report.malformed_count}")
        write(f"  duplicates          {report.duplicate_count}")
        write(f"  missing categories  "
              f"{', '.join(report.missing_required_categories) or 'none'}")

        reference = oracle_module.canonical_reference(question)
        write(f"  canonical reference {('#' + str(reference.pk)) if reference else 'NONE'}")

        if not structural_only:
            write(f"  tier 1 kill rate    {report.tier1_kill_rate}")
            write(f"  tier 2 kill rate    "
                  f"{report.tier2_effective_kill_rate}"
                  f"   (effective: equivalents excluded)")
            for result in report.results:
                write(f"    [{result.tier}] {result.identifier:<28} "
                      f"{result.outcome}"
                      + (f"  case {result.killed_by_case}"
                         if result.killed_by_case else "")
                      + (f"  {result.detail}" if result.detail else ""))

        write("")
        for problem in report.contract_problems:
            write(f"    contract: {problem}")
        for blocker in report.blockers:
            write(self.style.ERROR(f"    BLOCKER: {blocker}"))

        write("")
        verdict = report.verdict
        style = self.style.SUCCESS if verdict == quality.PASS else self.style.ERROR
        write(style(f"  QUALITY_GATE = {verdict}"))
        write("")
        write("  This verdict is QUALITY_GATE_PASS/FAIL only. It is not oracle")
        write("  evidence, not approval, and not ORACLE_VERIFIED — each is a")
        write("  separate state with its own command.")

    def _write_report(self, path, report, question=None):
        """
        The report, plus the provenance that says WHAT it was measured against.

        `QualityOutcome.from_mapping` reads four keys and ignores the rest, so
        the extra block travels with the evidence without changing what the
        approval path consumes. Without it a report is four numbers with no
        statement of which suite, which reference or which revision produced
        them — and a report that cannot be tied to a state is not evidence.
        """
        outcome = {
            "tier1_kill_rate": report.tier1_kill_rate,
            # `QualityOutcome` calls it tier2_kill_rate; the gate calls it
            # tier2_EFFECTIVE_kill_rate because documented equivalents are
            # excluded from the denominator. Same number, mapped once here
            # rather than renamed in either module.
            "tier2_kill_rate": report.tier2_effective_kill_rate,
            "blockers": list(report.blockers),
            "mutant_identifiers": sorted(r.identifier for r in report.results),
        }
        if question is not None:
            outcome["provenance"] = self._provenance(question, report)
        location = pathlib.Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(json.dumps(outcome, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        self.stdout.write(f"  report written      {location}")
        self.stdout.write(
            "  Supply it to question_review / question_approve with "
            "--quality-report.")
