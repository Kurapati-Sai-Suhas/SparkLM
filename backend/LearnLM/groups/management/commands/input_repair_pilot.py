"""
Apply the M18 input-repair PILOT: q121, q132 and q516, all or none.

    python manage.py input_repair_pilot --alias hiddentest \\
        --manifest remediation/m18_pilot_manifest.json \\
        --reason "M18 pilot: canonical v1 input representation" \\
        --operator Suhas
    # review the dry run, then repeat with:
        --apply --confirm --plan-digest <the PLAN DIGEST the dry run printed>

Dry-run by default. Writes exactly ONE column — `hidden_test_cases` — of at
most three questions, changing only the `stdin` of the cases the module plans,
and holds every expected output, the case order, the contract, the status and
the trust state fixed.

── Why a pilot command, not three `remediate_inputs` runs ─────────────────

`remediate_inputs` repairs one question from a hand-written changes file and
checks that the file changes only stdin. It cannot tell whether the new stdin
MEANS what the old one did, whether Java still receives it, whether the title
describes the method, or whether the second of three repairs failed after the
first landed. This command adds exactly those gates and reuses the rest:
`remediate_inputs`' reading of the changes file, its derivation and its
invariants, and `pre_image` for the write-ahead rule and the audit row.

── The manifest ────────────────────────────────────────────────────────────

    {"batch": "m18-input-pilot",
     "questions": [{"question": 121,
                    "changes_file": "q121_cases_input.json",
                    "review": {"verdict": "CONSISTENT", "title": "...",
                               "method": "maxProfit",
                               "statement_sha256": "..."}}, ...]}

`changes_file` is resolved beside the manifest. Both are grading data and are
gitignored. The review is a human attestation that the title, statement and
signature describe one problem; a dry run prints the fingerprint to attest.

── All or none ─────────────────────────────────────────────────────────────

Every question is validated before any is written. The writes then run inside
ONE outer transaction, each question in its own locked savepoint that is
re-validated immediately before the write and re-proved against what landed.
Any failure — in any question, at any step — rolls back every question and
every audit row. A partial pilot is not a state this command can leave.
"""

import hashlib
import json
import pathlib
from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import input_repair, pre_image
from groups.management.commands import _preimage_ops as ops
from groups.management.commands import remediate_inputs
from groups.models import Question, RemediationAction, RemediationBatch

#: The only questions this pilot may touch.
PILOT_QUESTIONS = frozenset({121, 132, 516})

#: Questions no pilot may touch, whatever a manifest says. Checked before the
#: allow-list so the refusal names the real reason.
PROTECTED_QUESTIONS = frozenset({21, 98, 100, 105, 110})

#: At most this many questions per pilot run.
MAX_QUESTIONS = 3

REPAIRABLE_FIELD = remediate_inputs.REPAIRABLE_FIELD


@dataclass
class Prepared:
    """One question, validated and planned, before anything is written."""
    question: Question
    record: object
    repair: input_repair.RepairPlan
    changes: list
    state: dict
    proposed: list
    before_digest: str
    projected: str
    eligibility: tuple


def eligibility_of(question):
    """Adaptive eligibility, question-level and per language — must not move."""
    return (question.is_adaptive_eligible,
            tuple(question.adaptive_eligible_for(lang)
                  for lang in input_repair.REFLECTION_LANGUAGES))


def plan_digest(batch_key, items):
    """
    The digest an apply must quote back. Binds the apply to the reviewed dry
    run: any change to a question, its pre-image or its approved changes
    produces a different digest and the apply refuses.
    """
    payload = {"batch": batch_key, "questions": [
        {"question": item.question.pk, "pre_image": item.record.state_digest,
         "before": item.before_digest, "after": item.projected,
         "changes": item.changes} for item in items]}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Command(BaseCommand):
    help = ("Apply the M18 input-repair pilot (q121, q132, q516), all or none. "
            "Dry-run by default; --apply needs the dry run's plan digest.")

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True, metavar="PATH")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument("--local", action="store_true")
        parser.add_argument("--plan-digest", metavar="SHA256")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]
        self.inputs = remediate_inputs.Command()

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="apply the M18 input-repair pilot",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_HIDDEN_TEST_ROLES,
            required_privileges=ops.HIDDEN_TEST_REPAIR_PROBE,
            forbidden_privileges=ops.HIDDEN_TEST_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "M18 INPUT-REPAIR PILOT" + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        manifest_path = pathlib.Path(options["manifest"])
        manifest = self._read_manifest(manifest_path)
        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=manifest["batch"]).first()
        if batch is None:
            raise ops.GateFailure(f"no such batch: {manifest['batch']}")

        # Every question is validated before any is written.
        items = [self._prepare(alias, batch, entry, manifest_path.parent)
                 for entry in manifest["questions"]]
        digest = plan_digest(batch.batch_key, items)
        for item in items:
            self._render(batch, item)
        self.stdout.write(f"PLAN DIGEST  {digest}")

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - nothing was written. Review the plan, then re-run "
                "with --apply --confirm --plan-digest <the digest above>."))
            return

        if options["plan_digest"] != digest:
            raise ops.GateFailure(
                f"--plan-digest {options['plan_digest']!r} is not this plan's "
                f"digest {digest}. Refusing: an apply must quote the dry run it "
                f"was reviewed against, and the plan has changed or was never "
                f"reviewed.")

        results = self._apply(alias, batch, operator, items,
                              options["reason"], digest)
        for pk, before, after, action in results:
            self.stdout.write(self.style.SUCCESS(
                f"q{pk}: repaired  {before[:16]} -> {after[:16]}  "
                f"audit #{action}"))
        self.stdout.write(
            "Every pre-image is unchanged and holds the ORIGINAL suite; each "
            "question can be rolled back with preimage_rollback.")

    # ── the manifest ──────────────────────────────────────────────────

    def _read_manifest(self, path):
        if not path.is_file():
            raise ops.GateFailure(f"no such manifest: {path}")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ops.GateFailure(f"{path} is not valid UTF-8 JSON ({exc})")
        if not isinstance(manifest, dict) or not isinstance(
                manifest.get("batch"), str):
            raise ops.GateFailure(f"{path} must name a 'batch'")
        entries = manifest.get("questions")
        if not isinstance(entries, list) or not entries:
            raise ops.GateFailure(f"{path} names no questions")
        if len(entries) > MAX_QUESTIONS:
            raise ops.GateFailure(
                f"{path} names {len(entries)} questions; a pilot run takes at "
                f"most {MAX_QUESTIONS}. This is not a bulk remediation tool.")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ops.GateFailure(f"{path}: each question must be an object")
            pk = entry.get("question")
            if not isinstance(pk, int) or isinstance(pk, bool):
                raise ops.GateFailure(f"{path}: question {pk!r} is not an id")
            if pk in PROTECTED_QUESTIONS:
                raise ops.GateFailure(
                    f"question {pk} is protected; no pilot may modify it")
            if pk not in PILOT_QUESTIONS:
                raise ops.GateFailure(
                    f"question {pk} is not a pilot question; this command "
                    f"repairs only {sorted(PILOT_QUESTIONS)}")
            if pk in seen:
                raise ops.GateFailure(f"question {pk} is named twice")
            seen.add(pk)
            if not isinstance(entry.get("changes_file"), str):
                raise ops.GateFailure(f"question {pk} names no changes_file")
        return manifest

    # ── validating one question, before anything is written ───────────

    def _prepare(self, alias, batch, entry, base):
        pk = entry["question"]
        question = Question.objects.using(alias).filter(pk=pk).first()
        if question is None:
            raise ops.GateFailure(f"no such question: {pk}")
        try:
            record = pre_image.require_pre_image(batch, question)
        except pre_image.PreImageError as exc:
            raise ops.GateFailure(f"q{pk}: {exc}")
        before_digest = pre_image.live_digest(question)
        if before_digest != record.state_digest:
            raise ops.GateFailure(
                f"q{pk} has moved since its pre-image was frozen (live "
                f"{before_digest[:12]}, pre-image {record.state_digest[:12]}). "
                f"Refusing: the repair would be planned against a state the "
                f"pre-image cannot restore.")

        changes_path = pathlib.Path(entry["changes_file"])
        if not changes_path.is_absolute():
            changes_path = base / changes_path
        changes = self.inputs._read_changes(str(changes_path), pk)

        state = pre_image.question_state(question)
        current = state[REPAIRABLE_FIELD]
        try:
            repair = input_repair.plan(question)
            input_repair.check_review(question, repair, entry.get("review"))
            input_repair.check_changes(question, repair, changes)
            proposed = self.inputs._derive(current, changes)
            input_repair.check_suite_transition(question, repair, current,
                                                proposed)
        except input_repair.RepairRefused as exc:
            raise ops.GateFailure(f"q{pk}: {exc}")
        self.inputs._check_invariants(current, proposed, changes)

        projected = pre_image.state_digest(
            pk, dict(state, **{REPAIRABLE_FIELD: proposed}))
        return Prepared(question, record, repair, changes, state, proposed,
                        before_digest, projected, eligibility_of(question))

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, operator, items, reason, digest):
        results = []
        with transaction.atomic(using=alias):
            for item in items:
                with transaction.atomic(using=alias):
                    results.append(self._apply_one(alias, batch, operator,
                                                   item, reason, digest))
        return results

    def _apply_one(self, alias, batch, operator, item, reason, digest):
        pk = item.question.pk
        locked = (Question.objects.using(alias)
                  .select_for_update().get(pk=pk))

        # Re-validated under the lock, immediately before the write.
        if pre_image.live_digest(locked) != item.before_digest:
            raise ops.GateFailure(
                f"q{pk} changed between the dry run and the apply; nothing "
                f"has been written")
        try:
            repair = input_repair.plan(locked)
            input_repair.check_changes(locked, repair, item.changes)
        except input_repair.RepairRefused as exc:
            raise ops.GateFailure(f"q{pk}: re-validation failed: {exc}")
        if self.inputs._derive(getattr(locked, REPAIRABLE_FIELD),
                               item.changes) != item.proposed:
            raise ops.GateFailure(
                f"q{pk}: the locked row no longer derives the reviewed proposal")

        self._write(alias, locked, item.proposed)

        # Re-proved against what LANDED, not against what was proposed.
        locked.refresh_from_db(using=alias)
        after_state = pre_image.question_state(locked)
        for name, value in item.state.items():
            if name != REPAIRABLE_FIELD and after_state[name] != value:
                raise ops.GateFailure(
                    f"q{pk}: {name} changed during an input repair; every "
                    f"question in the pilot has been rolled back")
        before_suite = item.state[REPAIRABLE_FIELD]
        landed = after_state[REPAIRABLE_FIELD]
        try:
            input_repair.check_suite_transition(locked, item.repair,
                                                before_suite, landed)
        except input_repair.RepairRefused as exc:
            raise ops.GateFailure(
                f"q{pk}: the written suite is not the planned one ({exc}); "
                f"every question in the pilot has been rolled back")
        self.inputs._check_invariants(before_suite, landed, item.changes)
        if eligibility_of(locked) != item.eligibility:
            raise ops.GateFailure(f"q{pk}: adaptive eligibility moved")
        after_digest = pre_image.live_digest(locked)
        if after_digest != item.projected:
            raise ops.GateFailure(
                f"q{pk}: landed digest {after_digest[:12]} is not the planned "
                f"{item.projected[:12]}")

        action = pre_image.record_action(
            batch, locked, RemediationAction.CLASS_INPUT_REPAIR, operator,
            detail=f"{reason} [M18 pilot plan {digest}]")
        return pk, item.before_digest, after_digest, action.pk

    def _write(self, alias, locked, proposed):
        """The single write. One column, one row, under the caller's lock."""
        setattr(locked, REPAIRABLE_FIELD, proposed)
        locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

    # ── reporting ─────────────────────────────────────────────────────

    def _render(self, batch, item):
        write = self.stdout.write
        repair = item.repair
        signature = repair.signature
        write(f"q{item.question.pk} - {item.question.title.strip()[:60]}")
        write(f"  method          {signature.method}({signature.parameter}: "
              f"{signature.declaration})")
        write(f"  languages       {', '.join(repair.languages)}")
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  pre-image       {item.record.state_digest}")
        write(f"  live digest     {item.before_digest}")
        write(f"  projected after {item.projected}")
        write(f"  contract        {item.state['execution_contract_version']} "
              f"(unchanged)   status {item.state['status']}   trust "
              f"{item.state['trust_state']}   (unchanged)")
        for case_plan, case in zip(repair.cases, item.state[REPAIRABLE_FIELD]):
            if not case_plan.changes:
                write(f"    case {case_plan.case}: untouched")
                continue
            before = ", ".join(f"{lang} {'ok' if ok else 'NO'}"
                               for lang, ok in case_plan.bound_before)
            write(f"    case {case_plan.case}: [{case_plan.decoder}]")
            write(f"      stdin     {case_plan.before!r} -> {case_plan.after!r}")
            write(f"      means     {case_plan.value!r}")
            write(f"      binds     before: {before}   after: all ok")
            write(f"      expected  {case.get('expected_output')!r}  UNCHANGED")
        write("")
