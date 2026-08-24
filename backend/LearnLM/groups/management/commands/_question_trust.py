"""
Shared plumbing for the question trust commands (M2 P2.7g-3).

`question_review`, `question_approve` and `question_promote` must agree on who
may act, what the artifact is, and what blocks it. Three copies of that logic
would drift, and the drift would show up as one command permitting what
another refuses — with trust promotion on the permissive side.

Nothing here writes.
"""

import json
import pathlib

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError

from django.db import DEFAULT_DB_ALIAS

from groups import question_artifact
from groups.models import Question
from groups.oracle import canonical_reference, canonical_reference_problem


def resolve_operator(username):
    """
    The acting staff user, or a CommandError.

    `is_staff + is_active` — the repository's existing operator flag, the same
    pair `reference_review` uses (M2 P2.7d-2). Deliberately NOT `User.role`,
    which is listed in `UserSerializer.fields` on an AllowAny registration
    endpoint and is therefore self-assignable by anyone with a browser; and
    deliberately not `is_superuser`, which would silently make the approving
    population smaller than the operator population that already approves
    references.
    """
    if not username:
        raise CommandError("--operator is required")
    user = get_user_model().objects.filter(username=username).first()
    if user is None:
        raise CommandError(f"no such user: {username}")
    if not user.is_active:
        raise CommandError(f"{username} is not an active account")
    if not user.is_staff:
        raise CommandError(
            f"{username} is not staff; question approval is an operator "
            f"action")
    return user


def resolve_question(question_id, alias=DEFAULT_DB_ALIAS):
    """
    The question, read through the SELECTED connection.

    `alias` defaults to `default` so existing callers and tests are unaffected;
    the operator commands pass their own. Reading a question from one
    connection and writing its approval to another is how a row ends up
    attached to something that is not there.
    """
    question = Question.objects.using(alias).filter(pk=question_id).first()
    if question is None:
        raise CommandError(f"no such question: {question_id}")
    return question


def resolve_reference(question):
    """The canonical reference, or a CommandError naming why there isn't one."""
    reference = canonical_reference(question)
    if reference is None:
        raise CommandError(
            f"question {question.pk} has no canonical reference: "
            f"{canonical_reference_problem(question) or 'unknown'}")
    return reference


def load_quality_outcome(path):
    """Read an operator-supplied P2.7h-1 report into a QualityOutcome."""
    if not path:
        raise CommandError("--quality-report is required")
    location = pathlib.Path(path)
    if not location.is_file():
        raise CommandError(f"no such quality report: {path}")
    try:
        data = json.loads(location.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CommandError(f"quality report is not valid JSON: {exc}")
    try:
        return question_artifact.QualityOutcome.from_mapping(data)
    except (ValueError, TypeError) as exc:
        raise CommandError(f"quality report is malformed: {exc}")


def build(question, reference, quality, using=None):
    """
    The assembled artifact. Reads only.

    `using` names the connection the oracle evidence is read from. Passed
    explicitly by the operator commands so the whole artifact — question,
    reference and provenance — is assembled from ONE database.
    """
    return question_artifact.build_artifact(question, reference, quality,
                                            using=using)


def render_artifact(command, question, reference, artifact, *, show_source=False):
    """
    The reviewer's view of the artifact.

    Reference source is withheld unless explicitly requested, matching the
    `reference_review --show-source` convention: the source is the answer key,
    and printing it into a terminal scrollback by default is how answer keys
    end up in shell history and CI logs.
    """
    style = command.style
    write = command.stdout.write

    write(style.MIGRATE_HEADING(
        f"Question {question.pk} — {question.title}"))
    write(f"  status              {question.status}")
    write(f"  trust_state         {question.trust_state}")
    write(f"  execution contract  {artifact.execution_contract_version}")
    write(f"  languages           "
          f"{', '.join(sorted(artifact.boilerplate_code or {})) or '(none)'}")
    write("")

    write(style.MIGRATE_HEADING("Reference"))
    write(f"  id                  {reference.pk}")
    write(f"  language            {reference.language}")
    write(f"  review_state        {reference.review_state}")
    write(f"  is_active           {reference.is_active}")
    write(f"  source_hash         {reference.source_hash}")
    write(f"  approved_by         {reference.approved_by_id}")
    write(f"  approved_at         {reference.approved_at}")
    if show_source:
        write("")
        write(style.WARNING("  --- reference source (answer key) ---"))
        for line in (reference.source_code or "").splitlines():
            write(f"  | {line}")
    write("")

    write(style.MIGRATE_HEADING(
        f"Oracle evidence — {len(artifact.cases)} case(s)"))
    for index, case in enumerate(artifact.cases, start=1):
        if case.is_oracle_backed:
            label = style.SUCCESS("ORACLE_BACKED")
        elif not case.oracle_output_digest:
            label = style.ERROR("NO EVIDENCE   ")
        else:
            label = style.WARNING("LEGACY/CONFLICT")
        write(f"  case {index:>3}  {label}  "
              f"case={case.case_digest[:12]} "
              f"stored={(case.expected_digest or '-')[:12]} "
              f"oracle={(case.oracle_output_digest or '-')[:12]} "
              f"runs={case.agreeing_runs}")

    legacy = artifact.legacy_cases
    if legacy:
        write("")
        write(style.WARNING(
            f"  {len(legacy)} case(s) are NOT oracle-backed. Their stored "
            f"expected_output predates the oracle or disagrees with it. They "
            f"stay UNVERIFIED — matching a value is not evidence of it."))
    write("")

    quality = artifact.quality
    write(command.style.MIGRATE_HEADING("Quality gate (P2.7h-1)"))
    write(f"  tier 1 kill rate    {quality.tier1_kill_rate}")
    write(f"  tier 2 kill rate    {quality.tier2_kill_rate}")
    write(f"  mutant set digest   {quality.mutant_digest[:16]}")
    write(f"  verdict             "
          f"{'PASS' if quality.passed else 'FAIL'}")
    for blocker in quality.blockers:
        write(style.WARNING(f"    blocker: {blocker}"))
    write("")

    if artifact.blockers:
        write(style.ERROR("BLOCKERS — this artifact cannot be approved:"))
        for blocker in artifact.blockers:
            write(style.ERROR(f"  • {blocker}"))
    else:
        write(style.SUCCESS("No blockers. This artifact is approvable."))
    write("")

    write(style.MIGRATE_HEADING("Artifact digest"))
    write(f"  schema version      {artifact.schema_version}")
    write(f"  digest              {artifact.digest()}")
