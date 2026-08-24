"""
Hidden-test quality gate (M2 P2.7h-1).

`MIN_HIDDEN_TESTS = 12` is a FLOOR, not a quality score. Twelve near-identical
cases satisfy it while testing one thing. This module answers the question the
floor cannot: **would this suite actually catch a wrong solution?**

── What it is ──────────────────────────────────────────────────────────────

Pure assessment. It reads a suite and a set of candidate wrong solutions,
executes them through a caller-supplied runner, and reports. It has no ORM
writes, no Django model imports, and no way to reach a `Question` row.

It NEVER manufactures grading truth. It reads `expected_output`; it cannot
write one. A suite it judges excellent is not thereby verified — verification
is oracle execution against an approved reference (P2.7g), and this gate runs
*after* that, never instead of it.

── Execution ───────────────────────────────────────────────────────────────

`runner` is the callable this repository already uses in two places —
`GradingService(runner)` and `OracleService(runner)` — with the signature

    (source, language, stdin) -> verdict dict

so the gate needs no Judge0 credentials and no execution engine of its own.
Tests inject a synthetic in-process runner; a future operator command would
inject the real one. Nothing here assumes any particular language actually
runs: a runner that cannot execute Java reports EXECUTION_ERROR, which is a
BLOCKER, not a silent pass.

── The gate ────────────────────────────────────────────────────────────────

    1. count      >= MIN_HIDDEN_TESTS
    2. contract    zero malformed cases (hidden_tests.validate_suite)
    3. duplicates  zero, under NORMALIZED comparison
    4. categories  every REQUIRED and APPLICABLE category present
    5. Tier 1      100% of applicable curated mutants killed
    6. Tier 2      >= 80% effective kill rate

A survivor can never be hidden by shrinking the denominator: EQUIVALENT
requires a written structural argument, and an unexplained "equivalent" is
recorded as SURVIVED.
"""

from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, Sequence

from groups import hidden_tests
from groups.utils import normalize_output

#: Tier-1 curated wrong solutions must ALL die. They are hand-written to be
#: the mistakes a learner actually makes, so a suite that misses one is a
#: suite that would mark a real misconception "Accepted".
TIER1_REQUIRED_KILL_RATE = 1.0

#: Tier-2 syntactic mutants are mechanical, so a residue of un-killable but
#: not-provably-equivalent mutants is expected. 80% is the contract's figure.
TIER2_REQUIRED_KILL_RATE = 0.80

KILLED = "KILLED"
SURVIVED = "SURVIVED"
EQUIVALENT = "EQUIVALENT"
NOT_APPLICABLE = "NOT_APPLICABLE"
EXECUTION_ERROR = "EXECUTION_ERROR"

PASS = "PASS"
FAIL = "FAIL"


# ═════════════════════════════════════════════════════════════
# The problem's input contract — what categories even mean here
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class InputContract:
    """
    What this problem's inputs actually look like.

    Category coverage is meaningless without it. "negative values" is a real
    gap for a problem over integers and nonsense for one over string lengths,
    and demanding it everywhere trains authors to add junk cases to satisfy a
    checklist. Every generic category below is gated on a property of THIS
    contract.
    """

    accepts_empty_input: bool = False
    is_sequence: bool = False
    numeric: bool = False
    allows_duplicates: bool = False
    allows_negative: bool = False
    allows_zero: bool = False
    order_sensitive: bool = False
    has_size_bounds: bool = False
    overflow_sensitive: bool = False
    description: str = ""


@dataclass(frozen=True)
class CategoryRule:
    name: str
    applies: Callable[[InputContract], bool]
    required: bool = True
    rationale: str = ""


#: Generic categories, each with the contract property that makes it apply.
#: Ordered for deterministic reporting.
GENERIC_CATEGORIES: tuple = (
    CategoryRule("empty_input", lambda c: c.accepts_empty_input,
                 rationale="the contract admits an empty input"),
    CategoryRule("singleton", lambda c: c.is_sequence,
                 rationale="sequence problems break on length 1"),
    CategoryRule("minimum_boundary", lambda c: c.has_size_bounds,
                 rationale="the contract states a lower bound"),
    CategoryRule("maximum_boundary", lambda c: c.has_size_bounds,
                 rationale="the contract states an upper bound"),
    CategoryRule("duplicate_values", lambda c: c.allows_duplicates,
                 rationale="inputs may repeat"),
    CategoryRule("negative_values", lambda c: c.numeric and c.allows_negative,
                 rationale="the domain includes negatives"),
    CategoryRule("zero", lambda c: c.numeric and c.allows_zero,
                 rationale="zero is in the domain and is a common special case"),
    CategoryRule("already_sorted", lambda c: c.is_sequence and c.order_sensitive,
                 required=False,
                 rationale="ordering assumptions hide here"),
    CategoryRule("reverse_sorted", lambda c: c.is_sequence and c.order_sensitive,
                 required=False,
                 rationale="the mirror of already_sorted"),
    CategoryRule("overflow_sensitive", lambda c: c.overflow_sensitive,
                 rationale="values can exceed a 32-bit accumulator"),
)


@dataclass(frozen=True)
class CategorySubstitution:
    """
    A problem-specific failure mode standing in for a generic category.

    Recorded rather than silently accepted: substituting is a judgement about
    THIS problem, and a later reviewer needs to see both what was replaced and
    why.
    """

    replaces: str
    name: str
    reason: str


# ═════════════════════════════════════════════════════════════
# Mutants
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Mutant:
    """
    A candidate wrong solution.

    Tier 1 is curated — a realistic algorithmic misconception, written by a
    human for this problem. Tier 2 is mechanical — an operator flip, a
    boundary nudge.

    `applicable=False` means this mutation does not make sense for this
    problem (there is no `<=` to flip, the problem has no loop bound). It is
    excluded from BOTH numerator and denominator and reported separately — it
    is not a kill.

    `equivalence_argument` is the ONLY route to EQUIVALENT. Passing every test
    is not evidence of equivalence; it is evidence of a possible gap. Without
    a written structural reason the outcome is SURVIVED.
    """

    identifier: str
    tier: int
    description: str
    source: str
    language: str = "python"
    applicable: bool = True
    not_applicable_reason: str = ""
    equivalence_argument: str = ""

    def __post_init__(self):
        if self.tier not in (1, 2):
            raise ValueError(f"mutant {self.identifier}: tier must be 1 or 2")
        if not self.applicable and not self.not_applicable_reason:
            raise ValueError(
                f"mutant {self.identifier}: NOT_APPLICABLE needs a reason — "
                f"an unexplained exclusion is how a kill rate gets inflated")


@dataclass
class MutantResult:
    identifier: str
    tier: int
    description: str
    outcome: str
    detail: str = ""
    killed_by_case: Optional[int] = None


# ═════════════════════════════════════════════════════════════
# Report
# ═════════════════════════════════════════════════════════════

@dataclass
class QualityReport:
    total_cases: int
    duplicate_count: int
    malformed_count: int
    contract_problems: list = field(default_factory=list)

    category_records: list = field(default_factory=list)
    missing_required_categories: list = field(default_factory=list)
    substitutions: list = field(default_factory=list)

    tier1_total: int = 0
    tier1_applicable: int = 0
    tier1_killed: int = 0
    tier1_survived: int = 0
    tier1_not_applicable: int = 0
    tier1_errors: int = 0
    tier1_kill_rate: Optional[float] = None

    tier2_total: int = 0
    tier2_applicable: int = 0
    tier2_killed: int = 0
    tier2_survived: int = 0
    tier2_equivalent: int = 0
    tier2_not_applicable: int = 0
    tier2_errors: int = 0
    tier2_effective_kill_rate: Optional[float] = None

    results: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    verdict: str = FAIL

    def as_dict(self):
        data = asdict(self)
        data["results"] = [asdict(r) for r in self.results]
        return data


# ═════════════════════════════════════════════════════════════
# Execution
# ═════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExecutionPlan:
    """
    How a candidate solution and a stored case become a real execution.

    REQUIRED, with no default (M2 P2.7 follow-up). The gate used to call

        runner(mutant.source, mutant.language, case.get("stdin", ""))

    which bypassed the shared seam on BOTH halves: the mutant was never wrapped
    by `GradingService._build_executable`, and the stored stdin never passed
    through `prepare_stdin`. Tier-1 and Tier-2 kill rates were therefore
    measured against semantics no learner ever experiences — a suite could pass
    the gate while the grader treated the same inputs differently.

    A default would have quietly restored that, so there is none. Production
    builds this through `GradingService.quality_execution_plan(question)`; the
    two callables it holds are the same seam the grader and the oracle use.

    Kept as plain callables rather than an ORM object so this module stays pure
    — it still imports no models and reaches no database.
    """

    #: (source, language) -> the source that will actually be executed.
    build_executable: Callable[[str, str], str]
    #: (stored stdin, language) -> the bytes actually written to stdin.
    prepare_stdin: Callable[[str, str], str]


def _run_case(runner, plan, mutant, case):
    """
    (matched, detail). `matched` is None when the runner could not execute.

    Comparison uses `normalize_output` — the SAME comparator
    `GradingService.grade` uses. A gate that compared more loosely than the
    grader would pass suites the grader then fails.
    """
    try:
        # Both halves of the seam, in the grader's own order: decide what runs,
        # then decide what it is fed. `prepare_stdin` raises for a question
        # whose stored case cannot be executed under its declared contract, and
        # that is an EXECUTION_ERROR — an unmeasured mutant, never a silent
        # pass.
        source = plan.build_executable(mutant.source, mutant.language)
        stdin = plan.prepare_stdin(case.get("stdin", ""), mutant.language)
    except Exception as exc:                                  # noqa: BLE001
        return None, f"execution contract: {type(exc).__name__}: {exc}"

    try:
        verdict = runner(source, mutant.language, stdin)
    except Exception as exc:                                  # noqa: BLE001
        return None, f"runner raised {type(exc).__name__}: {exc}"

    if not isinstance(verdict, dict):
        return None, f"runner returned {type(verdict).__name__}, expected dict"
    if verdict.get("error"):
        return None, str(verdict["error"])
    status_id = verdict.get("status_id")
    if status_id is not None and status_id != 3:
        # A mutant that crashes or times out IS caught by the suite — the
        # learner would see a failure. That is a kill, not an error.
        return False, f"non-accepted status {status_id}"

    # `str()` because a malformed case may hold a non-string expected_output.
    # That is ALREADY a blocker — `validate_suite` catches it and the verdict
    # is FAIL regardless — so the coercion exists only so the gate reports the
    # violation instead of crashing on it. Assessment must survive bad input;
    # a harness that dies on the suites it is meant to judge is useless
    # exactly when it is needed.
    actual = normalize_output(str(verdict.get("stdout") or ""))
    expected = normalize_output(str(case.get("expected_output") or ""))
    return actual == expected, ""


def evaluate_mutant(runner, plan, mutant, cases):
    """
    Run one mutant against the whole suite, through `plan`.

    A mutant dies the moment ANY case disagrees with the stored expected
    output. Surviving every case is what needs explaining.
    """
    if not mutant.applicable:
        return MutantResult(mutant.identifier, mutant.tier, mutant.description,
                            NOT_APPLICABLE, mutant.not_applicable_reason)

    for index, case in enumerate(cases):
        matched, detail = _run_case(runner, plan, mutant, case)
        if matched is None:
            return MutantResult(mutant.identifier, mutant.tier,
                                mutant.description, EXECUTION_ERROR,
                                f"case {index + 1}: {detail}")
        if not matched:
            return MutantResult(mutant.identifier, mutant.tier,
                                mutant.description, KILLED, detail,
                                killed_by_case=index + 1)

    if mutant.equivalence_argument:
        return MutantResult(mutant.identifier, mutant.tier, mutant.description,
                            EQUIVALENT, mutant.equivalence_argument)

    # Passed every test with no argument for why it must. That is a SUITE GAP.
    return MutantResult(
        mutant.identifier, mutant.tier, mutant.description, SURVIVED,
        "passed every hidden test; no equivalence argument was supplied, so "
        "this is recorded as a gap rather than assumed harmless")


# ═════════════════════════════════════════════════════════════
# Categories
# ═════════════════════════════════════════════════════════════

def assess_categories(contract, cases, substitutions=()):
    """
    (records, missing_required) for the categories that APPLY to this problem.

    A category the contract does not admit is reported as not applicable and
    counts neither for nor against the suite.
    """
    present = {
        str(case.get("category")).strip().lower()
        for case in cases
        if isinstance(case, dict) and case.get("category")
    }
    replaced = {s.replaces: s for s in substitutions}

    records, missing = [], []
    for rule in GENERIC_CATEGORIES:
        applicable = bool(rule.applies(contract))
        substitution = replaced.get(rule.name)

        if not applicable:
            records.append({
                "category": rule.name, "applicable": False,
                "required": False, "present": False,
                "substituted_by": None, "note": "not applicable to this contract",
            })
            continue

        if substitution is not None:
            satisfied = substitution.name.strip().lower() in present
            records.append({
                "category": rule.name, "applicable": True,
                "required": rule.required, "present": satisfied,
                "substituted_by": substitution.name,
                "note": f"substituted: {substitution.reason}",
            })
            if rule.required and not satisfied:
                missing.append(f"{rule.name} (substituted by "
                               f"{substitution.name}, which is also absent)")
            continue

        satisfied = rule.name in present
        records.append({
            "category": rule.name, "applicable": True,
            "required": rule.required, "present": satisfied,
            "substituted_by": None, "note": rule.rationale,
        })
        if rule.required and not satisfied:
            missing.append(rule.name)

    return records, missing


# ═════════════════════════════════════════════════════════════
# The gate
# ═════════════════════════════════════════════════════════════

def normalized_duplicate_indexes(cases):
    """
    Duplicate inputs under NORMALIZED comparison.

    P2.7h-1 deliberately adopts the stricter of the two definitions already in
    the repository. `hidden_tests.validate_suite` compares raw `stdin`;
    `reseed_questions` compares `normalize_output(stdin)`. Two cases differing
    only by a trailing newline are the same test, so the normalized definition
    is the one that means anything — and a gate that were more permissive than
    the generator could pass a suite the generator would reject.

    This module does NOT change either existing definition. Unifying them is a
    separate change with its own blast radius; see the P2.7h-1 report.
    """
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


def evaluate_suite(cases, mutants, runner, contract, *, plan,
                   substitutions=(), floor=hidden_tests.MIN_HIDDEN_TESTS):
    """
    The whole gate, deterministically.

    Nothing here writes. `cases` is read, `mutants` are executed through the
    caller's runner VIA `plan`, and a report comes back. The same inputs always
    produce the same report — no randomness, no iteration-order dependence, no
    clock.

    `plan` is keyword-only and has no default on purpose: a gate that can be
    called without one is a gate that can silently measure the wrong semantics,
    which is exactly the defect this parameter exists to close.
    """
    cases = list(cases or [])
    report = QualityReport(total_cases=len(cases), duplicate_count=0,
                           malformed_count=0)

    # 1-2. Contract and structure, reusing the established validator rather
    # than re-deriving what a valid case is for the fourth time.
    problems = hidden_tests.validate_suite(cases, floor=floor)
    report.contract_problems = sorted(str(p) for p in problems)
    report.malformed_count = sum(
        1 for p in problems if p.index is not None
        and "duplicate" not in p.message)

    if len(cases) < floor:
        report.blockers.append(
            f"only {len(cases)} hidden tests; the floor is {floor}")

    if report.malformed_count:
        report.blockers.append(
            f"{report.malformed_count} case(s) violate the hidden-test contract")

    # 3. Duplicates, normalized.
    duplicates = normalized_duplicate_indexes(cases)
    report.duplicate_count = len(duplicates)
    if duplicates:
        pairs = ", ".join(f"case {i + 1} repeats case {j + 1}"
                          for i, j in duplicates)
        report.blockers.append(
            f"{len(duplicates)} duplicate input(s) under normalized "
            f"comparison: {pairs}")

    # 4. Categories.
    records, missing = assess_categories(contract, cases, substitutions)
    report.category_records = records
    report.missing_required_categories = missing
    report.substitutions = [asdict(s) for s in substitutions]
    if missing:
        report.blockers.append(
            f"missing required categor{'y' if len(missing) == 1 else 'ies'}: "
            + ", ".join(missing))

    # 5-6. Mutants. Sorted by identifier so the report is order-independent.
    results = [evaluate_mutant(runner, plan, m, cases)
               for m in sorted(mutants, key=lambda m: (m.tier, m.identifier))]
    report.results = results

    _summarise_tier(report, results, tier=1)
    _summarise_tier(report, results, tier=2)

    _apply_tier1_rule(report)
    _apply_tier2_rule(report)

    errors = [r for r in results if r.outcome == EXECUTION_ERROR]
    if errors:
        # NEVER a silent pass. An unrunnable mutant is an unmeasured mutant.
        report.blockers.append(
            f"{len(errors)} mutant(s) could not be executed: "
            + ", ".join(sorted(r.identifier for r in errors)))

    report.verdict = FAIL if report.blockers else PASS
    report.blockers = sorted(report.blockers)
    return report


def _summarise_tier(report, results, tier):
    subset = [r for r in results if r.tier == tier]
    counts = {outcome: sum(1 for r in subset if r.outcome == outcome)
              for outcome in (KILLED, SURVIVED, EQUIVALENT, NOT_APPLICABLE,
                              EXECUTION_ERROR)}
    if tier == 1:
        report.tier1_total = len(subset)
        report.tier1_killed = counts[KILLED]
        report.tier1_survived = counts[SURVIVED] + counts[EQUIVALENT]
        report.tier1_not_applicable = counts[NOT_APPLICABLE]
        report.tier1_errors = counts[EXECUTION_ERROR]
        report.tier1_applicable = (report.tier1_killed + report.tier1_survived
                                   + report.tier1_errors)
    else:
        report.tier2_total = len(subset)
        report.tier2_killed = counts[KILLED]
        report.tier2_survived = counts[SURVIVED]
        report.tier2_equivalent = counts[EQUIVALENT]
        report.tier2_not_applicable = counts[NOT_APPLICABLE]
        report.tier2_errors = counts[EXECUTION_ERROR]
        report.tier2_applicable = (report.tier2_killed + report.tier2_survived
                                   + report.tier2_errors)


def _apply_tier1_rule(report):
    """
    Tier 1 is all-or-nothing, and EQUIVALENT is not a defence.

    A curated mutant is a wrong algorithm somebody deliberately wrote. If it
    survives, the suite would mark that misconception "Accepted" — which is
    the exact outcome the gate exists to prevent. There is no percentage here.
    """
    denominator = report.tier1_killed + report.tier1_survived
    if denominator == 0:
        report.tier1_kill_rate = None
        if report.tier1_total == 0:
            report.blockers.append(
                "no Tier-1 curated wrong solutions were supplied; a suite "
                "cannot be shown to catch realistic mistakes without any")
        else:
            report.blockers.append(
                "every Tier-1 mutant was excluded as not-applicable or failed "
                "to execute; nothing was actually measured")
        return

    report.tier1_kill_rate = report.tier1_killed / denominator
    if report.tier1_kill_rate < TIER1_REQUIRED_KILL_RATE:
        report.blockers.append(
            f"Tier-1 kill rate {report.tier1_kill_rate:.0%} — every curated "
            f"wrong solution must be caught ({report.tier1_survived} survived)")


def _apply_tier2_rule(report):
    """
    Effective kill rate = killed / (killed + survived).

    EQUIVALENT is excluded — but only ever with a written structural argument
    (see `Mutant`), so the denominator cannot be shrunk by relabelling an
    inconvenient survivor. NOT_APPLICABLE is excluded because a mutation that
    could not be made is not a test of anything.
    """
    denominator = report.tier2_killed + report.tier2_survived
    if denominator == 0:
        report.tier2_effective_kill_rate = None
        if report.tier2_total == 0:
            report.blockers.append(
                "no Tier-2 mutants were supplied; the automated kill rate is "
                "unmeasured, not satisfied")
        else:
            report.blockers.append(
                "every Tier-2 mutant was excluded or unrunnable; the kill "
                "rate is unmeasured, not satisfied")
        return

    report.tier2_effective_kill_rate = report.tier2_killed / denominator
    if report.tier2_effective_kill_rate < TIER2_REQUIRED_KILL_RATE:
        report.blockers.append(
            f"Tier-2 effective kill rate "
            f"{report.tier2_effective_kill_rate:.0%} is below "
            f"{TIER2_REQUIRED_KILL_RATE:.0%} "
            f"({report.tier2_survived} survived of {denominator} measured)")
