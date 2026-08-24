"""
Read-only blast-radius report for the v1 execution contract (M2 P2.7 §10).

    python manage.py contract_impact --alias default --json --out report.json

SELECT-ONLY, and gated by the same `census_gates` the production census uses:
the role must hold no INSERT/UPDATE/DELETE/DDL privilege on grading truth, or
this refuses to run. The gate is the protection; this command being read-only
in Python is only the second line.

It answers one question — how many production questions are graded by calling
the learner's function with an argument its own signature contradicts — and
answers it by reading, never by executing. No Judge0 call is made, no oracle is
run, and nothing is written, proposed, or repaired.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from groups import census_gates, contract_impact
from groups.models import Question

#: Columns the analysis needs. Named explicitly so the query cannot drag the
#: whole row — `hidden_test_cases` is large and this runs over the whole bank.
FIELDS = ("id", "status", "trust_state", "execution_contract_version",
          "boilerplate_code", "hidden_test_cases", "hidden_wrapper_code")


class Command(BaseCommand):
    help = ("Read-only report of how many questions v1 calls with an argument "
            "their declared signature contradicts. Executes nothing.")

    def add_arguments(self, parser):
        parser.add_argument("--alias", default="default")
        parser.add_argument(
            "--allow-non-production", action="store_true",
            help="Permit a loopback/private database. The report is stamped "
                 "is_production=false.")
        parser.add_argument(
            "--allow-write-role", action="store_true",
            help="Proceed even if the role can write. For the ephemeral test "
                 "database only.")
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--out", metavar="PATH")
        parser.add_argument(
            "--sample", type=int, default=0, metavar="N",
            help="Also list N affected question ids per reason, lowest first. "
                 "Ids only — no content is printed.")
        parser.add_argument(
            "--stratified", type=int, default=0, metavar="N",
            help="Draw a reproducible stratified sample of N question ids for "
                 "manual semantic review. Selects ids; executes nothing.")
        parser.add_argument(
            "--seed", type=int, default=20250815, metavar="INT",
            help="Seed for --stratified. The same seed selects the same "
                 "questions, so a second reviewer audits the same sample.")
        parser.add_argument(
            "--exclude", type=int, nargs="*", default=[], metavar="ID",
            help="Question ids to keep out of the stratified sample.")

    def handle(self, *args, **options):
        try:
            identity = census_gates.run_all(
                options["alias"],
                allow_non_production=options["allow_non_production"],
                require_read_only=not options["allow_write_role"])
        except census_gates.GateFailure as failure:
            raise CommandError(
                f"CONNECTION GATE FAILED — nothing was read and no counts are "
                f"reported.\n\n{failure}")

        rows = list(Question.objects.using(options["alias"])
                    .values(*FIELDS).order_by("id"))
        classifications = [contract_impact.classify(row) for row in rows]

        payload = {
            "database_identity": identity,
            "summary": contract_impact.summarise(classifications),
            "canonical": self._canonical_segments(rows),
        }
        if options["sample"]:
            payload["affected_ids"] = self._affected_ids(
                classifications, options["sample"])

        if options["stratified"]:
            payload["stratified_sample"] = {
                "seed": options["seed"],
                "excluded": sorted(options["exclude"]),
                "questions": contract_impact.stratified_sample(
                    classifications, options["stratified"],
                    options["seed"], exclude=options["exclude"]),
            }

        if options["out"]:
            import pathlib
            pathlib.Path(options["out"]).write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                encoding="utf-8")

        if options["json"]:
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True,
                                         default=str))
        else:
            self._render(payload)

    def _canonical_segments(self, rows):
        """
        Verdicts under canonical execution, reported per segment.

        Segmented because one bank-wide table hides the thing that matters: the
        1,141 questions with no test cases cannot be miscalled at all, and
        folding them in with the graded ones makes both numbers meaningless.
        """
        def has_python(row):
            starter = row.get("boilerplate_code") or {}
            source = starter.get("python") if isinstance(starter, dict) else None
            return source if isinstance(source, str) and source.strip() else ""

        def cases(row):
            found = row.get("hidden_test_cases")
            return found if isinstance(found, list) else []

        segments = {
            "graded": [r for r in rows if cases(r)],
            "no_test_cases": [r for r in rows if not cases(r)],
            "variadic_starters": [
                r for r in rows
                if contract_impact.accepts_variable_arity(has_python(r))],
            "fixed_arity_starters": [
                r for r in rows
                if has_python(r)
                and not contract_impact.accepts_variable_arity(has_python(r))],
            "declares_text_input": [
                r for r in rows if self._declares_text(has_python(r))],
            "python_cased_boolean_output": [
                r for r in rows
                if any(isinstance(c, dict)
                       and isinstance(c.get("expected_output"), str)
                       and c["expected_output"].strip() in ("True", "False")
                       for c in cases(r))],
        }
        return {name: contract_impact.summarise_verdicts(members)
                | {"size": len(members)}
                for name, members in segments.items()}

    @staticmethod
    def _declares_text(source):
        signature = contract_impact.declared_signature(source) if source else None
        if signature is None:
            return False
        _name, parameters = signature
        return any(contract_impact.declares_text(a) for _n, a in parameters)

    def _affected_ids(self, classifications, limit):
        """Ids per reason, lowest first — reproducible, and content-free."""
        listing = {}
        for reason in contract_impact.REASON_ORDER:
            matched = [f["id"] for f in classifications if reason in f["reasons"]]
            if matched:
                listing[reason] = matched[:limit]
        return listing

    def _render(self, payload):
        write, style = self.stdout.write, self.style
        identity = payload["database_identity"]
        summary = payload["summary"]

        write(style.MIGRATE_HEADING("EXECUTION-CONTRACT IMPACT (READ-ONLY)"))
        write(f"  database        {identity.get('database')}")
        write(f"  role            {identity.get('role')}")
        write(f"  host class      {identity.get('host_class')}")
        write(f"  read-only role  {identity.get('read_only')}")
        write(f"  production      {identity.get('is_production')}")
        write("")
        graded = summary["with_test_cases"]
        write(f"  questions        {summary['total_questions']}")
        write(f"  analysable       {summary['analysable']}")
        write(f"  not analysable   {summary['not_analysable']}")
        write(f"  WITH test cases  {graded}   <- the denominator; the rest are "
              f"never executed")
        write(f"  without tests    {summary['without_test_cases']}")
        write("")

        for reason in contract_impact.REASON_ORDER:
            count = summary["reason_counts"].get(reason, 0)
            share = f"{100 * count / graded:5.1f}%" if graded else "    —"
            line = f"  {reason:<24} {count:>6}  {share}"
            if reason == "text_retyped" and count:
                write(style.ERROR(line))
            elif reason in ("type_unstable", "ambiguous_entry_point") and count:
                write(style.WARNING(line))
            else:
                write(line)

        write("")
        write(style.MIGRATE_HEADING("UNDER CANONICAL EXECUTION (v3 adapter)"))
        header = "  ".join(f"{v[:13]:>13}" for v in contract_impact.VERDICT_ORDER)
        write(f"  {'segment':<26} {'n':>5}  {header}")
        for name, block in payload["canonical"].items():
            counts = block["counts"]
            cells = "  ".join(
                f"{counts.get(v, 0):>13}" for v in contract_impact.VERDICT_ORDER)
            write(f"  {name:<26} {block['size']:>5}  {cells}")
        write("")
        write("  SAFE means the adapter can invoke the question as its signature")
        write("  declares. It does NOT mean the stored answers are correct.")

        sample = payload.get("stratified_sample")
        if sample:
            write("")
            write(style.MIGRATE_HEADING(
                f"STRATIFIED SAMPLE (seed {sample['seed']}, "
                f"excluded {sample['excluded'] or 'none'})"))
            for entry in sample["questions"]:
                write(f"  q{entry['id']:<8} {entry['stratum']}")
            write("  ids only — nothing was executed and no content was read "
                  "into this report")

        write("")
        write(style.ERROR(
            f"{summary['never_callable']} questions are called with the wrong "
            f"NUMBER of arguments.\nThe call raises TypeError before the "
            f"learner's code runs, so every submission\nfails — correct ones "
            f"included."))
        write("")
        write(style.WARNING(
            f"{summary['provably_miscalled']} questions declare a text "
            f"parameter and are handed a non-text value.\nTheir stored expected "
            f"outputs answer a different question from the one asked.\n\n"
            f"{summary['grader_crashes']} questions store a non-string stdin or "
            f"expected_output; GradingService\ncalls .strip() on both, so the "
            f"submission raises inside the grader.\n\n"
            f"These are COUNTS, not a repair plan and not a reseed "
            f"authorisation. Nothing\nwas executed and nothing was changed."))
