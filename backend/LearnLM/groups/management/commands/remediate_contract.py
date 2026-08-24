"""
Apply an approved CONTRACT_REPAIR to one question (M2 P2.7).

    python manage.py remediate_contract --alias contract \\
        --batch p27-pilot-1 --question 3309 --to-version v3 \\
        --reason "adjudication record C: two declared str parameters" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `execution_contract_version` —
of one question, and records an append-only `RemediationAction`.

── A third command, and a third role ───────────────────────────────────────

Statement repair changes what is asked. Hidden-test repair changes what answer
is recorded. This changes neither: it changes **how the stored inputs are
delivered to the learner's code**, which is a distinct authority over the same
row. Under v1 the wrapper guesses the shape of stdin; under v3 the adapter
builds a canonical JSON envelope from the declared signature and the wrapper
splats it positionally.

The three commands hold three disjoint column grants, so no role can perform
two of these acts and no order between them can be violated by mistake.

── The feasibility rule, and why it refuses more than it accepts ───────────

Declaring a contract a question cannot actually execute under is worse than
leaving it alone: the declaration is what the grader and the oracle both trust.
So before proposing anything this command binds EVERY stored case through
`execution_adapter` under the target contract, and refuses unless all of them
bind cleanly and unambiguously.

That includes refusing on a WARNING. `undeclared_parameter_type` means the
adapter had no declared type and fell back to guessing — which is the defect
this milestone exists to remove, not a detail to migrate on top of. A question
whose starter is unannotated needs its starter annotated first
(`BOILERPLATE_REPAIR`, a different column and a different review); it does not
need a contract declaration that makes the guess official.

── What it cannot do ───────────────────────────────────────────────────────

It cannot edit the statement, the answers, the starter code, the wrapper, the
status or the trust state — by `update_fields`, by an in-transaction re-check
of every other captured field, and by a column-scoped database role. It also
cannot move a question to v1 or v2: those are not repairs, and re-declaring a
question under a contract with different input semantics is a migration that
needs its own review.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import execution_adapter, execution_contract, pre_image, provenance
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "execution_contract_version"

#: The only contract a repair may move a question TO. See the module docstring.
TARGET_CONTRACT = execution_contract.CONTRACT_V3

#: The language whose starter declares the contract. The adapter reads Python
#: signatures; a question with no Python starter has no declared contract for
#: this command to check against, and is refused rather than assumed.
CONTRACT_LANGUAGE = "python"


class Command(BaseCommand):
    help = ("Migrate one question's execution contract to v3, after proving "
            "every stored case binds under it. Dry-run by default.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--to-version", required=True, metavar="VERSION",
            help=f"the contract to declare; only {TARGET_CONTRACT} is a repair")
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
            action="change an execution contract",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_CONTRACT_ROLES,
            required_privileges=ops.CONTRACT_REPAIR_PROBE,
            forbidden_privileges=ops.CONTRACT_REPAIR_FORBIDDEN)

        target = (options["to_version"] or "").strip()
        if target != TARGET_CONTRACT:
            raise ops.GateFailure(
                f"--to-version {target!r} is not a repair. This command only "
                f"migrates to {TARGET_CONTRACT!r}; declaring an older contract "
                f"changes what the stored inputs mean and needs its own "
                f"review.")

        self.stdout.write(self.style.MIGRATE_HEADING(
            "CONTRACT REPAIR" + ("" if writing else "  (DRY RUN)")))
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
        stored = before_state[REPAIRABLE_FIELD]
        effective = execution_contract.contract_version(question)
        if effective == target:
            raise ops.GateFailure(
                f"question {question.pk} already grades under {target}; "
                f"refusing to record a repair that changes nothing")

        before_digest = pre_image.live_digest(question)
        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: target}))

        bindings, refusals = self._assess(before_state, target)
        self._render_plan(batch, question, record, stored, effective, target,
                          before_digest, projected, before_state, bindings)

        if refusals:
            raise ops.GateFailure(
                f"question {question.pk} cannot be migrated to {target} yet:\n"
                + "\n".join(f"  - {reason}" for reason in refusals)
                + "\nNothing was written.")

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, target,
                                   before_state, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Execution contract repaired for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL contract; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, target, before_state,
               reason):
        with transaction.atomic(using=alias):
            locked = (Question.objects.using(alias)
                      .select_for_update().get(pk=question.pk))
            setattr(locked, REPAIRABLE_FIELD, target)
            locked.save(using=alias, update_fields=[REPAIRABLE_FIELD])

            locked.refresh_from_db(using=alias)
            after_state = pre_image.question_state(locked)

            for name, value in before_state.items():
                if name == REPAIRABLE_FIELD:
                    continue
                if after_state[name] != value:
                    raise ops.GateFailure(
                        f"{name} changed during a contract repair; the write "
                        f"has been reverted")

            if after_state[REPAIRABLE_FIELD] != target:
                raise ops.GateFailure(
                    f"the contract column holds "
                    f"{after_state[REPAIRABLE_FIELD]!r} after the write, not "
                    f"{target!r}; the write has been reverted")

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_CONTRACT_REPAIR,
                operator, detail=reason)
        return after_digest

    # ── feasibility ───────────────────────────────────────────────────

    def _assess(self, state, target):
        """
        ([binding, ...], [refusal, ...]) for every stored case under `target`.

        Everything is derived from the question's OWN starter and its OWN
        stored inputs. Nothing is inferred from the statement, and no input is
        modified — this command reads the cases only to decide whether the
        contract it is about to declare can actually execute them.
        """
        refusals = []

        wrapper = state.get("hidden_wrapper_code") or {}
        if wrapper:
            refusals.append(
                f"the question carries its own wrapper ({sorted(wrapper)}), "
                f"which takes precedence over the contract version, so "
                f"declaring {target} would change nothing while claiming to "
                f"repair it")

        starter = (state.get("boilerplate_code") or {}).get(CONTRACT_LANGUAGE)
        if not starter:
            refusals.append(
                f"no {CONTRACT_LANGUAGE} starter, so the question declares no "
                f"signature for the adapter to bind against")
            return [], refusals

        methods = execution_adapter.public_method_names(starter)
        if len(methods) > 1:
            refusals.append(
                f"the starter exposes {len(methods)} public methods {methods}; "
                f"which one runs is a guess, and a contract cannot be declared "
                f"over a guess")

        signature = execution_adapter.declared_signature(starter)
        if signature is None:
            refusals.append("no callable found in the starter code")
            return [], refusals

        cases = state.get("hidden_test_cases") or []
        if not cases:
            refusals.append("the question stores no test cases, so nothing "
                            "demonstrates that the contract executes")

        bindings = []
        for index, case in enumerate(cases, start=1):
            stdin, _expected, problem = execution_adapter.read_test_case(case)
            if problem:
                bindings.append((index, case, None, problem))
                refusals.append(f"case {index} is not structurally usable: "
                                f"{problem}")
                continue

            invocation = execution_adapter.build_invocation(stdin, starter)
            bindings.append((index, case, invocation, ""))
            if not invocation.ok:
                refusals.append(
                    f"case {index} cannot be expressed under {target}: "
                    f"{invocation.outcome} — {invocation.detail}")
            elif invocation.warnings:
                refusals.append(
                    f"case {index} binds only by guessing "
                    f"({', '.join(invocation.warnings)}); annotate the starter "
                    f"first — that is a BOILERPLATE_REPAIR, a different column "
                    f"and a different review")

        return bindings, refusals

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, stored, effective, target,
                     before_digest, projected, state, bindings):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write(f"  field           {REPAIRABLE_FIELD} (the ONLY field this "
              f"command can change)")
        write(f"  stored value    {stored!r}  (grades as {effective})")
        write(f"  proposed value  {target!r}")
        write("")

        starter = (state.get("boilerplate_code") or {}).get(CONTRACT_LANGUAGE, "")
        signature = execution_adapter.declared_signature(starter)
        if signature:
            name, parameters = signature
            rendered = ", ".join(
                f"{p}: {a}" if a else f"{p}  <-- UNANNOTATED"
                for p, a in parameters)
            write(f"  declared        {name}({rendered})")
            write(f"  arity           {len(parameters)}")
        write("")

        write("  cases — inputs are READ, never written:")
        for index, case, invocation, problem in bindings:
            stdin = case.get("stdin") if isinstance(case, dict) else case
            identity = (provenance.case_identity(stdin)
                        if isinstance(stdin, str) else "n/a")
            write(f"    case {index}: stdin {stdin!r}")
            write(f"            case_identity {identity[:16]}…  (unchanged — "
                  f"this command cannot write hidden_test_cases)")
            if problem:
                write(self.style.ERROR(f"            UNUSABLE  {problem}"))
                continue
            if invocation.ok:
                rendered = ", ".join(f"{value!r}" for value in invocation.arguments)
                write(self.style.SUCCESS(
                    f"            binds to  ({rendered})"))
                write(f"            envelope  {invocation.envelope()}")
                if invocation.warnings:
                    write(self.style.WARNING(
                        f"            WARNING   {', '.join(invocation.warnings)}"))
            else:
                write(self.style.ERROR(
                    f"            REFUSED   {invocation.outcome}: "
                    f"{invocation.detail}"))
        write("")
