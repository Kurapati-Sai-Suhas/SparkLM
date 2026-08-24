"""
Add hidden test cases and coverage labels to one question (M2 P2.7h-3).

    python manage.py expand_hidden_tests --alias hiddentest \\
        --batch p27-pilot-1 --question 3309 \\
        --plan remediation/q3309_suite_expansion.json \\
        --expect-digest <sha256> \\
        --reason "reach the coverage floor before recording oracle evidence" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `hidden_test_cases` — of one
question, and records an append-only `SUITE_EXPANSION` action.

── A fourth class over one column, and why ─────────────────────────────────

    HIDDEN_TEST_REPAIR      the stored answer's FORM changed
    INPUT_REPAIR            the question being asked changed
    EXPECTED_OUTPUT_REPAIR  the answer changed
    SUITE_EXPANSION         there are now MORE questions than there were

The last one is not a repair. It is the only class that invalidates evidence
rather than correcting data: oracle executions are scoped to case digests, so a
suite that grew has cases no execution covers, and an approval built on the old
suite would be an approval of a different artifact. Recording it under any of
the other three would hide exactly the fact a later reviewer most needs.

── What it may and may not do ──────────────────────────────────────────────

MAY   append new cases; add a `category` label to an existing case
NEVER change an existing stdin, expected_output or explanation
NEVER remove, reorder or replace an existing case
NEVER touch any other field of the question

Existing cases are carried over BY VALUE from what is stored — the plan file
supplies only labels and additions — so preservation is structural rather than
checked, and the checks below are a second opinion on top of it.

── Every added case must be executable ─────────────────────────────────────

A case the adapter cannot bind is not coverage; it is a case that will refuse at
grading time. Each addition is bound through `execution_adapter` under the
question's declared contract before anything is written, and a refusal aborts.
"""

import copy
import json
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import execution_adapter, execution_contract, hidden_tests
from groups import hidden_test_quality as quality
from groups import pre_image, provenance
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "hidden_test_cases"

#: The key this action class may ADD to an existing case, and nothing else.
LABEL_KEY = "category"

#: Keys of an existing case that must survive byte-for-byte.
PRESERVED_KEYS = ("stdin", "expected_output", "explanation")


class Command(BaseCommand):
    help = ("Append hidden test cases and add coverage labels. Dry-run by "
            "default. Cannot change any existing input, answer or explanation.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--plan", required=True, metavar="PATH",
            help="JSON holding `labels` for existing cases and `additions`. A "
                 "file: new cases carry answers to a graded problem.")
        parser.add_argument(
            "--expect-digest", metavar="SHA256",
            help="The question's current state digest. Given, the command "
                 "refuses unless live state matches it.")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="expand a hidden-test suite",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_HIDDEN_TEST_ROLES,
            required_privileges=ops.HIDDEN_TEST_REPAIR_PROBE,
            forbidden_privileges=ops.HIDDEN_TEST_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "SUITE EXPANSION" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {options['batch']}")

        question = Question.objects.using(alias).filter(
            pk=options["question"]).first()
        if question is None:
            raise ops.GateFailure(f"no such question: {options['question']}")

        record = pre_image.require_pre_image(batch, question)

        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)
        expected = options.get("expect_digest")
        if expected and expected != before_digest:
            raise ops.GateFailure(
                f"the question is at {before_digest} but the plan was written "
                f"against {expected}. Refusing: it has moved since.")

        current = before_state[REPAIRABLE_FIELD] or []
        labels, additions = self._read_plan(options["plan"], question.pk,
                                            len(current))

        proposed = self._derive(current, labels, additions)
        self._check_invariants(current, proposed, len(additions))
        self._check_executable(question, proposed, len(current))

        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: proposed}))

        self._render_plan(batch, question, record, before_digest, projected,
                          current, proposed)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, proposed,
                                   before_state, len(additions),
                                   options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Suite expanded for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(self.style.WARNING(
            "Any oracle evidence recorded against the OLD suite is now "
            "incomplete: the new cases have no executions. Run the oracle "
            "against this suite before reviewing the artifact."))

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, proposed, before_state,
               added, reason):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            setattr(locked, REPAIRABLE_FIELD, proposed)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a suite expansion; the write "
                        f"has been reverted")

            # Re-checked against what LANDED, not what was proposed.
            self._check_invariants(before_state[REPAIRABLE_FIELD] or [],
                                   after_state[REPAIRABLE_FIELD], added)

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_SUITE_EXPANSION,
                operator, detail=reason)
        return after_digest

    # ── the plan ──────────────────────────────────────────────────────

    def _read_plan(self, path, question_id, case_count):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such plan: {path}")
        try:
            parsed = json.loads(location.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid UTF-8 ({exc.reason})")
        except json.JSONDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid JSON: {exc}")
        if not isinstance(parsed, dict):
            raise ops.GateFailure(f"{path} must hold an object")
        if parsed.get("question") != question_id:
            raise ops.GateFailure(
                f"{path} names question {parsed.get('question')!r} but "
                f"--question is {question_id}; refusing to expand one "
                f"question's suite from another's plan")

        labels = parsed.get("labels") or {}
        if not isinstance(labels, dict):
            raise ops.GateFailure(f"{path}: labels must be an object")
        resolved = {}
        for key, value in labels.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                raise ops.GateFailure(f"{path}: label key {key!r} is not a "
                                      f"case number")
            if not 1 <= index <= case_count:
                raise ops.GateFailure(
                    f"{path}: label for case {index}, but the suite has "
                    f"{case_count} case(s)")
            if not isinstance(value, str) or not value.strip():
                raise ops.GateFailure(
                    f"{path}: case {index} has an empty category")
            resolved[index] = value.strip()

        additions = parsed.get("additions")
        if not isinstance(additions, list) or not additions:
            raise ops.GateFailure(
                f"{path} adds no cases. Labelling alone is a different action "
                f"and is not what this command records.")
        for position, case in enumerate(additions, start=1):
            if not isinstance(case, dict):
                raise ops.GateFailure(f"{path}: addition {position} is not an "
                                      f"object")
            for field in hidden_tests.REQUIRED_FIELDS:
                if field not in case:
                    raise ops.GateFailure(
                        f"{path}: addition {position} is missing {field!r}")
            if not isinstance(case.get("stdin"), str):
                raise ops.GateFailure(
                    f"{path}: addition {position} stdin must be text")
            if not isinstance(case.get("expected_output"), str):
                raise ops.GateFailure(
                    f"{path}: addition {position} expected_output must be "
                    f"text — a non-text answer is the defect this milestone "
                    f"spent a phase repairing")
            label = case.get(LABEL_KEY)
            if not isinstance(label, str) or not label.strip():
                raise ops.GateFailure(
                    f"{path}: addition {position} has no {LABEL_KEY}. An "
                    f"unlabelled case cannot count toward coverage, so adding "
                    f"one silently would grow the suite without growing what "
                    f"the gate can see.")
        return resolved, additions

    def _derive(self, current, labels, additions):
        """
        The proposed suite, BUILT from the stored one.

        Existing cases are carried over by value with at most a label added, so
        their inputs, answers and explanations are preserved by construction.
        """
        proposed = copy.deepcopy(current)
        for index, label in labels.items():
            case = proposed[index - 1]
            if not isinstance(case, dict):
                raise ops.GateFailure(f"case {index} is not an object")
            existing = case.get(LABEL_KEY)
            if existing and existing != label:
                raise ops.GateFailure(
                    f"case {index} is already labelled {existing!r}; "
                    f"relabelling is a judgement about coverage that this "
                    f"command will not make silently")
            case[LABEL_KEY] = label
        proposed.extend(copy.deepcopy(addition) for addition in additions)
        return proposed

    # ── the invariants ────────────────────────────────────────────────

    def _check_invariants(self, current, proposed, added):
        if len(proposed) != len(current) + added:
            raise ops.GateFailure(
                f"the suite has {len(current)} case(s) and the proposal has "
                f"{len(proposed)}; {added} addition(s) were named")

        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            for key in PRESERVED_KEYS:
                if was.get(key) != now.get(key):
                    raise ops.GateFailure(
                        f"case {index} changes its {key!r} from "
                        f"{was.get(key)!r} to {now.get(key)!r}. This command "
                        f"adds cases and labels; changing an existing case is "
                        f"a different action class.")
            extra = set(now) - set(was) - {LABEL_KEY}
            if extra:
                raise ops.GateFailure(
                    f"case {index} gains key(s) {sorted(extra)}; only "
                    f"{LABEL_KEY!r} may be added to an existing case")
            if set(was) - set(now):
                raise ops.GateFailure(
                    f"case {index} loses key(s) {sorted(set(was) - set(now))}")

        for position, case in enumerate(proposed[len(current):],
                                        start=len(current) + 1):
            if not case.get(LABEL_KEY, "").strip():
                raise ops.GateFailure(f"case {position} has no {LABEL_KEY}")

        duplicates = quality.normalized_duplicate_indexes(proposed)
        if duplicates:
            pairs = ", ".join(f"case {i + 1} repeats case {j + 1}"
                              for i, j in duplicates)
            raise ops.GateFailure(
                f"the proposal contains duplicate input(s) under normalised "
                f"comparison: {pairs}. A repeated input adds no coverage and "
                f"the quality gate blocks on it.")

        identities = [provenance.case_identity(case.get("stdin", ""))
                      for case in proposed]
        if len(set(identities)) != len(identities):
            raise ops.GateFailure("two cases share a case identity")

    def _check_executable(self, question, proposed, existing_count):
        """
        Every ADDED case must bind under the question's declared contract.

        A case the adapter cannot deliver is not coverage — it is a case that
        refuses at grading time, which is how 48 production questions came to
        be ungradable in the first place.
        """
        version = execution_contract.contract_version(question)
        starter = (question.boilerplate_code or {}).get("python", "")
        if version != execution_contract.CONTRACT_V3:
            return
        for position, case in enumerate(proposed[existing_count:],
                                        start=existing_count + 1):
            invocation = execution_adapter.build_invocation(
                case.get("stdin", ""), starter)
            if not invocation.ok:
                raise ops.GateFailure(
                    f"added case {position} does not bind under {version}: "
                    f"{invocation.outcome} — {invocation.detail}")
            if invocation.warnings:
                raise ops.GateFailure(
                    f"added case {position} binds only by guessing "
                    f"({', '.join(invocation.warnings)})")

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, projected,
                     current, proposed):
        write = self.stdout.write
        starter = (question.boilerplate_code or {}).get("python", "")

        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:44]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write(f"  field           {REPAIRABLE_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  cases           {len(current)} -> {len(proposed)}  "
              f"(+{len(proposed) - len(current)}; floor is "
              f"{hidden_tests.MIN_HIDDEN_TESTS})")
        write(f"  suite matches the pre-image: "
              f"{current == record.captured_state()[REPAIRABLE_FIELD]}")
        write("")

        write("  EXISTING — inputs, answers and explanations unchanged:")
        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            added_label = (now.get(LABEL_KEY) if not was.get(LABEL_KEY)
                           else None)
            write(f"    case {index:>2}: stdin {self._short(was.get('stdin'))}"
                  f"  expected {was.get('expected_output')!r}")
            write(f"             category {was.get(LABEL_KEY, '(none)')!r}"
                  + (f" -> {added_label!r}  ADDED" if added_label else "")
                  + f"   identity {provenance.case_identity(was.get('stdin', ''))[:12]}…")

        write("")
        write("  ADDED:")
        for position, case in enumerate(proposed[len(current):],
                                        start=len(current) + 1):
            stdin = case.get("stdin", "")
            invocation = execution_adapter.build_invocation(stdin, starter)
            write(self.style.SUCCESS(f"    case {position:>2}: NEW"))
            write(f"             stdin      {self._short(stdin)}")
            write(f"             expected   {case.get('expected_output')!r}")
            write(f"             category   {case.get(LABEL_KEY)!r}")
            write(f"             why        {case.get('explanation', '')}")
            write(f"             identities case "
                  f"{provenance.case_identity(stdin)[:12]}…  input "
                  f"{provenance.input_identity(stdin)[:12]}…  output "
                  f"{provenance.output_identity(str(case.get('expected_output')))[:12]}…")
            write(f"             binds      "
                  f"{self._short(invocation.envelope(), 60) if invocation.ok else invocation.detail}")

        present = {str(case.get(LABEL_KEY, "")).strip().lower()
                   for case in proposed if case.get(LABEL_KEY)}
        write("")
        write(f"  categories present  {sorted(present)}")
        write(f"  duplicates          "
              f"{quality.normalized_duplicate_indexes(proposed) or 'none'}")
        write(f"  cases removed       0 (structural: existing cases are carried "
              f"over by value)")
        write(f"  cases reordered     0 (additions are appended)")
        write("")

    def _short(self, value, limit=48):
        """
        A long value, shortened for reading.

        The maximum-boundary case of a string problem is legitimately 10^4
        characters; printing it whole floods the terminal an operator is
        supposed to be reading carefully, which is how a dry-run stops being
        reviewed.
        """
        text = repr(value)
        if len(text) <= limit:
            return text
        return f"{text[:limit]}… ({len(value)} chars)"
