"""
Oracle execution and reconciliation pipeline (M2 P2.7g-2).

Turns an APPROVED canonical reference into *evidence*. It does not turn
evidence into truth: this module never writes `expected_output`, never sets
`Question.status` or `trust_state`, and contains no code path that could. A
structural test enforces that.

── Where this sits ─────────────────────────────────────────────────────────

    APPROVED REFERENCE -> ACTIVE -> **EXECUTION** -> **DETERMINISM** ->
    **PROVENANCE** -> **RECONCILIATION** -> quality gate -> human question
    review -> ORACLE_VERIFIED

The four bold steps are this phase. Everything after the arrow from
reconciliation is deliberately absent, and the last one — human question
review — has no mechanism anywhere in the repository. Inventing one would be
inventing the authority that makes grading truth trustworthy, so it is
reported instead.

── Determinism is hoisted, not skipped ─────────────────────────────────────

`OracleService.run` verifies determinism internally by running twice and
discarding the second result. This pipeline instead calls it twice with
`verify_determinism=False` and compares the two itself. That is STRICTLY
STRONGER, not weaker: both runs are recorded as provenance, so the evidence
that a result was reproducible is itself durable rather than transient. The
P2.7d contract — "the ORACLE_VERIFIED transition must not be reachable from a
call that passed verify_determinism=False" — is honoured, because the
comparison still happens and nothing here reaches ORACLE_VERIFIED at all.
"""

from dataclasses import asdict, dataclass, field

from groups import execution_contract, hidden_tests, provenance
from groups.models import OracleExecution, Question, ReferenceSolution
from groups.oracle import (
    OracleError, OracleFailed, OracleService, OracleUnapproved, OracleUnavailable,
)
from groups.utils import normalize_output

# ── Case outcomes ───────────────────────────────────────────────────────────
CASE_OK = "OK"
CASE_NONDETERMINISTIC = "NONDETERMINISTIC"
CASE_EXECUTION_FAILED = "EXECUTION_FAILED"
CASE_UNAVAILABLE = "UNAVAILABLE"
CASE_REFUSED = "REFUSED"

# ── Reconciliation verdicts ────────────────────────────────────────────────
RECON_AGREEMENT = "AGREEMENT"
RECON_CONFLICT = "CONFLICT"
RECON_ABSENT = "ABSENT"
RECON_UNAVAILABLE = "UNAVAILABLE"

#: Every authoritative output requires this many independent, agreeing runs.
#: Two is the minimum that can detect disagreement at all. It does not prove
#: determinism — a one-in-N flake survives it — and the reports say so rather
#: than implying more confidence than two samples support.
REQUIRED_RUNS = 2


@dataclass
class CaseResult:
    case_index: int
    case_digest: str
    outcome: str
    oracle_output: str = None
    output_digest: str = None
    reconciliation: str = None
    existing_output_digest: str = None
    provenance_ids: list = field(default_factory=list)
    detail: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass
class OracleRunReport:
    question_id: int
    reference_id: int = None
    reference_source_hash: str = None
    language: str = None
    execution_contract_version: str = None
    dry_run: bool = True

    reference_blockers: list = field(default_factory=list)
    question_blockers: list = field(default_factory=list)

    cases: list = field(default_factory=list)
    agreements: int = 0
    conflicts: int = 0
    absent: int = 0
    failed_cases: int = 0

    eligible: bool = False
    ready_for_quality_gate: bool = False
    blockers: list = field(default_factory=list)

    def as_dict(self):
        data = asdict(self)
        data["cases"] = [c.as_dict() for c in self.cases]
        return data


# ═════════════════════════════════════════════════════════════
# Eligibility
# ═════════════════════════════════════════════════════════════

def check_reference_eligible(question, reference):
    """
    Blockers preventing this reference from acting as an oracle. Empty = eligible.

    Canonicality is the strongest predicate and is NOT inferred from
    `review_state == APPROVED`: an approved-but-inactive reference is a
    legitimate resting state (superseded, or approved in a non-canonical
    language) and must not silently define answers.
    """
    blockers = []

    if reference.question_id != question.pk:
        blockers.append(
            f"reference {reference.pk} belongs to question "
            f"{reference.question_id}, not question {question.pk}")
        # Everything below would describe the wrong problem's reference.
        return blockers

    if reference.review_state != ReferenceSolution.REVIEW_APPROVED:
        blockers.append(f"reference is {reference.review_state}, not APPROVED")

    if not reference.is_active:
        blockers.append(
            "reference is not active; an approved-but-superseded "
            "implementation must not define answers")

    if not reference.has_valid_approval_provenance:
        blockers.append(
            "approval provenance is broken — the source no longer matches the "
            "hash that was approved")

    if reference.language and reference.language.lower() not in _known_languages():
        blockers.append(f"language {reference.language!r} has no executor mapping")

    try:
        # Via the canonical resolver rather than re-deriving the default, so a
        # question graded under v1-by-omission is oracled under v1 too.
        execution_contract.contract_version(question)
    except execution_contract.UnknownExecutionContract as exc:
        blockers.append(str(exc))

    return blockers


def _known_languages():
    from common import languages
    return set(languages.LANGUAGE_IDS)


def check_question_eligible(question):
    """
    Blockers preventing this question from being oracle-executed at all.

    Deliberately does NOT require `trust_state == ORACLE_VERIFIED` — producing
    the evidence that could eventually justify that is the entire point. It
    does require the suite to be structurally sound, because executing against
    malformed cases produces outputs nobody can interpret.
    """
    blockers = []
    cases = question.hidden_test_cases

    if not isinstance(cases, list) or not cases:
        blockers.append("question has no hidden test cases")
        return blockers

    problems = hidden_tests.validate_suite(cases)
    malformed = [p for p in problems
                 if p.index is not None and "duplicate" not in p.message]
    if malformed:
        blockers.append(
            f"{len(malformed)} hidden case(s) violate the contract: "
            + "; ".join(sorted(str(p) for p in malformed[:5])))

    duplicates = _normalized_duplicates(cases)
    if duplicates:
        blockers.append(
            f"{len(duplicates)} duplicate input(s) under normalized comparison")

    if len(cases) < hidden_tests.MIN_HIDDEN_TESTS:
        # A FLOOR breach is recorded but does not stop execution: gathering
        # evidence about an under-covered question is still useful, and the
        # coverage bar belongs to the P2.7h-1 gate that runs afterwards.
        blockers.append(
            f"{len(cases)} hidden tests, below the floor of "
            f"{hidden_tests.MIN_HIDDEN_TESTS} (advisory at this stage)")

    if question.status == Question.STATUS_BLOCKED:
        blockers.append("question is BLOCKED")

    return blockers


def _normalized_duplicates(cases):
    seen, duplicates = {}, []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("stdin"), str):
            continue
        key = normalize_output(case["stdin"])
        if key in seen:
            duplicates.append((index, seen[key]))
        else:
            seen[key] = index
    return duplicates


def _blocking(blockers):
    """Advisory blockers do not stop execution; everything else does."""
    return [b for b in blockers if "advisory" not in b]


# ═════════════════════════════════════════════════════════════
# Execution
# ═════════════════════════════════════════════════════════════

def execute_case(question, reference, case, index, service, *,
                 execution_contract_version, executor=None, record=False,
                 using=None):
    """
    Execute one case REQUIRED_RUNS times and reconcile the result.

    Every run is recorded as provenance when `record` is set — including the
    runs that failed or disagreed. A blocked case is evidence too, and the
    reason a later reviewer can tell "we never tried" from "we tried and it
    would not settle".
    """
    stdin = case.get("stdin", "")
    digest = provenance.case_identity(stdin)
    result = CaseResult(case_index=index, case_digest=digest, outcome=CASE_OK)

    outputs = []
    for run in range(REQUIRED_RUNS):
        try:
            # verify_determinism=False because THIS function performs the
            # comparison, across runs that are each recorded. See the module
            # docstring: hoisted, not skipped.
            output = service.run(question, reference, stdin,
                                 verify_determinism=False)
        except OracleUnapproved as exc:
            result.outcome = CASE_REFUSED
            result.detail = str(exc)
            return result
        except OracleUnavailable as exc:
            result.outcome = CASE_UNAVAILABLE
            result.detail = str(exc)
            _record(record, question, reference, stdin, "",
                    OracleExecution.STATUS_ERROR, execution_contract_version,
                    executor, result, using)
            return result
        except OracleFailed as exc:
            result.outcome = CASE_EXECUTION_FAILED
            result.detail = str(exc)
            _record(record, question, reference, stdin, "",
                    OracleExecution.STATUS_FAILED, execution_contract_version,
                    executor, result, using)
            return result
        except OracleError as exc:
            result.outcome = CASE_EXECUTION_FAILED
            result.detail = f"{type(exc).__name__}: {exc}"
            return result

        outputs.append(output)

    if len(set(outputs)) != 1:
        # NO majority vote, NO first-wins, NO overwrite. Whatever this
        # problem's answer is, it is not a function of its input alone, so no
        # single stored value can be correct.
        result.outcome = CASE_NONDETERMINISTIC
        result.detail = (f"{REQUIRED_RUNS} identical runs disagreed: "
                         f"{[o[:40] for o in outputs]}")
        _record(record, question, reference, stdin, outputs[0],
                OracleExecution.STATUS_NONDETERMINISTIC,
                execution_contract_version, executor, result, using)
        return result

    output = outputs[0]
    result.oracle_output = output
    result.output_digest = provenance.output_identity(output)

    for _ in range(REQUIRED_RUNS):
        _record(record, question, reference, stdin, output,
                OracleExecution.STATUS_SUCCESS, execution_contract_version,
                executor, result, using)

    result.reconciliation, result.existing_output_digest = reconcile_case(
        case, output)
    return result


def _record(enabled, question, reference, stdin, output, status,
            contract_version, executor, result, using=None):
    if not enabled:
        return
    execution = provenance.record_execution(
        question=question, reference=reference, stdin=stdin,
        produced_output=output, status=status,
        execution_contract_version=contract_version, executor=executor or {},
        using=using)
    result.provenance_ids.append(execution.pk)


# ═════════════════════════════════════════════════════════════
# Reconciliation
# ═════════════════════════════════════════════════════════════

def reconcile_case(case, oracle_output):
    """
    (verdict, existing_digest) comparing the stored answer with the oracle's.

    Compared with `normalize_output` — the grader's own comparator — because a
    reconciliation looser or stricter than grading would report agreements the
    learner does not experience.

    ── What ABSENT means, precisely ────────────────────────────────────────

    `expected_output` is a REQUIRED field (`hidden_tests.REQUIRED_FIELDS`), so
    a case that omits the key entirely is malformed and never reaches this
    function — `check_question_eligible` blocks the whole question first. ABSENT
    therefore means the key is present with an empty value: the placeholder
    shape of a case awaiting generation, which is a legitimate state, as
    opposed to a case whose schema is simply broken.

    A CONFLICT is NEVER resolved here. Two answers disagree; deciding which is
    right requires a human who can read the problem statement.
    """
    existing = case.get("expected_output")
    if existing is None or not str(existing).strip():
        return RECON_ABSENT, None

    existing_norm = normalize_output(str(existing))
    existing_digest = provenance.output_identity(existing_norm)

    if existing_norm == normalize_output(oracle_output):
        return RECON_AGREEMENT, existing_digest
    return RECON_CONFLICT, existing_digest


# ═════════════════════════════════════════════════════════════
# Orchestration
# ═════════════════════════════════════════════════════════════

def run_question(question, runner, *, record=False, executor=None,
                 using=None):
    """
    Execute every hidden case of one question against its canonical reference.

    DRY RUN by default: `record=False` writes nothing at all. Even with
    `record=True` the only thing written is provenance — never
    `expected_output`, never `status`, never `trust_state`.
    """
    from groups.oracle import canonical_reference, canonical_reference_problem

    report = OracleRunReport(question_id=question.pk, dry_run=not record)

    reference = canonical_reference(question)
    if reference is None:
        # Covers zero active references, several active ones (the
        # multi-language ambiguity), and an active one whose provenance broke.
        report.reference_blockers.append(
            canonical_reference_problem(question) or "no canonical reference")
        report.blockers.append("no single canonical reference")
        return report

    report.reference_id = reference.pk
    report.reference_source_hash = reference.source_hash
    report.language = reference.language
    try:
        contract = execution_contract.contract_version(question)
    except execution_contract.UnknownExecutionContract as exc:
        report.reference_blockers.append(str(exc))
        report.blockers.append(str(exc))
        return report
    report.execution_contract_version = contract

    report.reference_blockers = check_reference_eligible(question, reference)
    report.question_blockers = check_question_eligible(question)

    blocking = _blocking(report.reference_blockers) + _blocking(
        report.question_blockers)
    if blocking:
        report.blockers.extend(blocking)
        return report

    report.eligible = True
    service = OracleService(runner)

    for index, case in enumerate(question.hidden_test_cases):
        result = execute_case(
            question, reference, case, index, service,
            execution_contract_version=contract, executor=executor,
            record=record, using=using)
        report.cases.append(result)

    report.agreements = sum(1 for c in report.cases
                            if c.reconciliation == RECON_AGREEMENT)
    report.conflicts = sum(1 for c in report.cases
                           if c.reconciliation == RECON_CONFLICT)
    report.absent = sum(1 for c in report.cases
                        if c.reconciliation == RECON_ABSENT)
    report.failed_cases = sum(1 for c in report.cases if c.outcome != CASE_OK)

    if report.failed_cases:
        report.blockers.append(
            f"{report.failed_cases} case(s) did not produce a settled output")
    if report.conflicts:
        report.blockers.append(
            f"{report.conflicts} case(s) CONFLICT with the stored "
            f"expected_output — a human must decide which is correct")

    # "Ready for the quality gate" is the furthest this phase can go. It is
    # not readiness for ORACLE_VERIFIED: that needs the P2.7h-1 gate and a
    # human question review that does not exist yet.
    report.ready_for_quality_gate = (
        report.eligible and not report.failed_cases and not report.conflicts
        and bool(report.cases))
    return report
