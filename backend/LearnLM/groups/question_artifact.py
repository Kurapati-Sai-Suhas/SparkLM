"""
The grading artifact and its digest (M2 P2.7g-3).

A human approving a question as ORACLE_VERIFIED is not approving a solution.
They are approving a COMPOSITE spanning three tables — the problem statement,
the harness that grades it, every hidden case, the reference that produced the
answers, the provenance proving it ran, and the quality gate's verdict. This
module turns that composite into one 64-character digest.

── Why a digest rather than a change log ───────────────────────────────────

The alternative is a list of triggers: "invalidate approval when the statement
changes, when a hidden test changes, when the reference changes, when...".
Every such list is one forgotten field away from a stale approval silently
staying valid, and the forgotten field is discovered by a learner being graded
against an answer nobody approved.

Recomputation has no such list. `question_promote` rebuilds the digest from
live state and demands it equal the approved one, so ANY change to ANY
participating field is caught by construction — including changes made by code
that did not exist when this module was written.

── What is snapshotted and what is checked live ────────────────────────────

CONTENT is snapshotted here. LIFECYCLE is not.

`ReferenceSolution.is_active` is deliberately absent from the digest even
though a deactivated reference must block promotion. Activity is legitimately
toggled — a reference can be deactivated and reactivated with no change to
what it computes — so freezing it would invalidate approvals over a no-op.
Promotion checks canonicality live, against the database, at the moment of
promotion. Content is proven by digest; lifecycle is proven by lookup.

── Canonical encoding ──────────────────────────────────────────────────────

NOT `json.dumps(sort_keys=True)`. JSON leaves too much room: `ensure_ascii`
changes byte length, float repr varies, and dict ordering is only as canonical
as the caller remembered to make it. Two artifacts that differ could encode
identically, which in this module means a changed answer key inheriting an old
approval.

Instead every field is emitted as a length-prefixed frame:

    <len(label)>:<label>|<len(value)>:<value>\\n

with lengths counted in UTF-8 BYTES. Because each side is preceded by its own
byte count, no value can forge a frame boundary: a `content` field containing
the literal text `|8:injected` is read as content, never as a new field. Field
ORDER is fixed by the emission sequence in `_frames`, not by dict iteration.

Floats are formatted to a fixed 6-decimal representation rather than `repr`,
which varies across platforms and Python versions.
"""

import hashlib
import json
from dataclasses import dataclass, field

from django.db import DEFAULT_DB_ALIAS

from groups import execution_contract, provenance
from groups.models import OracleExecution, _ARTIFACT_SCHEMA_VERSION
from groups.utils import normalize_output

#: Bumped whenever the participating field set or the encoding changes.
#:
#: It is the FIRST field emitted, so a digest computed under schema 1 can never
#: collide with one computed under schema 2 even if every other field is
#: identical. An approval carries the version it was computed under; promotion
#: recomputes under the current one, so a schema change invalidates every
#: outstanding approval rather than silently reinterpreting it.
#:
#: Defined in `models` so the model default and this cannot drift apart.
ARTIFACT_SCHEMA_VERSION = _ARTIFACT_SCHEMA_VERSION

#: Quality thresholds, re-exported from the P2.7h-1 gate rather than restated,
#: so the bar cannot drift between the module that measures and the module
#: that decides.
from groups.hidden_test_quality import (  # noqa: E402
    TIER1_REQUIRED_KILL_RATE, TIER2_REQUIRED_KILL_RATE,
)


# ═════════════════════════════════════════════════════════════
# Canonical encoding
# ═════════════════════════════════════════════════════════════

def _frame(label, value):
    """One length-prefixed field. See the module docstring for the format."""
    label_bytes = str(label).encode("utf-8")
    value_bytes = _as_text(value).encode("utf-8")
    return (b"%d:%s|%d:%s\n"
            % (len(label_bytes), label_bytes, len(value_bytes), value_bytes))


def _as_text(value):
    """
    A value's canonical text form.

    Strings pass through byte-exact — a problem statement's whitespace is part
    of the problem. Floats get fixed-point formatting because `repr` varies.
    Anything structural is JSON-encoded with every ambiguity pinned; that JSON
    sits INSIDE a length-prefixed frame, so it is a leaf encoding and not the
    canonical representation the module relies on.
    """
    if value is None:
        return "\x00none"
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), default=str)


def digest_of(frames):
    """sha256 over the concatenated frames."""
    hasher = hashlib.sha256()
    for frame in frames:
        hasher.update(frame)
    return hasher.hexdigest()


# ═════════════════════════════════════════════════════════════
# Evidence value objects
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CaseEvidence:
    """What the oracle proved about one hidden case."""
    case_digest: str
    input_digest: str
    expected_digest: str
    oracle_output_digest: str
    agreeing_runs: int

    @property
    def is_oracle_backed(self):
        """
        Whether the STORED answer is the one the oracle produced.

        This is the predicate that keeps legacy data legacy. A pre-existing
        `expected_output` becomes oracle-backed only when an execution exists
        whose output digest matches it — never because it looks plausible, and
        never by being stamped.
        """
        return (bool(self.oracle_output_digest)
                and self.oracle_output_digest == self.expected_digest)


@dataclass(frozen=True)
class QualityOutcome:
    """
    The P2.7h-1 gate's verdict, in the shape the digest consumes.

    Carried as data rather than recomputed at approval time because the gate
    needs a runner and therefore Judge0, and neither approval nor promotion
    should depend on an external service being reachable. The operator
    supplies the report; the approval freezes it; promotion reuses the frozen
    copy rather than accepting a fresh one, so quality evidence cannot be
    swapped between approval and promotion.
    """
    tier1_kill_rate: float = None
    tier2_kill_rate: float = None
    blockers: tuple = ()
    mutant_identifiers: tuple = ()

    @property
    def mutant_digest(self):
        """
        Digest of the mutant SET the gate ran.

        Without it, a gate run against three easy mutants and one against
        forty would produce the same 1.00 kill rate and the same artifact.
        """
        frames = [_frame("mutant", identifier)
                  for identifier in sorted(self.mutant_identifiers)]
        return digest_of(frames)

    @property
    def passed(self):
        if self.blockers:
            return False
        if self.tier1_kill_rate is None or self.tier2_kill_rate is None:
            return False
        return (self.tier1_kill_rate >= TIER1_REQUIRED_KILL_RATE
                and self.tier2_kill_rate >= TIER2_REQUIRED_KILL_RATE)

    @classmethod
    def from_quality_report(cls, report):
        return cls(
            tier1_kill_rate=report.tier1_kill_rate,
            tier2_kill_rate=report.tier2_kill_rate,
            blockers=tuple(sorted(report.blockers)),
            mutant_identifiers=tuple(sorted(
                result.identifier for result in
                (list(getattr(report, "tier1_results", []))
                 + list(getattr(report, "tier2_results", []))))),
        )

    @classmethod
    def from_mapping(cls, data):
        """Rehydrate from the JSON an operator supplies, or from an approval."""
        if not isinstance(data, dict):
            raise ValueError("quality report must be a JSON object")
        return cls(
            tier1_kill_rate=_optional_float(data.get("tier1_kill_rate")),
            tier2_kill_rate=_optional_float(data.get("tier2_kill_rate")),
            blockers=tuple(sorted(str(b) for b in data.get("blockers") or ())),
            mutant_identifiers=tuple(sorted(
                str(m) for m in data.get("mutant_identifiers") or ())),
        )

    def as_dict(self):
        return {"tier1_kill_rate": self.tier1_kill_rate,
                "tier2_kill_rate": self.tier2_kill_rate,
                "blockers": list(self.blockers),
                "mutant_identifiers": list(self.mutant_identifiers)}


def _optional_float(value):
    return None if value is None else float(value)


@dataclass
class QuestionArtifact:
    """Everything a reviewer approves, assembled and ordered."""
    schema_version: int
    question_id: int
    content: str
    execution_contract_version: str
    boilerplate_code: dict
    hidden_wrapper_code: dict
    cases: list                      # CaseEvidence, sorted by case_digest
    reference_id: int
    reference_language: str
    reference_source_hash: str
    quality: QualityOutcome
    blockers: list = field(default_factory=list)

    # ── the encoding ──────────────────────────────────────────

    def _frames(self):
        """
        Every field, in the ONE order that defines this schema.

        Reordering these lines changes every digest in the system. That is the
        intended cost of a schema change and the reason the version is first.
        """
        yield _frame("schema_version", self.schema_version)
        yield _frame("question_id", self.question_id)
        yield _frame("content", self.content)
        yield _frame("execution_contract_version",
                     self.execution_contract_version)

        # Language maps: sorted by language, so a dict rebuilt in a different
        # insertion order digests identically while any VALUE change does not.
        for language in sorted(self.boilerplate_code or {}):
            yield _frame(f"boilerplate:{language}",
                         (self.boilerplate_code or {})[language])
        for language in sorted(self.hidden_wrapper_code or {}):
            yield _frame(f"wrapper:{language}",
                         (self.hidden_wrapper_code or {})[language])

        yield _frame("case_count", len(self.cases))

        # Sorted by case digest, NOT array position. Grading treats the suite
        # as a set, so a benign reorder must not invalidate a reviewed
        # artifact — while any content edit still changes a case digest and so
        # cannot hide behind a reorder. The ordering is total because
        # normalized-duplicate inputs are rejected upstream.
        for case in sorted(self.cases, key=lambda c: c.case_digest):
            yield _frame("case", case.case_digest)
            yield _frame("case_input", case.input_digest)
            yield _frame("case_expected", case.expected_digest)
            yield _frame("case_oracle_output", case.oracle_output_digest)
            yield _frame("case_agreeing_runs", case.agreeing_runs)

        yield _frame("reference_id", self.reference_id)
        yield _frame("reference_language", self.reference_language)
        yield _frame("reference_source_hash", self.reference_source_hash)

        yield _frame("quality_tier1_kill_rate", self.quality.tier1_kill_rate)
        yield _frame("quality_tier2_kill_rate", self.quality.tier2_kill_rate)
        for blocker in sorted(self.quality.blockers):
            yield _frame("quality_blocker", blocker)
        yield _frame("quality_mutant_digest", self.quality.mutant_digest)

    def digest(self):
        return digest_of(self._frames())

    # ── derived state ─────────────────────────────────────────

    @property
    def legacy_cases(self):
        """Cases whose stored answer is NOT the one the oracle produced."""
        return [case for case in self.cases if not case.is_oracle_backed]

    @property
    def is_fully_oracle_backed(self):
        return bool(self.cases) and not self.legacy_cases


# ═════════════════════════════════════════════════════════════
# Assembly
# ═════════════════════════════════════════════════════════════

#: How many agreeing executions a case needs. Mirrors the pipeline's
#: REQUIRED_RUNS; two is the minimum that can detect disagreement at all.
REQUIRED_AGREEING_RUNS = 2


def evidence_alias(question, using=None):
    """
    The connection an artifact's evidence must be read from.

    The question's OWN connection, not `default`. The operator commands read a
    question through a named alias; reading its oracle evidence through a
    different one would assemble a single artifact — and therefore a single
    digest a human is asked to vouch for — out of two databases. On a
    deployment where they are the same server that is invisible, and on one
    where they are not it is undetectable in the digest.

    `using` overrides explicitly; otherwise the alias the question was loaded
    from, falling back to `default` for a question that was constructed rather
    than read.
    """
    return using or question._state.db or DEFAULT_DB_ALIAS


def collect_case_evidence(question, reference, using=None):
    """
    (evidence, blockers) for every hidden case, read from provenance.

    Evidence is scoped to the reference's CURRENT source hash. Executions from
    a superseded revision of the same reference are deliberately invisible
    here: they are history worth keeping (P2.7g-1) but they describe code that
    is no longer canonical, and counting them would let an approval rest on an
    implementation nobody approved.
    """
    alias = evidence_alias(question, using)
    evidence, blockers = [], []
    cases = question.hidden_test_cases
    if not isinstance(cases, list) or not cases:
        return [], ["question has no hidden test cases"]

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            blockers.append(f"case {index + 1} is not an object")
            continue

        stdin = case.get("stdin", "")
        case_digest = provenance.case_identity(stdin)
        expected = case.get("expected_output")
        expected_digest = (
            provenance.output_identity(normalize_output(str(expected)))
            if expected is not None and str(expected).strip() else "")

        executions = OracleExecution.objects.using(alias).filter(
            question=question, case_digest=case_digest,
            reference_source_hash=reference.source_hash)

        if executions.filter(
                status=OracleExecution.STATUS_NONDETERMINISTIC).exists():
            blockers.append(
                f"case {index + 1} has a nondeterministic execution on record")

        successes = list(executions.filter(
            status=OracleExecution.STATUS_SUCCESS))
        if not successes:
            blockers.append(
                f"case {index + 1} has no successful oracle execution for the "
                f"current reference revision")
            evidence.append(CaseEvidence(case_digest, provenance.input_identity(stdin),
                                         expected_digest, "", 0))
            continue

        digests = {execution.output_digest for execution in successes}
        if len(digests) != 1:
            # Two successful runs of the same code on the same input produced
            # different answers. Recorded, unresolvable, never averaged.
            blockers.append(
                f"case {index + 1} has {len(digests)} conflicting successful "
                f"outputs on record")
            evidence.append(CaseEvidence(case_digest, provenance.input_identity(stdin),
                                         expected_digest, "", len(successes)))
            continue

        output_digest = digests.pop()
        if len(successes) < REQUIRED_AGREEING_RUNS:
            blockers.append(
                f"case {index + 1} has {len(successes)} agreeing run(s); "
                f"{REQUIRED_AGREEING_RUNS} are required to evidence determinism")

        evidence.append(CaseEvidence(
            case_digest=case_digest,
            input_digest=provenance.input_identity(stdin),
            expected_digest=expected_digest,
            oracle_output_digest=output_digest,
            agreeing_runs=len(successes)))

    return evidence, blockers


def build_artifact(question, reference, quality, using=None):
    """
    Assemble the artifact and everything blocking its approval.

    Reads only. Nothing in this call path writes, and the structural guard in
    the test suite asserts that rather than trusting this sentence.

    Every read is on ONE connection — see `evidence_alias`.
    """
    blockers = []
    try:
        contract = execution_contract.contract_version(question)
    except execution_contract.UnknownExecutionContract as exc:
        contract = ""
        blockers.append(str(exc))

    evidence, evidence_blockers = collect_case_evidence(
        question, reference, using=using)
    blockers.extend(evidence_blockers)

    artifact = QuestionArtifact(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        question_id=question.pk,
        content=question.content or "",
        execution_contract_version=contract,
        boilerplate_code=question.boilerplate_code or {},
        hidden_wrapper_code=question.hidden_wrapper_code or {},
        cases=evidence,
        reference_id=reference.pk,
        reference_language=reference.language,
        reference_source_hash=reference.source_hash or "",
        quality=quality,
        blockers=blockers,
    )

    legacy = artifact.legacy_cases
    if legacy:
        # The stored answer is not the oracle's. Either it predates the oracle
        # entirely or it disagrees; both are UNVERIFIED, and neither becomes
        # trusted by resemblance.
        blockers.append(
            f"{len(legacy)} case(s) have expected_output that is not backed by "
            f"a matching oracle execution (legacy or conflicting)")

    if not quality.passed:
        blockers.append(
            "hidden-test quality gate did not pass "
            f"(tier1={quality.tier1_kill_rate}, tier2={quality.tier2_kill_rate}, "
            f"{len(quality.blockers)} blocker(s))")

    artifact.blockers = blockers
    return artifact
