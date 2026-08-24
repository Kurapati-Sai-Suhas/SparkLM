"""
Choose the execution contract for a reseeded question (M2 P2.7h-27).

    python manage.py reseed_contract --alias contract \\
        --batch reseed-slice-1 --question 9830 \\
        --expect-digest <sha256> \\
        --reason "reseed slice 1" --operator Suhas --apply --confirm

Dry-run by default. Writes at most ONE column of ONE question and records an
append-only `RemediationAction(CONTRACT_DECLARATION)`.

── Why this is not `remediate_contract`, and does not weaken it ────────────

`remediate_contract` refuses a question that stores no test cases:

    the question stores no test cases, so nothing demonstrates that the
    contract executes

That refusal is right for a LIVE question. Changing which harness runs changes
what its stored expected outputs mean, so it demands execution evidence before
it will act.

A reseed candidate has the exact opposite shape. It has no cases by
definition at this point in the pipeline — `stub_blockers` requires
`hidden_test_cases == []` — so there is nothing to demonstrate and nothing to
reinterpret. The contract must be chosen precisely BEFORE the suite exists,
because the suite is authored against it. Waiting for evidence here would
invert the lifecycle: cases first, contract second, every stored answer
silently re-read.

So this command's safety comes, like the rest of the reseed path, from a
PRECONDITION ON STATE rather than from execution evidence. `remediate_contract`
is left exactly as it was, and every question it would accept fails this
command's preconditions on several independent grounds.

── The role is shared; the gate is not ─────────────────────────────────────

Both commands write `execution_contract_version`, so both run as
`learnlm_contract_rw`. PostgreSQL grants are per column: a dedicated role
would need identical grants and would separate credentials, not capabilities.
The separation that matters is here, in the preconditions.

── An audited decision that may write nothing ──────────────────────────────

A signature classified V1_SUFFICIENT keeps `execution_contract_version` at
"v1" — byte-identical to a question nobody has examined. The DECISION is the
artifact, not the diff, so the action is recorded either way. Without that,
"we chose v1" and "we never looked" are the same row, and suite authoring
cannot tell whether it is allowed to start.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import (execution_adapter, pre_image, reseed_authoring,
                    reseed_contract_census as census)
from groups.management.commands import _preimage_ops as ops
from groups.models import (Question, RemediationAction, RemediationBatch,
                           ReseedLedger)

#: The single column this action class may change.
CONTRACT_FIELD = "execution_contract_version"


class Command(BaseCommand):
    help = ("Choose the execution contract for a reseeded question from its "
            "declared signature. Dry-run by default. Changes the contract "
            "version and nothing else, and refuses any question that has "
            "grading truth or has not had a signature declared.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--expect-digest", required=True, metavar="SHA256",
            help="The question's current state digest. REQUIRED: an "
                 "orchestrated write must prove it is acting on the state it "
                 "was planned against.")
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
            action="set the execution contract on a reseeded question",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_CONTRACT_ROLES,
            required_privileges=ops.CONTRACT_REPAIR_PROBE,
            forbidden_privileges=ops.CONTRACT_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "CONTRACT DECLARATION" + ("" if writing else "  (DRY RUN)")))
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

        # WRITE-AHEAD. Raises unless frozen, captured and verifying.
        record = pre_image.require_pre_image(batch, question)

        blockers = reseed_authoring.contract_blockers(
            question, pre_image_record=record, using=alias)
        if blockers:
            raise ops.GateFailure(
                f"question {question.pk} may not have its contract set:"
                f"\n  - " + "\n  - ".join(blockers))

        # Idempotency: one decision per question per batch. Re-running is a
        # refusal, not a silent second audit row — an append-only trail that
        # accumulates duplicates cannot answer "when was this decided".
        if RemediationAction.objects.using(alias).filter(
                batch=batch, question=question,
                action_class=RemediationAction.CLASS_CONTRACT_DECLARATION
        ).exists():
            raise ops.GateFailure(
                f"question {question.pk} already has a CONTRACT_DECLARATION "
                f"in batch {batch.batch_key}; the contract is chosen once")

        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)
        if options["expect_digest"] != before_digest:
            raise ops.GateFailure(
                f"the question is at {before_digest} but this write was "
                f"planned against {options['expect_digest']}. Refusing: it "
                f"has moved since.")

        target, verdict = reseed_authoring.contract_target(question)
        if target is None:
            raise ops.GateFailure(
                f"the declared signature does not classify: {verdict}. "
                f"NEEDS_MANUAL_REVIEW — this command will not guess a "
                f"contract, and no contract is better than a wrong one.")

        current = before_state[CONTRACT_FIELD] or "v1"
        self._render_plan(batch, question, record, before_digest, current,
                          target, verdict)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(
            alias, batch, question, operator, before_state, current, target,
            verdict, options["expect_digest"], options["reason"], record)

        self.stdout.write(self.style.SUCCESS(
            f"Contract set for question {question.pk}: {target} ({verdict})."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        if current == target:
            self.stdout.write(
                "  no field changed — the decision was v1 and the question "
                "already declared v1. The AUDIT ROW is the artifact.")
        self.stdout.write(
            "No hidden test case was created. The suite is written later and "
            "is bound against THIS contract.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, before_state, current,
               target, verdict, expected_digest, reason, record):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))

            blockers = reseed_authoring.contract_blockers(
                locked, pre_image_record=record, using=alias)
            if blockers:
                raise ops.GateFailure(
                    f"question {locked.pk} stopped being eligible between the "
                    f"plan and the write:\n  - " + "\n  - ".join(blockers))
            if pre_image.live_digest(locked) != expected_digest:
                raise ops.GateFailure(
                    "the question changed between the plan and the lock; the "
                    "write has been abandoned")

            # Re-decide under the lock. The signature is what the verdict is
            # computed from, and the signature lives in a column another role
            # can write.
            relocked_target, relocked_verdict = \
                reseed_authoring.contract_target(locked)
            if relocked_target != target or relocked_verdict != verdict:
                raise ops.GateFailure(
                    f"the contract decision changed under the lock: planned "
                    f"{target} ({verdict}), now {relocked_target} "
                    f"({relocked_verdict}); the write has been abandoned")

            if current != target:
                setattr(locked, CONTRACT_FIELD, target)
                locked.save(using=alias, update_fields=[CONTRACT_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)
            for name, value in before_state.items():
                if name == CONTRACT_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a contract declaration; the "
                        f"write has been reverted")
            if after_state[CONTRACT_FIELD] != target:
                raise ops.GateFailure(
                    f"the contract is {after_state[CONTRACT_FIELD]} after the "
                    f"write but {target} was chosen; the write has been "
                    f"reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_CONTRACT_DECLARATION,
                operator,
                detail=f"{reason} | {current} -> {target} ({verdict})")
        return after_digest

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, current,
                     target, verdict):
        write = self.stdout.write
        source = (question.boilerplate_code or {}).get("python") or ""
        declared = execution_adapter.declared_signature(source)

        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  field           {CONTRACT_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  hidden cases    {len(question.hidden_test_cases or [])} "
              f"(must be 0 — the suite is authored against this contract)")
        if declared:
            name, parameters = declared
            write(f"  signature       {name}("
                  + ", ".join(f"{p}: {a}" for p, a in parameters) + ")")
            write(f"  shape           {census.classify_shape(source)} "
                  f"{census.SHAPE_NAMES[census.classify_shape(source)]}")
        write("")
        write(f"  verdict         {verdict}")
        write(f"  contract        {current} -> {target}"
              + ("   (no change; the audit row is the artifact)"
                 if current == target else ""))
        if target == "v3":
            write("")
            write("  v3 reuses the v1 harness and changes only the stdin it "
                  "is fed:")
            write("    v1  stdin '[3, 6, 4]' -> method(3, 6, 4)   WRONG")
            write("    v3  stdin '[3, 6, 4]' -> method([3, 6, 4]) right")
        write("")
        write(f"  ledger stage    -> {ReseedLedger.STAGE_CONTRACT}")
        write("")
