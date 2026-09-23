"""
Migrate ONE live question from the v1 to the v2 execution contract (M14).

    # validate only -- the default; writes nothing, and works before a
    # pre-image exists, reporting its absence as a blocker
    python manage.py migrate_contract_v2 --batch m14-pilot-q100 \\
        --question 100 --to-version v2 --operator Suhas

    # the write, on the restricted contract role, after a frozen pre-image
    python manage.py migrate_contract_v2 --alias contract \\
        --batch m14-pilot-q100 --question 100 --to-version v2 \\
        --operator Suhas --apply --confirm

Writes exactly ONE column -- `execution_contract_version`, v1 -> v2 -- of one
question, and records an append-only `RemediationAction(CONTRACT_MIGRATION)`.

── Why a new command rather than widening `remediate_contract` ─────────────

`remediate_contract` refuses v2 on principle: "re-declaring a question under a
contract with different input semantics is a migration that needs its own
review." That refusal is correct and stays. A repair to v3 leaves the stored
inputs meaning what they meant; this changes what they mean -- a tree argument
arrives as a string under v1 and is BUILT into a node under v2 -- so every
stored expected output starts being read under a representation it was never
checked against. Different evidence, different audit class, different command.

── What decides eligibility ────────────────────────────────────────────────

`groups.contract_migration.eligibility`, which defers the structural verdict
entirely to `migration_readiness.classify` -- the function behind
`v2_migration_worklist`. This command never re-derives SAFE_TO_MIGRATE. It adds
only what the classifier cannot know: the question must be on v1, and must not
carry a wrapper that would make the contract column irrelevant.

The same function runs twice: once to build the plan, and again on the LOCKED
row inside the transaction immediately before the write, so a verdict that
changes between the two -- content edited, quarantine added, classifier
revised -- stops the write rather than being overtaken by it.

── What it cannot do ───────────────────────────────────────────────────────

It cannot change an expected output, a stored input, the statement, the
starter, the status or the trust state:

  * by the database role. On production it runs as `learnlm_contract_rw`,
    which `run_gates` verifies holds UPDATE on this one column and is REFUSED
    if it holds UPDATE on content, hidden_test_cases, status, trust_state,
    boilerplate_code or hidden_wrapper_code. The guarantee does not rest on
    this file being correct.
  * by `update_fields=[execution_contract_version]`.
  * by re-reading every captured field after the write and reverting the
    whole transaction if anything but the contract moved.

It cannot migrate more than one question per run: `--question` takes one id
and refuses to be given twice. There is no --force, no --skip-checks, and no
flag that relaxes a gate.

── Confirmation, and why it is stricter than its siblings ──────────────────

The shared gate (`_preimage_ops.require_confirmation`) demands --confirm only
against production; on a local database `--apply` alone writes. That is
reasonable for a repair. It is not reasonable for a change of input semantics,
and it would also make the demand untestable -- every test runs locally. So
this command requires --confirm with --apply on EVERY database.
"""

import argparse
import copy

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import contract_migration, migration_readiness, pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

MIGRATED_FIELD = contract_migration.MIGRATED_FIELD
TARGET_CONTRACT = contract_migration.TARGET_CONTRACT


class _Once(argparse.Action):
    """Refuse a repeated flag instead of silently keeping the last value.

    argparse's default `store` keeps the LAST occurrence, so
    `--question 100 --question 110` would quietly migrate 110 alone while the
    operator believed they had asked for two. One question per run is a safety
    property; a flag that can be overridden by accident does not provide it.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error(
                f"{option_string} was given more than once. This command "
                f"migrates exactly one question per run; there is no batch "
                f"mode.")
        setattr(namespace, self.dest, values)


class Command(BaseCommand):
    help = ("Migrate ONE question's execution contract from v1 to v2, only if "
            "the M14 classifier calls it SAFE_TO_MIGRATE and a frozen, "
            "current pre-image exists. Dry-run by default.")

    #: Empty, so Django does not add its `--skip-checks` flag to this command.
    #:
    #: That flag skips Django's configuration checks, not any gate here -- but
    #: `--help` on a safety-critical migration would list `--skip-checks`, and
    #: an operator is entitled to read that name literally. The configuration
    #: checks still run on every deploy (the start chain runs `migrate`) and in
    #: CI; every migration gate below runs unconditionally regardless.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY",
                            help="the remediation batch holding the frozen "
                                 "pre-image")
        parser.add_argument("--question", required=True, type=int,
                            metavar="ID", action=_Once, default=None,
                            help="exactly one question id")
        parser.add_argument(
            "--to-version", required=True, metavar="VERSION",
            help=f"must be {TARGET_CONTRACT}; stated explicitly so the "
                 f"operator names the contract being declared")
        parser.add_argument("--operator", required=True, metavar="USERNAME")
        parser.add_argument("--alias", default="default")
        parser.add_argument("--apply", action="store_true",
                            help="perform the write (otherwise validate only)")
        parser.add_argument("--confirm", action="store_true",
                            help="required with --apply, on every database")
        parser.add_argument("--local", action="store_true",
                            help="target a non-production database")

    def handle(self, *args, **options):
        alias = options["alias"]
        writing = options["apply"]

        if writing and not options["confirm"]:
            raise ops.GateFailure(
                "--apply requires --confirm, on every database including "
                "--local. A contract migration changes what every stored "
                "input means; nothing was written.")

        operator, identity = ops.run_gates(
            alias, options["operator"],
            action="migrate an execution contract to v2",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_CONTRACT_ROLES,
            required_privileges=ops.CONTRACT_REPAIR_PROBE,
            forbidden_privileges=ops.CONTRACT_REPAIR_FORBIDDEN)

        target = (options["to_version"] or "").strip()
        if target != TARGET_CONTRACT:
            raise ops.GateFailure(
                f"--to-version {target!r} is refused. This command migrates to "
                f"{TARGET_CONTRACT!r} and nothing else: v3 is a repair "
                f"(`remediate_contract`) and v1 is where a migration starts.")

        question = Question.objects.using(alias).filter(
            pk=options["question"]).first()
        if question is None:
            raise ops.GateFailure(f"no such question: {options['question']}")

        batch = RemediationBatch.objects.using(alias).filter(
            batch_key=options["batch"]).first()

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"M14 CONTRACT MIGRATION  v1 -> {TARGET_CONTRACT}"
            + ("" if writing else "  (DRY RUN)")))
        ops.render_identity(self, identity, operator)
        self.stdout.write("")

        before_state = pre_image.question_state(question)
        try:
            record = self._require_rollback_anchor(
                batch, question, options["batch"])
            preimage_refusals = []
        except ops.GateFailure as exc:
            # Collected, not raised, so the plan still shows everything else
            # an operator needs -- a dry run before the freeze is useful.
            record, preimage_refusals = None, [str(exc)]
        verdict, eligibility_refusals = contract_migration.eligibility(
            question.pk, before_state)
        digests = contract_migration.component_digests(before_state)

        self._render_plan(question, batch, record, before_state, verdict,
                          digests, operator)

        refusals = preimage_refusals + eligibility_refusals
        if refusals:
            raise ops.GateFailure(
                f"question {question.pk} cannot be migrated to "
                f"{TARGET_CONTRACT}:\n"
                + "\n".join(f"  - {reason}" for reason in refusals)
                + "\nNothing was written.")

        if not writing:
            self.stdout.write(self.style.SUCCESS(
                "DRY RUN -- every gate passed and nothing was written. Re-run "
                "with --alias contract --apply --confirm to migrate."))
            return

        action, after_digest = self._apply(alias, options["batch"], question,
                                           operator)

        self.stdout.write(self.style.SUCCESS(
            f"Question {question.pk} migrated v1 -> {TARGET_CONTRACT}."))
        self.stdout.write(f"  audit action    {action.pk} "
                          f"({RemediationAction.CLASS_CONTRACT_MIGRATION})")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The frozen pre-image still holds v1; `preimage_rollback` can "
            "restore it.")

    # ── gates ─────────────────────────────────────────────────────────

    def _require_rollback_anchor(self, batch, question, batch_key):
        """The verified, current pre-image -- or a clean refusal. THE gate.

        Every property a rollback anchor needs is checked here, and any
        failure raises `GateFailure` rather than returning a flag a caller
        could forget to read:

          1. the batch exists and is frozen            (require_pre_image)
          2. it holds a pre-image for this question    (require_pre_image)
          3. that pre-image verifies against its own
             digest -- not corrupt, not tampered       (require_pre_image)
          4. it still describes the live row           (staleness)

        Called twice. In the plan, the refusal is caught and shown. On the
        LOCKED row inside the transaction it is not caught, and the contract
        write never starts.

        `record_action` re-checks 1-3 AFTER the write, inside the same
        transaction. That is a backstop, not this gate -- and it does not
        check 4 at all. For a stale pre-image this function is the only
        protection there is, which mutation testing confirmed: with it
        disabled, a stale migration did not merely write and roll back, it
        committed.

        This replaced a gate that returned `(record, refusals)`. Two values
        meant two things the write path had to remember to check, and the
        first mutation review found a variant where the refusal list came
        back empty with no record -- stopped only because the audit step
        happened to crash on `record.pk`.
        """
        if batch is None:
            raise ops.GateFailure(
                f"no batch {batch_key!r}. Capture and freeze a pre-image "
                f"first:  preimage_capture --batch {batch_key} --questions "
                f"{question.pk} --operator <you> --apply --confirm, then "
                f"--freeze")
        try:
            record = pre_image.require_pre_image(batch, question)
        except pre_image.PreImageError as exc:
            raise ops.GateFailure(str(exc)) from None
        stale = contract_migration.staleness(record, question)
        if stale:
            raise ops.GateFailure(stale[0])
        return record

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch_key, question, operator):
        """The only write. Every gate is re-run on the locked row first."""
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            # Re-read by the operator's key, not by the plan's object: the
            # gate below owns the "no such batch" refusal, and a `.get(pk=...)`
            # here would pre-empt it with an AttributeError when there is none.
            batch = RemediationBatch.objects.using(alias).filter(
                batch_key=batch_key).first()

            # 1. The rollback anchor, FIRST, on the locked row, before any
            #    write. Not caught: a refusal here ends the transaction with
            #    the question row untouched.
            try:
                record = self._require_rollback_anchor(
                    batch, locked, batch_key)
            except ops.GateFailure as exc:
                raise ops.GateFailure(
                    f"question {locked.pk} stopped being migratable between "
                    f"the plan and the write -- the rollback anchor was "
                    f"refused before any write:\n  - {exc}\n"
                    f"Nothing was written.") from None

            # 2. Eligibility, on the same locked row.
            #
            # A DEEP copy, not `question_state(locked)` itself: that returns
            # the row's own dict and list objects, so a write that mutated
            # `hidden_test_cases` in place and saved it would move this
            # snapshot too, and the after-write comparison below would compare
            # the corrupted suite with itself and pass.
            locked_state = copy.deepcopy(pre_image.question_state(locked))
            verdict, refusals = contract_migration.eligibility(
                locked.pk, locked_state)
            if refusals:
                raise ops.GateFailure(
                    f"question {locked.pk} stopped being migratable between "
                    f"the plan and the write:\n"
                    + "\n".join(f"  - {reason}" for reason in refusals)
                    + "\nNothing was written.")

            # 3. Only now, the one write.
            digests = contract_migration.component_digests(locked_state)
            setattr(locked, MIGRATED_FIELD, TARGET_CONTRACT)
            locked.save(using=alias, update_fields=[MIGRATED_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)
            for name, value in locked_state.items():
                if name == MIGRATED_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a contract migration; the "
                        f"whole transaction has been reverted")
            if after_state[MIGRATED_FIELD] != TARGET_CONTRACT:
                raise ops.GateFailure(
                    f"the contract column holds {after_state[MIGRATED_FIELD]!r} "
                    f"after the write, not {TARGET_CONTRACT!r}; reverted")

            action = pre_image.record_action(
                batch, locked, RemediationAction.CLASS_CONTRACT_MIGRATION,
                operator,
                detail=contract_migration.audit_detail(record, verdict, digests))
            after_digest = pre_image.live_digest(locked)
        return action, after_digest

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, question, batch, record, state, verdict, digests,
                     operator):
        write = self.stdout.write
        stored = state.get(MIGRATED_FIELD)
        effective = contract_migration.effective_version(question.pk, state)
        cases = state.get("hidden_test_cases") or []

        write(f"  question        {question.pk} -- {(question.title or '').strip()[:48]}")
        write(f"  current version {stored!r}  (grades as {effective})")
        write(f"  target version  {TARGET_CONTRACT!r}")
        write(f"  field           {MIGRATED_FIELD} (the ONLY column written)")
        write("")

        if verdict is None:
            write(self.style.ERROR("  classifier      not a structural question"))
        else:
            style = self.style.SUCCESS if verdict.safe else self.style.ERROR
            write(style(f"  classifier      {verdict.bucket}"))
            write(f"                  {verdict.reason}")
            write(f"  method          {verdict.method or '-'}")
            write(f"  parameter kinds {', '.join(verdict.parameter_kinds) or '-'}")
            write(f"  return kind     {verdict.return_kind or 'scalar'}")
            for note in verdict.notes:
                write(self.style.WARNING(f"  note            {note}"))
        write("")

        kinds = verdict.parameter_kinds if verdict is not None else []
        write(f"  cases           {len(cases)} -- inputs are READ, never written")
        for index, case in enumerate(cases):
            stdin = case.get("stdin") if isinstance(case, dict) else None
            arguments = migration_readiness.parse_arguments(stdin)
            arity_ok = len(arguments) == len(kinds) if kinds else None
            inputs = []
            for position, kind in enumerate(kinds):
                if kind in migration_readiness.STRUCTURAL_KINDS:
                    value = arguments[position] if position < len(arguments) else ""
                    fixed, canonical = migration_readiness.canonical_round_trip(
                        value, kind)
                    inputs.append("canonical" if fixed else
                                  ("normalises" if canonical is not None
                                   else "INVALID"))
            arity = ("-" if arity_ok is None else
                     f"{len(arguments)}/{len(kinds)} {'ok' if arity_ok else 'MISMATCH'}")
            write(f"    case {index}: {stdin!r:32} arity {arity:14} "
                  f"inputs {', '.join(inputs) or '-'}")
        write("")

        write("  digests (the migration may change NONE of these)")
        for name, value in digests.items():
            write(f"    {name:18} {value[:16]}…")
        write("")

        if record is None:
            write(self.style.ERROR(
                f"  pre-image       MISSING -- batch "
                f"{batch.batch_key if batch else '(none)'}; required before "
                f"--apply"))
        else:
            # Never assume `frozen_at` is set just because a record came back:
            # that invariant is the gate's to enforce, not the renderer's to
            # rely on. When it was assumed, a disabled gate crashed HERE --
            # before the write -- and the crash hid whether any pre-write
            # protection existed at all.
            frozen = (f"frozen {batch.frozen_at:%Y-%m-%d %H:%M}Z"
                      if batch.frozen_at else "NOT FROZEN")
            write(self.style.SUCCESS(
                f"  pre-image       {record.pk} in {batch.batch_key} ({frozen})"))
            write(f"                  digest {record.state_digest[:16]}…  holds "
                  f"{record.captured_state().get(MIGRATED_FIELD)!r} for rollback")
        write("")

        write("  planned mutation")
        write(f"    UPDATE groups_question SET {MIGRATED_FIELD} = "
              f"'{TARGET_CONTRACT}' WHERE id = {question.pk}")
        write("    -- one row, one column, inside a transaction holding "
              "SELECT ... FOR UPDATE on it")
        write("  planned audit row")
        write(f"    RemediationAction(action_class="
              f"{RemediationAction.CLASS_CONTRACT_MIGRATION}, "
              f"question={question.pk}, batch="
              f"{batch.batch_key if batch else '?'}, pre_image="
              f"{record.pk if record else '?'}, applied_by={operator.username})")
        write(f"    detail: reason={contract_migration.REASON}; "
              f"from={contract_migration.SOURCE_CONTRACT}; to={TARGET_CONTRACT}; "
              f"classifier=...; component digests")
        write("")
