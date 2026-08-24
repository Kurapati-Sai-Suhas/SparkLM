"""
Apply an approved INPUT_REPAIR to one question (M2 P2.7, migration 0045).

    python manage.py remediate_inputs --alias hiddentest \\
        --batch p27-pilot-1 --question 3309 \\
        --changes-file remediation/q3309_case4_input.json \\
        --reason "approved plan: canonical v3 form for the empty/empty case" \\
        --operator Suhas --apply --confirm

Dry-run by default. Writes exactly ONE column — `hidden_test_cases` — of one
question, changing ONLY the `stdin` of explicitly named cases.

── The mirror of `remediate_hidden_tests` ──────────────────────────────────

Both commands write the same column, and they are exact opposites:

    remediate_hidden_tests   may rewrite expected_output, holds every stdin fixed
    remediate_inputs         may rewrite stdin, holds every expected_output fixed

Neither may add, remove or reorder a case.

**The database cannot enforce this separation.** Column-level grants gave
statement repair and hidden-test repair a real privilege boundary — one role can
write `content` and not `hidden_test_cases`, the other the reverse — but an
input repair and an answer repair are the same column, so `learnlm_hidden_test_rw`
authorises both. What separates them here is the command's invariant, its
`action_class`, and the tests that hold them; not a grant. That is stated
plainly rather than implied, because a boundary people believe is enforced when
it is not is worse than one they know they must maintain.

── Why the audit class is its own ──────────────────────────────────────────

Changing an input changes the QUESTION being asked. Changing an expected output
changes the ANSWER being recorded. Both may be legitimate; conflating them in
the audit trail would destroy the distinction that this milestone exists to
protect, so `INPUT_REPAIR` is a separate class (migration 0045) and this command
is the only thing that writes it.

── Derived, never retyped ──────────────────────────────────────────────────

The proposed suite is BUILT from the stored suite: every case is carried over by
value and only the named `stdin` is substituted. The change file states each
case's `before` text and the command refuses unless it matches what is stored,
so an approval written against a stale reading cannot land.

The carry-over source is the LIVE suite, not the pre-image, and that is
deliberate: a question whose answers were already repaired in this batch (q17,
q266) must not have that repair silently reverted by a later input repair. The
report prints whether live still matches the pre-image so the operator can see
which they are working from.
"""

import copy
import json
import pathlib

from django.core.management.base import BaseCommand
from django.db import transaction

from groups import execution_adapter, pre_image
from groups.management.commands import _preimage_ops as ops
from groups.models import Question, RemediationAction, RemediationBatch

#: The single column this action class may change.
REPAIRABLE_FIELD = "hidden_test_cases"

#: The key inside a case that this action class may change. Named as a constant
#: so the structural tests can assert the command mentions no other.
REPAIRABLE_KEY = "stdin"

#: The key it must hold fixed — the mirror of `remediate_hidden_tests`.
PROTECTED_KEY = "expected_output"


class Command(BaseCommand):
    help = ("Apply an approved input repair to one question. Dry-run by "
            "default. Changes only the stdin of explicitly named cases, and "
            "cannot change any expected output.")

    def add_arguments(self, parser):
        parser.add_argument("--batch", required=True, metavar="KEY")
        parser.add_argument("--question", required=True, type=int, metavar="ID")
        parser.add_argument(
            "--changes-file", required=True, metavar="PATH",
            help="JSON naming the cases to change and their before/after "
                 "stdin. A file, not an argument: grading data must not pass "
                 "through shell history.")
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
            action="repair a test-case input",
            confirmed=options["confirm"],
            require_production=not options["local"],
            needs_write=writing,
            allowed_roles=ops.ALLOWED_HIDDEN_TEST_ROLES,
            required_privileges=ops.HIDDEN_TEST_REPAIR_PROBE,
            forbidden_privileges=ops.HIDDEN_TEST_REPAIR_FORBIDDEN)

        self.stdout.write(self.style.MIGRATE_HEADING(
            "INPUT REPAIR" + ("" if writing else "  (DRY RUN)")))
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

        changes = self._read_changes(options["changes_file"], question.pk)
        before_state = pre_image.question_state(question)
        current = before_state[REPAIRABLE_FIELD]

        proposed = self._derive(current, changes)
        self._check_invariants(current, proposed, changes)

        before_digest = pre_image.live_digest(question)
        projected = pre_image.state_digest(
            question.pk, dict(before_state, **{REPAIRABLE_FIELD: proposed}))

        self._render_plan(batch, question, record, before_digest, projected,
                          current, proposed, changes)

        if not writing:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — nothing was written. Re-run with --apply --confirm."))
            return

        after_digest = self._apply(alias, batch, question, operator, proposed,
                                   before_state, changes, options["reason"])

        self.stdout.write(self.style.SUCCESS(
            f"Inputs repaired for question {question.pk}."))
        self.stdout.write(f"  before digest   {before_digest}")
        self.stdout.write(f"  after digest    {after_digest}")
        self.stdout.write(
            "The pre-image is unchanged and still holds the ORIGINAL suite; "
            "this question can be rolled back.")

    # ── the write ─────────────────────────────────────────────────────

    def _apply(self, alias, batch, question, operator, proposed, before_state,
               changes, reason):
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
                        f"{name} changed during an input repair; the write has "
                        f"been reverted")

            # Re-checked against what LANDED, not against what was proposed —
            # the two are the same only if the write did what it claimed.
            self._check_invariants(before_state[REPAIRABLE_FIELD],
                                   after_state[REPAIRABLE_FIELD], changes)

            after_digest = pre_image.live_digest(locked)
            pre_image.record_action(
                batch, locked, RemediationAction.CLASS_INPUT_REPAIR,
                operator, detail=reason)
        return after_digest

    # ── deriving the proposal ─────────────────────────────────────────

    def _read_changes(self, path, question_id):
        location = pathlib.Path(path)
        if not location.is_file():
            raise ops.GateFailure(f"no such changes file: {path}")
        try:
            parsed = json.loads(location.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid UTF-8 ({exc.reason})")
        except json.JSONDecodeError as exc:
            raise ops.GateFailure(f"{path} is not valid JSON: {exc}")

        if not isinstance(parsed, dict):
            raise ops.GateFailure(
                f"{path} must hold an object with 'question' and 'changes'")
        if parsed.get("question") != question_id:
            raise ops.GateFailure(
                f"{path} names question {parsed.get('question')!r} but "
                f"--question is {question_id}; refusing to apply an approval "
                f"written for a different question")

        changes = parsed.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ops.GateFailure(f"{path} has no changes to apply")

        seen = set()
        for entry in changes:
            if not isinstance(entry, dict):
                raise ops.GateFailure(f"{path}: each change must be an object")
            for key in ("case", "before", "after"):
                if key not in entry:
                    raise ops.GateFailure(
                        f"{path}: a change is missing {key!r}")
            index = entry["case"]
            if not isinstance(index, int) or isinstance(index, bool) or index < 1:
                raise ops.GateFailure(
                    f"{path}: case {index!r} is not a 1-based case number")
            if index in seen:
                raise ops.GateFailure(
                    f"{path}: case {index} is named twice; one change per case")
            seen.add(index)
            if not isinstance(entry["before"], str) or \
                    not isinstance(entry["after"], str):
                raise ops.GateFailure(
                    f"{path}: case {index} before/after must both be text")
            if entry["before"] == entry["after"]:
                raise ops.GateFailure(
                    f"{path}: case {index} does not change; refusing to record "
                    f"a repair that changes nothing")
        return changes

    def _derive(self, current, changes):
        """
        The proposed suite, BUILT from the stored one.

        Every case is carried over by value; only a named `stdin` is
        substituted. Nothing is retyped, so a case this repair does not touch is
        byte-identical by construction rather than by inspection.
        """
        proposed = copy.deepcopy(current)
        for entry in changes:
            index = entry["case"]
            if index > len(proposed):
                raise ops.GateFailure(
                    f"case {index} does not exist; the suite has "
                    f"{len(proposed)} case(s)")
            case = proposed[index - 1]
            if not isinstance(case, dict):
                raise ops.GateFailure(
                    f"case {index} is a {type(case).__name__}, not an object")
            stored = case.get(REPAIRABLE_KEY)
            if stored != entry["before"]:
                raise ops.GateFailure(
                    f"case {index} currently holds {stored!r}, but the "
                    f"approval was written against {entry['before']!r}. "
                    f"Refusing: the question has moved since the approval.")
            case[REPAIRABLE_KEY] = entry["after"]
        return proposed

    # ── the invariants ────────────────────────────────────────────────

    def _check_invariants(self, current, proposed, changes):
        """
        What an input repair may and may not do. Run BEFORE the write and again
        against what landed.
        """
        if len(current) != len(proposed):
            raise ops.GateFailure(
                f"the suite has {len(current)} case(s) and the proposal has "
                f"{len(proposed)}. Adding or removing a case is a different "
                f"action class and needs its own review.")

        targets = {entry["case"] for entry in changes}
        old_ids = pre_image.suite_case_identities(current)
        new_ids = pre_image.suite_case_identities(proposed)

        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            old_expected = was.get(PROTECTED_KEY) if isinstance(was, dict) else was
            new_expected = now.get(PROTECTED_KEY) if isinstance(now, dict) else now
            if old_expected != new_expected:
                raise ops.GateFailure(
                    f"case {index} changes its {PROTECTED_KEY} from "
                    f"{old_expected!r} to {new_expected!r}. This command "
                    f"repairs INPUTS; changing an answer is a different action "
                    f"class and needs its own review.")

            # The output identity is the repository's definition of "the same
            # answer". It must hold even where the input changed.
            if old_ids[index - 1][2] != new_ids[index - 1][2]:
                raise ops.GateFailure(
                    f"case {index} changes its output identity; the recorded "
                    f"answer is not the same value")

            if index in targets:
                if not isinstance(was, dict) or not isinstance(now, dict):
                    raise ops.GateFailure(
                        f"case {index} is not an object; it cannot be repaired")
                if was.get(REPAIRABLE_KEY) == now.get(REPAIRABLE_KEY):
                    raise ops.GateFailure(
                        f"case {index} was named for repair but its "
                        f"{REPAIRABLE_KEY} did not change")
                continue

            if was != now:
                raise ops.GateFailure(
                    f"case {index} was not named for repair but changed; "
                    f"refusing")

    # ── reporting ─────────────────────────────────────────────────────

    def _render_plan(self, batch, question, record, before_digest, projected,
                     current, proposed, changes):
        write = self.stdout.write
        starter = (question.boilerplate_code or {}).get("python", "")
        targets = {entry["case"] for entry in changes}
        old_ids = pre_image.suite_case_identities(current)
        new_ids = pre_image.suite_case_identities(proposed)

        write(f"  batch           {batch.batch_key} ({batch.state})")
        write(f"  question        {question.pk} — {question.title[:48]}")
        write(f"  pre-image       {record.state_digest}")
        write(f"  current digest  {before_digest}")
        write(f"  projected after {projected}")
        write(f"  field           {REPAIRABLE_FIELD}, key {REPAIRABLE_KEY} "
              f"(the ONLY thing this command can change)")
        write(f"  cases           {len(current)} (unchanged count and order; "
              f"every {PROTECTED_KEY} held fixed)")
        write(f"  suite matches the pre-image: "
              f"{current == record.captured_state()[REPAIRABLE_FIELD]}")
        write("")

        for index, (was, now) in enumerate(zip(current, proposed), start=1):
            expected = now.get(PROTECTED_KEY)
            case_before, _input_before, output_before = old_ids[index - 1]
            case_after, _input_after, output_after = new_ids[index - 1]

            if index not in targets:
                write(f"    case {index}: untouched   stdin={now.get('stdin')!r}"
                      f"  {PROTECTED_KEY}={expected!r}")
                continue

            write(self.style.WARNING(f"    case {index}: STDIN CHANGED"))
            write(self.style.ERROR(f"      before stdin      "
                                   f"{was.get(REPAIRABLE_KEY)!r}"))
            write(self.style.SUCCESS(f"      after stdin       "
                                     f"{now.get(REPAIRABLE_KEY)!r}"))
            write(f"      {PROTECTED_KEY}   {expected!r}  UNCHANGED")
            write(f"      case identity     {case_before[:16]}…")
            write(f"                     -> {case_after[:16]}…  "
                  f"(changes: the input changed)")
            write(f"      output identity   {output_before[:16]}…")
            write(f"                     -> {output_after[:16]}…  "
                  f"{'UNCHANGED' if output_before == output_after else '*** MOVED ***'}")

            invocation = execution_adapter.build_invocation(
                now.get(REPAIRABLE_KEY), starter)
            if invocation.ok and not invocation.warnings:
                write(f"      binds as          {invocation.envelope()}")
            elif invocation.ok:
                write(f"      binds with warnings {invocation.warnings}")
            else:
                write(f"      does not bind yet   {invocation.outcome}: "
                      f"{invocation.detail}")
        write("")
