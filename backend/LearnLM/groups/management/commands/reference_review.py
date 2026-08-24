"""
Human review and approval of reference solutions (M2 P2.7d-2).

P2.7d built the lifecycle — DRAFT -> IN_REVIEW -> APPROVED -> ACTIVE, three
database CHECK constraints, frozen approval provenance — but exposed it only
as model methods. Nothing let a human actually walk it, so no reference could
ever be approved, and every phase downstream (oracle execution, expected-output
generation, ORACLE_VERIFIED, adaptive evidence, Glicko) was unreachable. This
command is that missing first link, and nothing more.

── Why a management command and not a UI ───────────────────────────────────

`ReferenceSolution` is grading truth. It is deliberately absent from the Django
admin, has no serializer, no viewset and no route, and
`test_reference_solution_secrecy` fails if any appears — including a
source-level guard against any module that both reads `source_code` and builds
an HTTP response. A web surface would trade that structural guarantee for a
permission check. A management command has no HTTP surface to leak through, so
the guarantee is unchanged.

    python manage.py reference_review list
    python manage.py reference_review list --state IN_REVIEW
    python manage.py reference_review inspect 12 --operator alice --show-source
    python manage.py reference_review submit 12 --operator alice
    python manage.py reference_review approve 12 --operator alice --confirm
    python manage.py reference_review reject 12 --operator alice --confirm
    python manage.py reference_review activate 12 --operator alice
    python manage.py reference_review deactivate 12 --operator alice

── What this command will not do ───────────────────────────────────────────

It never creates a reference, never edits `source_code`, never touches
`Question.status`, `trust_state`, `hidden_test_cases` or `expected_output`,
and never runs an oracle. It moves one row through the states P2.7d already
defined, using P2.7d's own methods, so the database constraints stay the
authority rather than being re-implemented here.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from groups.management.commands import _preimage_ops as ops
from groups.models import ReferenceSolution

#: Actions that change state. Everything else is read-only, which is the
#: default: running the command with no action lists candidates and writes
#: nothing.
MUTATING_ACTIONS = {"submit", "approve", "reject", "activate", "deactivate"}

#: Terminal transitions. APPROVED has no un-approve and REJECTED has no
#: reopen (P2.7d: a reference is superseded, never edited), so both are one-way
#: doors and get a second explicit confirmation.
IRREVERSIBLE_ACTIONS = {"approve", "reject"}


def _topic_name(question):
    """
    The topic's name, read through `default`.

    NOT joined from the reference's own connection: the oracle role holds
    SELECT on `groups_question` and nothing on `groups_topic`, so a
    `select_related("question__topic")` made every read of a reference fail
    with `permission denied for table groups_topic`. The id is on the row; the
    name is a heading.
    """
    if not question.topic_id:
        return "-"
    from groups.models import Topic
    topic = Topic.objects.filter(pk=question.topic_id).first()
    return topic.name if topic else f"#{question.topic_id}"


def _approver_name(reference):
    """
    The approver's username, read through `default`.

    NOT through the reference's own connection: the oracle role holds no
    privilege on the user table, so following the FK there would make an
    ordinary listing fail on a permission error. The id is authoritative and
    lives on the row; the name is a display convenience.
    """
    if not reference.approved_by_id:
        return "-"
    user = get_user_model().objects.filter(pk=reference.approved_by_id).first()
    return user.username if user else f"#{reference.approved_by_id}"


class Command(BaseCommand):
    help = "Inspect and approve reference solutions (operator workflow)."

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            choices=["list", "inspect", "submit", "approve", "reject",
                     "activate", "deactivate"],
            nargs="?", default="list",
            help="Default 'list' is read-only.")
        parser.add_argument("reference_id", nargs="?", type=int, default=None)
        parser.add_argument(
            "--operator", type=str, default=None,
            help="Username of the staff account performing the action.")
        parser.add_argument(
            "--show-source", action="store_true",
            help="Print the reference source during `inspect`. Withheld unless "
                 "asked for, because a reviewer often only needs the metadata.")
        parser.add_argument(
            "--confirm", action="store_true",
            help="Required for irreversible transitions (approve, reject).")
        parser.add_argument(
            "--state", type=str, default=None,
            help="Filter `list` by review state.")
        parser.add_argument(
            "--alias", default="default",
            help="Database connection. `oracle` on production; the reference "
                 "is read and written through it, with no fallback.")


    # ── entry point ──────────────────────────────────────────

    def handle(self, *args, **options):
        action = options["action"]
        alias = options["alias"]

        if action == "list":
            return self._list(options["state"], alias)

        reference_id = options["reference_id"]
        if reference_id is None:
            raise CommandError(f"'{action}' needs a reference id.")

        # The gate runs for the MUTATING actions only: `inspect` reads, and a
        # reviewer must be able to read a candidate through any connection they
        # are entitled to.
        if action not in ("inspect",):
            identity = ops.describe_target(alias)
            if identity["is_production"]:
                ops.gate_writing_role(alias, allowed=ops.ALLOWED_ORACLE_ROLES)
                ops.gate_write_privilege(
                    alias, required=ops.REFERENCE_WRITE_PROBE,
                    forbidden=ops.ORACLE_FORBIDDEN)
            else:
                ops.gate_write_privilege(alias,
                                         required=ops.REFERENCE_WRITE_PROBE)

        reference = self._get_reference(reference_id, alias)

        # Authorisation for EVERY action but `list` — including `inspect`,
        # which can print grading truth to a terminal.
        operator = self._authorised_operator(options["operator"], action)

        if action == "inspect":
            return self._inspect(reference, operator, options["show_source"])

        if action in IRREVERSIBLE_ACTIONS and not options["confirm"]:
            raise CommandError(
                f"'{action}' is irreversible — APPROVED cannot be un-approved "
                f"and REJECTED cannot be reopened (a reference is superseded, "
                f"never edited). Re-run with --confirm.")

        return self._transition(action, reference, operator)

    # ── authorisation ────────────────────────────────────────

    def _authorised_operator(self, username, action):
        """
        Resolve and authorise the operator.

        `is_staff` is this repository's existing operator flag — it gates the
        MLOps telemetry endpoint and Django admin, and
        `test_authorization_matrix` documents it as exactly that. Reused rather
        than inventing a second authorisation system. `is_active` is Django's
        own account check: a disabled account must not be able to define
        grading truth.

        The `role` field on User is NOT used: nothing in the codebase enforces
        it, so treating it as an authorisation signal here would invent a
        second, unenforced convention.
        """
        if not username:
            raise CommandError(
                f"'{action}' requires --operator <username>. Approval "
                f"provenance names an accountable account; there is no "
                f"anonymous path.")

        User = get_user_model()
        operator = User.objects.filter(username=username).first()
        if operator is None:
            raise CommandError(f"No user named {username!r}.")
        if not operator.is_active:
            raise CommandError(
                f"{username!r} is disabled and cannot review reference "
                f"solutions.")
        if not operator.is_staff:
            raise CommandError(
                f"{username!r} is not staff. Reference solutions are grading "
                f"truth; approving one requires the operator flag this "
                f"project already uses for admin surfaces.")
        return operator

    def _get_reference(self, reference_id, alias="default"):
        # `approved_by` is NOT select_related: the operator alias holds no
        # privilege on the user table, and a join would make every read of a
        # reference depend on one.
        reference = (ReferenceSolution.objects.using(alias)
                     .select_related("question")
                     .filter(pk=reference_id).first())
        if reference is None:
            raise CommandError(f"No reference solution with id {reference_id}.")
        return reference

    # ── read-only ────────────────────────────────────────────

    def _list(self, state, alias="default"):
        queryset = ReferenceSolution.objects.using(alias).select_related(
            "question")
        if state:
            queryset = queryset.filter(review_state=state.upper())
        rows = list(queryset.order_by("review_state", "pk"))

        if not rows:
            self.stdout.write(self.style.WARNING(
                "No reference solutions match." if state else
                "No reference solutions exist yet. Nothing in the question "
                "bank can become ORACLE_VERIFIED until at least one is "
                "written, reviewed and approved."))
            return

        # NO source_code in list output, by construction: a reviewer browsing
        # candidates has no need of it, and the less often grading truth is
        # printed the fewer places it can be captured.
        self.stdout.write(
            f"{'id':>6}  {'question':>8}  {'language':<12}{'state':<12}"
            f"{'active':<8}{'provenance':<12}{'approved by':<16}title")
        self.stdout.write("-" * 100)
        for reference in rows:
            provenance = ("intact" if reference.has_valid_approval_provenance
                          else ("-" if reference.review_state !=
                                ReferenceSolution.REVIEW_APPROVED else "BROKEN"))
            approver = _approver_name(reference)
            self.stdout.write(
                f"{reference.pk:>6}  {reference.question_id:>8}  "
                f"{reference.language:<12}{reference.review_state:<12}"
                f"{('yes' if reference.is_active else 'no'):<8}"
                f"{provenance:<12}{approver[:15]:<16}"
                f"{reference.question.title[:32]}")

        self.stdout.write("")
        self._print_counts(rows)

    def _print_counts(self, rows):
        counts = {}
        for reference in rows:
            counts[reference.review_state] = counts.get(reference.review_state, 0) + 1
        summary = "  ".join(f"{state}={n}" for state, n in sorted(counts.items()))
        active = sum(1 for r in rows if r.is_active)
        canonical = sum(1 for r in rows if r.is_canonical)
        self.stdout.write(f"  {summary}   active={active}  canonical={canonical}")

    def _inspect(self, reference, operator, show_source):
        question = reference.question
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nReference {reference.pk} — question {question.pk}"))
        for label, value in [
            ("question", f"{question.pk} · {question.title}"),
            ("topic", _topic_name(question)),
            ("language", reference.language),
            ("review state", reference.review_state),
            ("is_active", reference.is_active),
            ("canonical", reference.is_canonical),
            ("provenance intact", reference.has_valid_approval_provenance),
            ("approved by", _approver_name(reference)),
            ("approved at", reference.approved_at or "-"),
            ("source hash", reference.source_hash or "-"),
            ("source length", f"{len(reference.source_code)} chars"),
            ("created", reference.created_at),
        ]:
            self.stdout.write(f"  {label:<20} {value}")

        if show_source:
            # Explicitly requested, and only ever to a terminal. This command
            # builds no HTTP response, which is what keeps the P2.5 secrecy
            # guarantee structural rather than a permission check.
            self.stdout.write(self.style.WARNING(
                f"\n  --- source (grading truth; requested by "
                f"{operator.username}) ---"))
            for line in reference.source_code.splitlines():
                self.stdout.write(f"  | {line}")
            self.stdout.write(self.style.WARNING("  --- end source ---"))
        else:
            self.stdout.write(self.style.WARNING(
                "\n  Source withheld. Re-run with --show-source to read it — "
                "which you must do before approving."))

        self.stdout.write("")
        self.stdout.write(f"  next: {self._next_step(reference)}")

    def _next_step(self, reference):
        state = reference.review_state
        if state == ReferenceSolution.REVIEW_DRAFT:
            return "submit (DRAFT -> IN_REVIEW)"
        if state == ReferenceSolution.REVIEW_IN_REVIEW:
            return "approve --confirm, or reject --confirm"
        if state == ReferenceSolution.REVIEW_REJECTED:
            return "terminal — write a new reference instead"
        if not reference.is_active:
            return "activate (make it the canonical oracle)"
        return "already canonical"

    # ── transitions ──────────────────────────────────────────

    def _transition(self, action, reference, operator):
        """
        Every transition goes through P2.7d's own lifecycle methods.

        Deliberately NOT `objects.update(review_state=..., is_active=True)`:
        that would skip the ordering rules, skip the provenance stamping, and
        leave the database CHECK constraints as the only thing standing
        between an operator and a broken row. The methods are the workflow;
        the constraints are the backstop.
        """
        before = (reference.review_state, reference.is_active)
        try:
            if action == "submit":
                reference.submit_for_review()
            elif action == "approve":
                reference.approve(by=operator)
            elif action == "reject":
                reference.reject()
            elif action == "activate":
                reference.activate()
            elif action == "deactivate":
                reference.deactivate()
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))

        reference.refresh_from_db()
        after = (reference.review_state, reference.is_active)

        self.stdout.write(self.style.SUCCESS(
            f"reference {reference.pk} (question {reference.question_id}): "
            f"{before[0]}/active={before[1]} -> {after[0]}/active={after[1]}"))

        if action == "approve":
            self.stdout.write(
                f"  approved_by  {operator.username}\n"
                f"  approved_at  {reference.approved_at}\n"
                f"  source_hash  {reference.source_hash}")
            self.stdout.write(self.style.WARNING(
                "  The source is now frozen: a database constraint recomputes "
                "its digest, so it cannot be edited while approved. Supersede "
                "it with a new reference instead.\n"
                "  APPROVED is not yet canonical — run `activate` to select it "
                "as the oracle."))
