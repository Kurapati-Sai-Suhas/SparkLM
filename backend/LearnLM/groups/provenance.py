"""
Output provenance (M2 P2.7g-1).

The foundation for the last link in the trust chain:

    APPROVED REFERENCE -> ACTIVE -> EXECUTION -> **PROVENANCE** ->
    expected_output -> oracle agreement -> quality gate -> human approval ->
    ORACLE_VERIFIED

This module records that an execution HAPPENED. It does not decide that the
output is correct, and it cannot: nothing here writes `Question.status`,
`trust_state`, `adaptive_eligible`, `hidden_test_cases` or `expected_output`.
A structural test enforces that, so the boundary is a property of the code
rather than a promise about it.

── Why provenance has to exist before any output is generated ──────────────

P2.7d's F5 analysis established that a reference may later be found wrong. The
only way to answer "which stored answers came from it?" is to have recorded
the link at generation time. Recorded afterwards it is a guess; recorded never,
a revocation cannot be scoped and the honest response to one bad reference
becomes distrusting the whole bank.
"""

import hashlib

from django.core.exceptions import ValidationError
from django.utils import timezone

from groups.models import OracleExecution, ReferenceSolution, compute_source_hash
from groups.utils import normalize_output


def effective_input(stdin):
    """
    The bytes an executor actually receives.

    Both `GradingService.grade` and `OracleService._execute` convert a literal
    two-character backslash-n into a real newline before running, so the stored
    stdin is not the executed input. Provenance has to record what RAN, or it
    cannot reproduce anything.
    """
    return (stdin or "").replace("\\n", "\n")


def case_identity(stdin):
    """
    Stable identity of a hidden-test CASE, independent of array position.

    `hidden_test_cases` is a JSON list with no per-case id, so position is the
    only handle the schema offers — and position changes the moment anyone
    reorders or inserts. This digests `normalize_output(stdin)`, which is the
    definition `reseed_questions` and the P2.7h-1 quality gate already use for
    duplicate detection.

    Deliberately NOT a new definition. The repository holds two notions of
    "same input" — `hidden_tests.validate_suite` compares raw stdin, everything
    else compares normalized — and this reuses the normalized one rather than
    minting a third. Unifying the two is a separate change with its own blast
    radius; see the P2.7g-1 report.
    """
    return hashlib.sha256(
        normalize_output(stdin or "").encode("utf-8")).hexdigest()


def input_identity(stdin):
    """
    Fingerprint of the exact executed bytes — the reproducibility handle.

    Distinct from `case_identity` on purpose: two stored cases differing only
    by a literal backslash-n execute identically, so they share an
    `input_digest` while remaining separable as cases.
    """
    return hashlib.sha256(effective_input(stdin).encode("utf-8")).hexdigest()


def output_identity(output):
    """Fingerprint of a produced output. Matches the database CHECK exactly."""
    return hashlib.sha256((output or "").encode("utf-8")).hexdigest()


def record_execution(*, question, reference, stdin, produced_output, status,
                     execution_contract_version, executor=None,
                     executed_at=None, is_authoritative=False):
    """
    Record one execution. Returns the created `OracleExecution`.

    Refuses, before writing anything:

      * a reference belonging to a different question — the cross-question
        defect P2.7d's review found in OracleService, restated here because
        provenance that crossed questions would attribute an answer to a
        problem it was never written for;
      * a reference with no `source_hash`, i.e. one that was never approved.
        An execution of an unapproved implementation is not evidence of
        anything and must not be recorded as though it were.

    It does NOT require the reference to be currently active. A superseded
    reference's history is exactly what a revocation needs to read.
    """
    if reference.question_id != question.pk:
        raise ValidationError(
            f"reference {reference.pk} belongs to question "
            f"{reference.question_id}, not question {question.pk}; provenance "
            f"may not cross questions")

    if reference.review_state != ReferenceSolution.REVIEW_APPROVED:
        raise ValidationError(
            f"reference {reference.pk} is {reference.review_state}; only an "
            f"APPROVED reference may produce recorded output")

    if not reference.source_hash:
        raise ValidationError(
            f"reference {reference.pk} has no source hash; its provenance "
            f"could not be pinned to a revision")

    # Recomputed rather than copied: if the stored hash and the stored source
    # ever disagreed, this records the truth about what ran. The P2.7d database
    # constraint makes that disagreement unwritable, so agreement here is a
    # cheap confirmation rather than a second source of truth.
    actual_hash = compute_source_hash(reference.source_code)
    if actual_hash != reference.source_hash:
        raise ValidationError(
            f"reference {reference.pk} source does not match its approved "
            f"hash; refusing to attribute output to an unverifiable revision")

    execution = OracleExecution(
        question=question,
        reference=reference,
        reference_source_hash=actual_hash,
        language=reference.language,
        case_digest=case_identity(stdin),
        input_digest=input_identity(stdin),
        produced_output=produced_output or "",
        output_digest=output_identity(produced_output),
        execution_contract_version=execution_contract_version,
        status=status,
        executed_at=executed_at or timezone.now(),
        executor=executor or {},
        provenance_schema_version=OracleExecution.PROVENANCE_SCHEMA_VERSION,
        is_authoritative=is_authoritative,
    )
    execution.full_clean(exclude=["produced_output"])
    execution.save()
    return execution


def outputs_produced_by(reference):
    """
    Every execution attributable to this reference — the revocation query.

    Indexed on `(reference, -executed_at)`, so scoping a revocation is a lookup
    rather than a scan of free text. P2.7g-1 only has to make this ANSWERABLE;
    acting on it is P2.7g-2's job.
    """
    return (OracleExecution.objects
            .filter(reference=reference)
            .order_by("-executed_at", "pk"))


def outputs_produced_by_source_hash(source_hash):
    """
    Every execution from a specific reference REVISION.

    A question may have had several references over time; revoking one
    revision must not implicate the others, and the denormalised hash is what
    makes that distinction possible after the fact.
    """
    return (OracleExecution.objects
            .filter(reference_source_hash=source_hash)
            .order_by("-executed_at", "pk"))


def authoritative_output(question, stdin):
    """
    The accepted output for one case, or None.

    This is the join P2.7g-2 will use to say "this expected_output is backed by
    provenance record X". P2.7g-1 provides the lookup and writes nothing.
    """
    return (OracleExecution.objects
            .filter(question=question, case_digest=case_identity(stdin),
                    is_authoritative=True)
            .first())
