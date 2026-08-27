"""
Withdraw trust from a question: ORACLE_VERIFIED -> UNVERIFIED (M2 P2.7h-35).

    python manage.py question_demote --alias promote --question 3309 \\
        --expect-trust ORACLE_VERIFIED \\
        --batch p27-pilot-1 \\
        --reason "hidden suite exposed publicly; evidence no longer covers it" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `trust_state` — of one
question, and records an append-only `RemediationAction(TRUST_DEMOTION)`.

── Why this has to exist ───────────────────────────────────────────────────

Before it, ORACLE_VERIFIED was a one-way door. `question_promote` requires
UNVERIFIED and refuses otherwise; `question_status` states plainly that it
cannot write `trust_state`. So a question whose evidence stopped covering its
suite — because the suite was replaced, or its answer key was published —
kept claiming verified trust, AND could never re-earn it, because promotion
refuses a question that is already ORACLE_VERIFIED.

Trust that cannot be withdrawn is not a trust model; it is a one-time
assertion. This is the withdrawal.

── The narrowest thing that does the job ───────────────────────────────────

ONE transition, hard-coded: ORACLE_VERIFIED -> UNVERIFIED. There is no
`--to` argument and no state table, because a general "set the trust state"
command is a promotion path that skips every promotion gate — the oracle
evidence, the approval, the artifact digest. Demotion is safe to expose
precisely because it only ever REMOVES a claim; a setter that could also add
one would need all of promotion's machinery and would then be promotion.

── What it does not touch ──────────────────────────────────────────────────

NOT `status`. Withdrawing trust and withdrawing from service are independent
decisions with different consequences, and they keep separate roles and
separate commands. Demotion alone already stops a question teaching the
adaptive model, because `is_adaptive_eligible` requires BOTH.

NOT the approval. Promotion stamps `promoted_at` on the approval it rests
on, because a promotion is an event on that approval. A demotion is not: the
approval remains exactly what it always was, the record that a person once
read and vouched for a specific artifact. Rewriting it would destroy the
evidence of what was believed at the time.

NOT the reference, the suite, the content, or any submission.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
TRUST_FIELD = "trust_state"

#: The only transition. Not a parameter — see the module docstring.
FROM_STATE = Question.TRUST_ORACLE_VERIFIED
TO_STATE = Question.TRUST_UNVERIFIED


class Command(BaseCommand):
    help = ("Withdraw ORACLE_VERIFIED from a question, returning it to "
            "UNVERIFIED. Dry-run by default. Writes the trust state and "
            "nothing else, and never publishes or unpublishes.")

    def add_arguments(self, parser):
        parser.add_argument("--question", type=int, required=True, metavar="ID")
        parser.add_argument(
            "--batch", required=True, metavar="KEY",
            help="The frozen batch holding this question's pre-image. The "
                 "audit row is bound to it, so a demotion is as traceable as "
                 "every other write in this milestone.")
        parser.add_argument(
            "--expect-trust", required=True, metavar="STATE",
            help="The trust state the operator believes the question is in. "
                 "REQUIRED: a demotion planned against ORACLE_VERIFIED must "
                 "not silently succeed on a question something else already "
                 "demoted.")
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
            action=f"withdraw {FROM_STATE} from a question",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_DEMOTION_ROLES,
            required_privileges=ops.DEMOTION_PROBE,
            forbidden_privileges=ops.PROMOTION_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "TRUST DEMOTION" + ("" if writing else "  (DRY RUN)")))
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

        if options["expect_trust"] != question.trust_state:
            raise ops.GateFailure(
                f"question {question.pk} is {question.trust_state}, but this "
                f"demotion was planned against {options['expect_trust']}. "
                f"Refusing: it has moved since.")
        if question.trust_state != FROM_STATE:
            raise ops.GateFailure(
                f"question {question.pk} is {question.trust_state}; this "
                f"command performs exactly {FROM_STATE} -> {TO_STATE} and "
                f"nothing else")

        before_state = pre_image.question_state(question)
        before_digest = pre_image.live_digest(question)
        self._render_plan(batch, question, record, before_digest)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator,
                                   before_state, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Trust withdrawn from question {question.pk}: "
            f"{FROM_STATE} -> {TO_STATE}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "status is UNCHANGED — this command cannot write it. The question "
            "is no longer adaptive-eligible, because that requires BOTH "
            "PUBLISHED and ORACLE_VERIFIED.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, before_state, reason):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))

            # Re-checked under the lock. Between the plan and here another
            # writer may have demoted this question already, and demoting a
            # question twice would file a second withdrawal of a claim that
            # was gone.
            if locked.trust_state != FROM_STATE:
                raise ops.GateFailure(
                    f"question {locked.pk} became {locked.trust_state} "
                    f"between the plan and the lock; the write has been "
                    f"abandoned")

            locked.trust_state = TO_STATE
            locked.save(using=alias, update_fields=[TRUST_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)
            for name, value in before_state.items():
                if name == TRUST_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a trust demotion; the write "
                        f"has been reverted")
            if after_state[TRUST_FIELD] != TO_STATE:
                raise ops.GateFailure(
                    f"trust_state is {after_state[TRUST_FIELD]} after the "
                    f"write but {TO_STATE} was intended; the write has been "
                    f"reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_TRUST_DEMOTION,
                operator, detail=f"{reason} | {FROM_STATE} -> {TO_STATE}")
        return after_digest

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48].strip()}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  field           {TRUST_FIELD} (the ONLY field this command "
              f"can change)")
        write(f"  status          {question.status}  (UNCHANGED — a separate "
              f"command and a separate role)")
        write(f"  trust           {FROM_STATE} -> {TO_STATE}")
        write(f"  adaptive        {question.is_adaptive_eligible} -> False")
        write(f"  hidden cases    {len(question.hidden_test_cases or [])} "
              f"(untouched)")
        write("")
        write("  the approval and every oracle execution are LEFT AS THEY ARE:")
        write("    they record what was believed and what was run at the time,")
        write("    and a withdrawal of trust must not rewrite that history.")
        write("")
