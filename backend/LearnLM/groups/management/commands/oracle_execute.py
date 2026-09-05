"""
Oracle execution and reconciliation (M2 P2.7g-2).

Runs the canonical reference against a question's hidden cases, records what
happened, and reports whether the oracle and the stored answers agree.

WHAT THIS COMMAND CANNOT DO
───────────────────────────
It cannot write `expected_output`. Not behind a flag, not with `--force`: the
writer does not exist anywhere in this phase, so there is no argument that
reaches one. It also cannot set `Question.status`, `trust_state` or
`adaptive_eligible`.

That is not caution for its own sake. The chain that ends in a trusted answer
runs

    ... -> RECONCILIATION -> quality gate -> **human question review** ->
    ORACLE_VERIFIED

and the repository contains no human question-review mechanism. P2.7d-2
approves *references*; nothing approves a *question*. Generating authoritative
answers before that boundary exists would mean the system minted its own
grading truth with no human in the loop — which is the specific failure this
whole milestone is meant to prevent.

DEFAULTS
────────
Dry run. `--execute` is required to persist even provenance, and it needs
`--operator` naming an active staff user, so every recorded execution is
attributable to a person.

THE ALIAS (M2 P2.7)
───────────────────
`--alias` selects the DATABASE connection and nothing else. Judge0 is reached
over HTTP and is unaffected by it — the alias decides where the question is
read from and where provenance is written, not where code runs.

Every read and every write is routed through it, with no fallback: before this,
the command used default managers throughout, which on this deployment is the
read-only census connection, so `--execute` could only ever have failed.
"""

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from common import languages
from groups import oracle_pipeline
from groups.execution_contract import judge0_resource_limits
from groups.management.commands import _preimage_ops as ops
from groups.models import Question


class Command(BaseCommand):
    help = ("Execute canonical references against hidden tests and reconcile "
            "the results. Dry run unless --execute is given. Never writes "
            "expected_output.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, action="append",
                            dest="questions", metavar="ID",
                            help="Question id; repeatable. Omit for --all.")
        parser.add_argument("--all", action="store_true",
                            help="Every question that has a canonical reference.")
        parser.add_argument("--execute", action="store_true",
                            help="Persist provenance rows. Requires --operator.")
        parser.add_argument("--operator", metavar="USERNAME",
                            help="Active staff user accountable for the run.")
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable report on stdout.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection. `oracle` on production. Controls Django "
                 "database access only — Judge0 is reached over HTTP.")

    def handle(self, *args, **options):
        record = options["execute"]
        alias = options["alias"]
        operator = self._resolve_operator(options.get("operator"), record)
        questions = self._resolve_questions(options, alias)

        if record:
            identity = ops.describe_target(alias)
            if identity["is_production"]:
                ops.gate_writing_role(alias, allowed=ops.ALLOWED_ORACLE_ROLES)
                ops.gate_write_privilege(
                    alias, required=ops.ORACLE_EXECUTE_PROBE,
                    forbidden=ops.ORACLE_FORBIDDEN)
            else:
                ops.gate_write_privilege(alias,
                                         required=ops.ORACLE_EXECUTE_PROBE)
            ops.render_identity(self, identity, operator)
            self.stdout.write("")

        limits = judge0_resource_limits()
        if record and not limits:
            # An unbounded execution has no defined semantics: a case that
            # "passed" may simply have been given more time than a learner
            # gets. Recording it as evidence would overstate what was proven.
            raise CommandError(
                "refusing to record provenance: no Judge0 CPU/memory limits "
                "are configured, so executions are unbounded and not "
                "reproducible under a known resource policy. Set "
                "JUDGE0_CPU_TIME_LIMIT and JUDGE0_MEMORY_LIMIT first.")

        runner = self._build_runner()
        # WHICH interpreter produced this evidence (M2 P2.7h-12).
        #
        # `language` on the row says "python" and nothing more, so evidence
        # recorded under Judge0's Python 3.8.1 was indistinguishable from
        # evidence recorded under 3.11.2 — in a pipeline whose entire premise
        # is reproducibility. The id is a fact this process controls; the
        # version string is what Judge0 itself reports, looked up once per
        # run and omitted rather than guessed if the lookup fails.
        #
        # Additive keys on an existing JSONField: no migration, no digest
        # change (the artifact digest does not read `executor`), and no
        # existing row is touched — a database trigger makes every column but
        # `is_authoritative` immutable after insert.
        # Language-independent half. The interpreter identity is added per
        # question below, because it depends on which reference is canonical
        # for THAT question and nothing here can know it in advance.
        executor_base = {
            "limits": limits,
            "operator": operator.username if operator else None,
        }

        reports = []
        for question in questions:
            report = oracle_pipeline.run_question(
                question, runner, record=record,
                executor=self._executor_for(question, executor_base),
                using=alias)
            reports.append(report)
            if not options["json"]:
                self._render(report)

        if options["json"]:
            self.stdout.write(json.dumps([r.as_dict() for r in reports], indent=2))
        else:
            self._render_summary(reports, record)

    # ── Inputs ────────────────────────────────────────────────────────────

    def _resolve_operator(self, username, record):
        if not record:
            return None
        if not username:
            raise CommandError("--execute requires --operator")
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"no such user: {username}")
        if not (user.is_staff and user.is_active):
            raise CommandError(f"{username} is not an active staff user")
        return user

    def _resolve_questions(self, options, alias):
        ids, want_all = options.get("questions"), options["all"]
        if ids and want_all:
            raise CommandError("--question and --all are mutually exclusive")
        if not ids and not want_all:
            raise CommandError("give --question ID (repeatable) or --all")

        if want_all:
            return list(Question.objects.using(alias).filter(
                reference_solutions__is_active=True).distinct().order_by("pk"))

        questions = list(
            Question.objects.using(alias).filter(pk__in=ids).order_by("pk"))
        missing = set(ids) - {q.pk for q in questions}
        if missing:
            raise CommandError(f"no such question(s): {sorted(missing)}")
        return questions

    #: Judge0 runtime names already looked up this run, keyed by language id.
    _runtime_cache = None

    def _executor_for(self, question, base):
        """
        Provenance for ONE question, stamped with the language actually run.

        Previously `judge0_language_id` was `judge0_id("python")` for every
        row, so a Java or C++ reference would have executed correctly — the
        runner dispatches on `reference.language` — and then recorded evidence
        claiming a Python interpreter produced it. Nothing downstream could
        have detected the lie, because the artifact digest does not read
        `executor`.

        The reference is resolved here as well as inside `run_question`. That
        is a second read of the same rows, deliberately accepted: the
        alternative was threading a callable through the pipeline signature to
        save one query, and provenance that is obviously correct is worth more
        than that. When no single canonical reference exists the language keys
        are omitted entirely — `run_question` reports the blocker and records
        nothing, so an absent key can never be mistaken for a claim.
        """
        from groups.oracle import canonical_reference

        executor = dict(base)
        reference = canonical_reference(question)
        if reference is None:
            return executor

        language_id = languages.judge0_id(reference.language)
        executor["reference_language"] = reference.language
        executor["judge0_language_id"] = language_id
        runtime = self._runtime_description(language_id)
        if runtime:
            executor["runtime"] = runtime
        return executor

    def _runtime_description(self, language_id):
        """
        What Judge0 calls the language with `language_id`, or None.

        Asked rather than assumed: a hardcoded "Python 3.11.2" beside a
        configurable id would be a claim that drifts the moment the id
        changes. A failed lookup records nothing instead of a guess — an
        unknown runtime is a fact, an invented one is not.

        Memoised per id: a batch of questions sharing a reference language
        must not re-ask Judge0 once per question, which matters more now that
        the id varies — and matters most when Judge0 is rate-limiting.
        """
        import os

        import requests

        if self._runtime_cache is None:
            self._runtime_cache = {}
        if language_id in self._runtime_cache:
            return self._runtime_cache[language_id]

        host = os.environ.get("JUDGE0_API_HOST")
        key = os.environ.get("JUDGE0_API_KEY")
        base = os.environ.get("JUDGE0_URL", f"https://{host}" if host else "")
        if not (host and key and base and language_id):
            return None

        name = None
        try:
            response = requests.get(
                f"{base}/languages/{language_id}",
                headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": host},
                timeout=10)
            response.raise_for_status()
            name = response.json().get("name")
        except Exception:  # noqa: BLE001 — provenance must not block evidence
            name = None

        # A failed lookup is cached too. Judge0 is the service most likely to
        # be rate-limiting when this runs, and re-asking once per question
        # after it has already refused makes that worse, not better.
        self._runtime_cache[language_id] = name
        return name

    def _build_runner(self):
        # Resolved per call rather than bound once, so the established
        # `monkeypatch(coding_views._run_on_judge0)` seam keeps working.
        from groups import coding_views

        def runner(source, language, stdin=""):
            return coding_views._run_on_judge0(source, language, stdin)

        return runner

    # ── Output ────────────────────────────────────────────────────────────

    def _render(self, report):
        head = f"Question {report.question_id}"
        if report.reference_id:
            head += (f"  reference {report.reference_id} "
                     f"[{report.language}] {report.reference_source_hash[:12]}"
                     f"  contract {report.execution_contract_version}")
        self.stdout.write(self.style.MIGRATE_HEADING(head))

        for blocker in report.reference_blockers:
            self.stdout.write(f"  reference: {blocker}")
        for blocker in report.question_blockers:
            self.stdout.write(f"  question:  {blocker}")

        if not report.eligible:
            self.stdout.write(self.style.ERROR("  NOT ELIGIBLE — nothing executed"))
            self.stdout.write("")
            return

        for case in report.cases:
            if case.outcome != oracle_pipeline.CASE_OK:
                self.stdout.write(self.style.ERROR(
                    f"  case {case.case_index + 1}: {case.outcome} — {case.detail}"))
            elif case.reconciliation == oracle_pipeline.RECON_CONFLICT:
                self.stdout.write(self.style.WARNING(
                    f"  case {case.case_index + 1}: CONFLICT "
                    f"(stored {case.existing_output_digest[:12]} != "
                    f"oracle {case.output_digest[:12]})"))
            elif case.reconciliation == oracle_pipeline.RECON_ABSENT:
                self.stdout.write(
                    f"  case {case.case_index + 1}: ABSENT — oracle produced "
                    f"{case.output_digest[:12]}, nothing stored")
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"  case {case.case_index + 1}: AGREEMENT"))

        self.stdout.write(
            f"  {report.agreements} agree, {report.conflicts} conflict, "
            f"{report.absent} absent, {report.failed_cases} unsettled")
        self.stdout.write("")

    def _render_summary(self, reports, record):
        ready = sum(1 for r in reports if r.ready_for_quality_gate)
        conflicts = sum(r.conflicts for r in reports)
        absent = sum(r.absent for r in reports)

        self.stdout.write(self.style.MIGRATE_HEADING("Summary"))
        self.stdout.write(f"  questions examined:        {len(reports)}")
        self.stdout.write(f"  ready for quality gate:    {ready}")
        self.stdout.write(f"  conflicting cases:         {conflicts}")
        self.stdout.write(f"  cases with no stored answer: {absent}")

        if not record:
            self.stdout.write(self.style.WARNING(
                "  DRY RUN — no provenance recorded"))

        if conflicts:
            self.stdout.write(self.style.WARNING(
                "\n  A CONFLICT means the reference and the stored answer "
                "disagree. This command does not decide which is right, and "
                "deliberately provides no flag to overwrite either one."))

        self.stdout.write(
            "\n  'Ready for quality gate' is NOT readiness for ORACLE_VERIFIED. "
            "\n  Still required: the P2.7h-1 hidden-test quality gate, and a "
            "human\n  question-approval step that does not yet exist in this "
            "repository.")
