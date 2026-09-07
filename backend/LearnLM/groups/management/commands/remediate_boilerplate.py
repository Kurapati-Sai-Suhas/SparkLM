"""
Apply an approved BOILERPLATE_REPAIR to one question (M2 P2.7).

    python manage.py remediate_boilerplate --alias boilerplate \\
        --batch p27-pilot-1 --question 1436 --language python \\
        --source-file remediation/q1436_approved_boilerplate.py \\
        --reason "approved plan: annotate paths as list[list[str]]" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `boilerplate_code` — of one
question, replacing exactly ONE language's starter.

── Why this column needs its own role and its own command ──────────────────

`boilerplate_code` is the code every learner is HANDED, and it is also the
declaration `execution_adapter` reads to decide how a submission is called. A
role able to edit it decides both what a learner starts from and how their
argument list is built — a different authority from repairing a statement, a
key, or a contract version, and one that must not arrive as a side effect of
holding any of those.

── ANNOTATION-ONLY ─────────────────────────────────────────────────────────

This command compares the current and proposed starters as SYNTAX TREES and
refuses everything except a change to parameter annotations:

    no renamed class, method or parameter        no reordered parameters
    no altered body                              no added or removed import
    no added or removed return annotation        no other language touched

That is narrower than "repair the boilerplate" and deliberately so. The approved
repair in this batch adds one annotation; a starter that needs rewriting is a
different action with a different review, and a command that could do both would
make this one's guarantees only as strong as the operator's care.

The comparison is structural rather than textual because a text diff cannot tell
`paths: list[list[str]]` from a renamed parameter or a quietly edited body — and
because a whitespace-only difference would be invisible to a naive equality
check while still changing what the learner sees.

── THE ONE RETURN-ANNOTATION EXCEPTION (M6.1) ──────────────────────────────

Return annotations were refused outright, on the stated grounds that "the
adapter binds inputs and never reads the return type, so this is a change to
what the learner is handed with no effect on grading". **That premise is wrong
for an undefined name.** A return annotation is evaluated at definition time
like any other, so `-> List[str]` raises `NameError` before the learner's first
line — the harness emits no imports. It is not cosmetic; it is the same defect
as `nums: List[int]`, in the other half of the signature. 16 questions were
left unrepairable by a guard whose reason did not apply to them.

The exception is therefore as narrow as the claim that justifies it. A return
annotation may change only when ALL of these hold:

    1. one is already there — this repairs, it does not add or remove
    2. the stored one names something UNDEFINED, by `language_readiness`'s
       definition of what the harness provides, not a second copy of it
    3. the proposed one names nothing undefined
    4. the proposed one is EXACTLY `language_readiness.canonical_annotation`
       of the stored one — the convention applied mechanically, not an
       opportunity to declare a different return type
    5. NO parameter annotation changes in the same proposal

(5) is what keeps the exception from widening the command. A starter needing
both halves repaired is two audited repairs, not one loosened one: the
parameter pass runs under the original rule, the return pass under this one,
and neither proposal can carry the other's change. Everything outside the
annotations is still held by the same structural comparison.
"""

import ast
import copy
import difflib
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import execution_adapter, language_readiness, pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "boilerplate_code"

#: Slot marker for a return annotation in a change tuple, where a parameter
#: name would otherwise sit.
RETURN_SLOT = "->"


class Command(BaseCommand):
    help = ("Apply an approved boilerplate annotation repair to one question. "
            "Dry-run by default. Changes one language's starter and nothing "
            "else, and cannot change anything but parameter annotations — or, "
            "on its own, a return annotation that names something undefined.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--language", required=True, metavar="KEY",
            help="Which starter to replace. Every other language is carried "
                 "over untouched.")
        parser.add_argument(
            "--source-file", required=True, metavar="PATH",
            help="File holding the approved replacement starter. A file, not "
                 "an argument: source must not pass through shell quoting.")
        parser.add_argument(
            "--expect-digest", metavar="SHA256",
            help="The question's current state digest. Given, the command "
                 "refuses unless live state matches it — so an approval cannot "
                 "be applied to a question that moved after it was written.")
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
            action="repair a question's starter code",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_BOILERPLATE_ROLES,
            required_privileges=ops.BOILERPLATE_REPAIR_PROBE,
            forbidden_privileges=ops.BOILERPLATE_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "BOILERPLATE REPAIR" + ("" if writing else "  (DRY RUN)")))
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
                f"the question is at {before_digest} but the approval was "
                f"written against {expected}. Refusing: it has moved since.")

        language = options["language"]
        current_starters = before_state[REPAIRABLE_FIELD] or {}
        if language not in current_starters:
            raise ops.GateFailure(
                f"question {question.pk} has no {language!r} starter; this "
                f"command repairs an existing one rather than adding a "
                f"language")

        current = current_starters[language]
        proposed_source = self._read_source(options["source_file"])
        if current == proposed_source:
            raise ops.GateFailure(
                "the file is byte-identical to the stored starter; refusing to "
                "record a repair that changes nothing")

        changes = self._check_annotation_only(current, proposed_source)

        proposed = dict(copy.deepcopy(current_starters),
                        **{language: proposed_source})
        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: proposed}))

        self._render_plan(batch, question, record, before_digest, projected,
                          language, current, proposed_source, current_starters,
                          proposed, changes)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, proposed,
                                   before_state, language, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Starter repaired for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL starter; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, proposed, before_state,
               language, reason):
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
                        f"{name} changed during a boilerplate repair; the "
                        f"write has been reverted")

            landed = after_state[REPAIRABLE_FIELD] or {}
            before_starters = before_state[REPAIRABLE_FIELD] or {}
            if set(landed) != set(before_starters):
                raise ops.GateFailure(
                    "the set of languages changed; the write has been reverted")
            for name, source in before_starters.items():
                if name == language:
                    continue
                if landed[name] != source:
                    raise ops.GateFailure(
                        f"the {name!r} starter changed during a repair of "
                        f"{language!r}; the write has been reverted")

            # Re-checked against what LANDED, not what was proposed.
            self._check_annotation_only(before_starters[language],
                                        landed[language])

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_BOILERPLATE_REPAIR,
                operator, detail=reason)
        return after_digest

    # ── the invariant ─────────────────────────────────────────────────

    def _read_source(self, path):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such source file: {path}")
        try:
            source = location.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid UTF-8 ({exc.reason})")
        if not source.strip():
            raise ops.GateFailure(f"{path} is empty or only whitespace")
        return source

    def _check_annotation_only(self, current, proposed):
        """
        [(function, parameter, before, after)] — or a refusal.

        Both sources are parsed; the proposal is then stripped of every
        annotation and compared against the current source stripped the same
        way. If anything but annotations moved, the stripped trees differ and
        the repair is refused.

        What survives that comparison is then split in two: parameter
        annotations, which this command has always been for, and return
        annotations, which it accepts only under the narrow M6.1 exception in
        the module docstring. A proposal carrying both is refused — repairing
        the two halves of a signature is two reviewed actions.
        """
        before_tree = self._parse(current, "the stored starter")
        after_tree = self._parse(proposed, "the proposed starter")

        if ast.dump(self._stripped(before_tree)) != \
                ast.dump(self._stripped(after_tree)):
            raise ops.GateFailure(
                "the proposal changes more than annotations. This command "
                "repairs an annotation; a renamed method, an edited body, a "
                "new import or a reordered parameter is a different action "
                "class and needs its own review.")

        parameter_changes = self._parameter_changes(before_tree, after_tree)
        return_changes = self._return_changes(before_tree, after_tree,
                                              bool(parameter_changes))

        changes = parameter_changes + return_changes
        if not changes:
            raise ops.GateFailure(
                "no annotation changed; the difference is whitespace or "
                "formatting only, which this command does not record as a "
                "repair")
        return changes

    def _parameter_changes(self, before_tree, after_tree):
        """
        [(function, parameter, before, after)]. Unchanged by M6.1: the return
        exception grants no authority here and takes none away.

        Zipped rather than looked up by name — the stripped comparison above
        has already proved the two trees have the same functions in the same
        order with the same parameters, so position is exact where a name
        would be ambiguous across two classes.
        """
        changes = []
        for before_fn, after_fn in zip(self._functions(before_tree),
                                       self._functions(after_tree)):
            for before_arg, after_arg in zip(self._arguments(before_fn),
                                             self._arguments(after_fn)):
                was = self._annotation(before_arg)
                now = self._annotation(after_arg)
                if was != now:
                    changes.append((before_fn.name, before_arg.arg, was, now))
        return changes

    def _return_changes(self, before_tree, after_tree, parameters_moved):
        """
        [(function, RETURN_SLOT, before, after)] — or a refusal.

        Each of the five conditions in the module docstring raises with the one
        that failed, so an operator reading a refusal learns which rule stopped
        them rather than that "something" did.
        """
        changes = []
        for before_fn, after_fn in zip(self._functions(before_tree),
                                       self._functions(after_tree)):
            was_node, now_node = before_fn.returns, after_fn.returns
            was = ast.unparse(was_node) if was_node is not None else None
            now = ast.unparse(now_node) if now_node is not None else None
            if was == now:
                continue

            if parameters_moved:
                raise ops.GateFailure(
                    f"the proposal changes both a parameter annotation and the "
                    f"return annotation of {before_fn.name!r}. Each half is a "
                    f"separate repair with its own review: apply the parameter "
                    f"annotations first, then the return annotation on its "
                    f"own.")

            self._check_return_repair(before_fn.name, was_node, now_node,
                                      before_tree, after_tree)
            changes.append((before_fn.name, RETURN_SLOT, was, now))
        return changes

    def _check_return_repair(self, name, before, after, before_tree,
                             after_tree):
        """
        The only circumstance in which a return annotation may move.

        `language_readiness` decides what counts as undefined and what the
        canonical repair is. This command deliberately does not restate either
        rule: a starter the readiness report calls broken and a starter this
        command agrees to repair must be the same set, and the way that stops
        being true is by keeping two copies.
        """
        if before is None or after is None:
            raise ops.GateFailure(
                f"the proposal {'adds a' if before is None else 'removes the'} "
                f"return annotation on {name!r}. This command repairs a return "
                f"annotation that cannot execute; adding or removing one "
                f"declares something new about the method and is a different "
                f"action class.")

        undefined = sorted(language_readiness.annotation_names(before)
                           - language_readiness.python_provided_names(
                               before_tree))
        if not undefined:
            raise ops.GateFailure(
                f"the stored return annotation of {name!r} is "
                f"{ast.unparse(before)!r}, which already resolves. A return "
                f"annotation may change ONLY to repair a NameError the learner "
                f"would hit before their first line; rewriting a working one "
                f"is a different action class and needs its own review.")

        remaining = sorted(language_readiness.annotation_names(after)
                           - language_readiness.python_provided_names(
                               after_tree))
        if remaining:
            raise ops.GateFailure(
                f"the proposed return annotation of {name!r} still names "
                f"{', '.join(remaining)}, which the harness never defines. The "
                f"repair would replace one NameError with another.")

        canonical = language_readiness.canonical_annotation(before)
        if ast.unparse(after) != canonical:
            raise ops.GateFailure(
                f"the proposed return annotation of {name!r} is "
                f"{ast.unparse(after)!r}, but the canonical repair of "
                f"{ast.unparse(before)!r} is {canonical!r}. This exception "
                f"applies the project's annotation convention mechanically; it "
                f"is not permission to declare a different return type.")

    def _parse(self, source, label):
        try:
            return ast.parse(source)
        except SyntaxError as exc:
            raise ops.GateFailure(f"{label} is not valid Python: {exc}")

    def _stripped(self, tree):
        """
        The same tree with every annotation removed — parameters AND returns.

        Returns are stripped here so that a return-annotation change reaches the
        rules written for it and is judged on its own terms, rather than being
        swept up by the generic comparison. A check that can only be reached
        after another one has already raised is not a check.
        """
        clone = copy.deepcopy(tree)
        for node in ast.walk(clone):
            if isinstance(node, ast.arg):
                node.annotation = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.returns = None
        return clone

    def _functions(self, tree):
        return [node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def _arguments(self, node):
        arguments = node.args
        return (list(arguments.posonlyargs) + list(arguments.args)
                + list(arguments.kwonlyargs))

    def _annotation(self, argument):
        if argument.annotation is None:
            return None
        return ast.unparse(argument.annotation)

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, projected,
                     language, current, proposed_source, current_starters,
                     proposed, changes):
        write = self.stdout.write
        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write(f"  field           {REPAIRABLE_FIELD}[{language!r}] (the ONLY "
              f"thing this command can change)")
        write(f"  languages       {sorted(current_starters)} -> "
              f"{sorted(proposed)}  (unchanged)")
        write(f"  size            {len(current)} -> {len(proposed_source)} bytes")
        write(f"  starter matches the pre-image: "
              f"{current_starters == record.captured_state()[REPAIRABLE_FIELD]}")
        write("")

        write("  annotations changed:")
        for function, parameter, was, now in changes:
            slot = ("return type" if parameter == RETURN_SLOT
                    else f"parameter {parameter}")
            write(f"    {function} — {slot}: {was or '(none)'} -> "
                  f"{now or '(none)'}")
        write("")

        write("  diff:")
        for line in difflib.unified_diff(
                current.splitlines(), proposed_source.splitlines(),
                "current", "proposed", lineterm="", n=2):
            style = (self.style.ERROR if line.startswith("-")
                     else self.style.SUCCESS if line.startswith("+")
                     else lambda text: text)
            write("    " + style(line))
        write("")

        signature = execution_adapter.declared_signature(proposed_source)
        if signature and language == "python":
            name, parameters = signature
            write(f"  declared signature after: {name}("
                  + ", ".join(f"{p}: {a}" if a else f"{p} <UNANNOTATED>"
                              for p, a in parameters) + ")")
            for stored in (question.hidden_test_cases or []):
                stdin = stored.get("stdin") if isinstance(stored, dict) else None
                invocation = execution_adapter.build_invocation(
                    stdin, proposed_source)
                state = ("OK" if invocation.ok and not invocation.warnings
                         else f"WARN {invocation.warnings}" if invocation.ok
                         else invocation.outcome)
                write(f"    {state:<10} "
                      + (invocation.envelope() if invocation.ok
                         else invocation.detail))
            write("  (a preview under the v3 adapter; this command does not "
                  "change the contract)")
        write("")
