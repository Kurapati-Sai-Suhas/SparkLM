"""
Question-bank census (M2 P2.7e).

READ-ONLY. Aggregates and streamed reads; no write path exists in this module
and a structural test asserts it.

── Why the connection gates matter more than the metrics ──────────────────

A census that silently reports development numbers as production is worse than
no census, because it converts "we don't know" into a false "we do". Every
gate in `connection_gates` therefore ABORTS rather than degrading, and the
report records which database answered so a number can never be separated from
its source.

── Trust is read, never inferred ──────────────────────────────────────────

Classification comes from `Question.status` and `Question.trust_state` alone.
Not from the presence of `expected_output`, not from old accepted submissions,
not from Elo or mastery. Those are what a question's history looks like, and
this milestone exists because history looked fine while the answer keys were
unverified.
"""

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field

from django.db.models import Count, Q

from groups import hidden_tests
from groups.models import (
    CodeSubmission, GlickoSnapshot, OracleExecution, Question, QuestionApproval,
    RecommendationLog, ReferenceSolution,
)
from groups.utils import normalize_output

#: Reseed blast-radius classes. One per question, first match wins, most
#: severe first — a question needing an oracle also needs approval, and
#: reporting it twice would double-count the blast radius.
SAFE = "SAFE"
BLOCKED = "BLOCKED"
NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"
GENERATE_TESTS = "GENERATE_TESTS"
ORACLE_REQUIRED = "ORACLE_REQUIRED"
QUESTION_APPROVAL_REQUIRED = "QUESTION_APPROVAL_REQUIRED"
REPAIR_BOILERPLATE = "REPAIR_BOILERPLATE"
REPAIR_CONTENT = "REPAIR_CONTENT"

#: Languages the frontend actually offers. A question declaring none of these
#: cannot be attempted, whatever its metadata says.
EXPECTED_LANGUAGES = ("python", "java", "cpp", "javascript", "c")


@dataclass
class CensusReport:
    generated_at: str = ""
    database_identity: dict = field(default_factory=dict)
    schema_state: dict = field(default_factory=dict)

    question_counts: dict = field(default_factory=dict)
    trust_counts: dict = field(default_factory=dict)
    adaptive_eligibility_counts: dict = field(default_factory=dict)
    hidden_test_counts: dict = field(default_factory=dict)
    duplicate_counts: dict = field(default_factory=dict)
    gradability_counts: dict = field(default_factory=dict)
    language_counts: dict = field(default_factory=dict)
    reference_counts: dict = field(default_factory=dict)
    provenance_counts: dict = field(default_factory=dict)
    approval_counts: dict = field(default_factory=dict)
    contradiction_counts: dict = field(default_factory=dict)
    grading_data_counts: dict = field(default_factory=dict)
    reseed_candidates: dict = field(default_factory=dict)
    safe_to_leave_untouched: int = 0

    blockers: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)
    report_hash: str = ""

    def as_dict(self):
        return asdict(self)


# ═════════════════════════════════════════════════════════════
# Trust and status
# ═════════════════════════════════════════════════════════════

def question_counts():
    """Status x trust_state, from the trust boundary only."""
    rows = (Question.objects
            .values("status", "trust_state")
            .annotate(n=Count("id")))
    matrix = {f"{r['status']}+{r['trust_state']}": r["n"] for r in rows}
    return {
        "total": Question.objects.count(),
        "by_status": dict(Question.objects.values_list("status")
                          .annotate(n=Count("id"))),
        "by_trust_state": dict(Question.objects.values_list("trust_state")
                               .annotate(n=Count("id"))),
        "matrix": matrix,
    }


def contradiction_counts():
    """
    States the trust model says cannot exist.

    `draft_oracle_verified` is the one migration 0042's CHECK forbids. A
    non-zero count here means the constraint would FAIL to apply, and that
    something wrote trust state through a path nobody has audited — which is a
    reason to stop, not to repair.
    """
    draft_verified = Question.objects.filter(
        status=Question.STATUS_DRAFT,
        trust_state=Question.TRUST_ORACLE_VERIFIED).count()

    # Adaptive eligibility is frozen per submission (P2.7c), so a submission
    # marked eligible against a question that is NOT currently adaptive-
    # eligible is EXPECTED and legitimate — the question may have been demoted
    # since. Counted, never flagged as a contradiction.
    eligible_on_unverified = CodeSubmission.objects.filter(
        adaptive_eligible=True).exclude(
        question__trust_state=Question.TRUST_ORACLE_VERIFIED).count()

    verified_without_reference = Question.objects.filter(
        trust_state=Question.TRUST_ORACLE_VERIFIED).exclude(
        reference_solutions__is_active=True).count()

    verified_without_approval = Question.objects.filter(
        trust_state=Question.TRUST_ORACLE_VERIFIED).exclude(
        approvals__isnull=False).count()

    return {
        "draft_oracle_verified": draft_verified,
        "oracle_verified_without_active_reference": verified_without_reference,
        "oracle_verified_without_approval": verified_without_approval,
        "eligible_submissions_on_now_unverified_questions":
            eligible_on_unverified,
    }


# ═════════════════════════════════════════════════════════════
# Hidden tests
# ═════════════════════════════════════════════════════════════

def _normalized_duplicates(cases):
    """
    Duplicate inputs under NORMALIZED comparison.

    Deliberately the stricter of the two definitions the repository contains:
    `hidden_tests.validate_suite` compares RAW stdin, while `reseed_questions`
    and the P2.7h-1 quality gate compare normalized. Normalized catches
    everything raw does and more, so it is the safe number to report — but the
    divergence is real and is surfaced as a warning rather than silently
    resolved here.
    """
    seen, duplicates = set(), 0
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("stdin"), str):
            continue
        key = normalize_output(case["stdin"])
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def hidden_test_census():
    """
    Per-question hidden-test analysis, streamed.

    `.values(...).iterator()` rather than loading model instances: the bank is
    ~1,100 questions whose `hidden_test_cases` JSON dominates the row size, and
    only three columns are needed.
    """
    counts = Counter()
    duplicates_total = 0
    per_question_class = {}
    malformed_examples = []

    stream = (Question.objects
              .values("id", "hidden_test_cases", "boilerplate_code",
                      "status", "trust_state", "execution_contract_version")
              .iterator(chunk_size=200))

    for row in stream:
        cases = row["hidden_test_cases"]
        question_id = row["id"]

        if not isinstance(cases, list) or not cases:
            counts["no_hidden_tests"] += 1
            per_question_class[question_id] = GENERATE_TESTS
            continue

        counts["has_hidden_tests"] += 1
        counts["total_cases"] += len(cases)

        problems = hidden_tests.validate_suite(cases)
        case_problems = [p for p in problems if p.index is not None
                         and "duplicate" not in p.message]
        if case_problems:
            counts["questions_with_malformed_cases"] += 1
            counts["malformed_cases"] += len(case_problems)
            if len(malformed_examples) < 10:
                malformed_examples.append(
                    {"question_id": question_id,
                     "problems": [str(p) for p in case_problems[:3]]})

        duplicates = _normalized_duplicates(cases)
        if duplicates:
            counts["questions_with_normalized_duplicates"] += 1
            duplicates_total += duplicates

        if len(cases) >= hidden_tests.MIN_HIDDEN_TESTS:
            counts["meets_minimum_count"] += 1
        else:
            counts["below_minimum_count"] += 1

        if hidden_tests.is_gradable(cases):
            counts["gradable"] += 1
        else:
            counts["not_gradable"] += 1

        # expected_output presence, which is NOT the same as trustworthiness.
        with_output = sum(
            1 for c in cases
            if isinstance(c, dict) and str(c.get("expected_output", "")).strip())
        counts["cases_with_expected_output"] += with_output
        counts["cases_without_expected_output"] += len(cases) - with_output

        languages = row["boilerplate_code"] or {}
        if not isinstance(languages, dict) or not languages:
            counts["no_boilerplate"] += 1
        elif not any(lang in languages for lang in EXPECTED_LANGUAGES):
            counts["boilerplate_without_known_language"] += 1

    return dict(counts), duplicates_total, per_question_class, malformed_examples


def language_census():
    counts = Counter()
    for row in Question.objects.values("boilerplate_code").iterator(
            chunk_size=500):
        languages = row["boilerplate_code"] or {}
        if not isinstance(languages, dict):
            counts["__malformed__"] += 1
            continue
        for language in languages:
            counts[str(language)] += 1
    return dict(counts)


# ═════════════════════════════════════════════════════════════
# References, provenance, approvals
# ═════════════════════════════════════════════════════════════

def reference_counts():
    total = ReferenceSolution.objects.count()
    if not total:
        return {"total": 0, "note": "no ReferenceSolution rows exist"}

    by_state = dict(ReferenceSolution.objects.values_list("review_state")
                    .annotate(n=Count("id")))
    duplicate_active = (ReferenceSolution.objects
                        .filter(is_active=True)
                        .values("question_id", "language")
                        .annotate(n=Count("id"))
                        .filter(n__gt=1).count())

    return {
        "total": total,
        "by_review_state": by_state,
        "active": ReferenceSolution.objects.filter(is_active=True).count(),
        "inactive": ReferenceSolution.objects.filter(is_active=False).count(),
        "approved_and_active": ReferenceSolution.objects.filter(
            review_state=ReferenceSolution.REVIEW_APPROVED,
            is_active=True).count(),
        "by_language": dict(ReferenceSolution.objects.values_list("language")
                            .annotate(n=Count("id"))),
        "missing_source_hash": ReferenceSolution.objects.filter(
            Q(source_hash="") | Q(source_hash__isnull=True)).count(),
        "questions_with_active_reference": (
            Question.objects.filter(reference_solutions__is_active=True)
            .distinct().count()),
        "duplicate_active_per_question_language": duplicate_active,
    }


def provenance_counts():
    """
    The categories §8 requires kept apart.

    "expected_output exists" and "expected_output is trusted" are different
    facts, and conflating them is how ~1,100 legacy values would be mistaken
    for verified answer keys.
    """
    total = OracleExecution.objects.count()
    authoritative = OracleExecution.objects.filter(is_authoritative=True).count()

    verified_questions = Question.objects.filter(
        trust_state=Question.TRUST_ORACLE_VERIFIED).count()

    return {
        "oracle_execution_rows": total,
        "authoritative_rows": authoritative,
        "questions_with_any_provenance": (
            Question.objects.filter(oracle_executions__isnull=False)
            .distinct().count()),
        "questions_with_authoritative_provenance": (
            Question.objects.filter(oracle_executions__is_authoritative=True)
            .distinct().count()),
        "questions_oracle_verified": verified_questions,
        "executions_by_status": dict(
            OracleExecution.objects.values_list("status")
            .annotate(n=Count("id"))) if total else {},
        "glicko_snapshots": GlickoSnapshot.objects.count(),
    }


def approval_counts():
    total = QuestionApproval.objects.count()
    if not total:
        return {"total": 0,
                "note": "no QuestionApproval rows exist; no question has been "
                        "human-approved"}
    return {
        "total": total,
        "questions_with_approval": (
            QuestionApproval.objects.values("question_id").distinct().count()),
        "promoted": QuestionApproval.objects.filter(
            promoted_at__isnull=False).count(),
        "not_promoted": QuestionApproval.objects.filter(
            promoted_at__isnull=True).count(),
        # Staleness needs the artifact digest recomputed against live state,
        # which is what `question_promote` does. Reported as "requires
        # recomputation" rather than guessed.
        "staleness": "NOT_EVALUATED — requires per-question artifact digest "
                     "recomputation (question_review); not run in a census",
    }


# ═════════════════════════════════════════════════════════════
# Grading data
# ═════════════════════════════════════════════════════════════

def grading_data_counts():
    total = CodeSubmission.objects.count()
    return {
        "code_submissions": total,
        "by_status": dict(CodeSubmission.objects.values_list("status")
                          .annotate(n=Count("id"))) if total else {},
        "adaptive_eligible_true": CodeSubmission.objects.filter(
            adaptive_eligible=True).count(),
        "adaptive_eligible_false": CodeSubmission.objects.filter(
            adaptive_eligible=False).count(),
        "orphaned_no_question": CodeSubmission.objects.filter(
            question__isnull=True).count(),
        "recommendation_logs": RecommendationLog.objects.count(),
    }


# ═════════════════════════════════════════════════════════════
# Blast radius
# ═════════════════════════════════════════════════════════════

def reseed_blast_radius():
    """
    One class per question, most severe first.

    First match wins deliberately: a question needing hidden tests also needs
    an oracle and approval, and counting it in all three would inflate the
    blast radius past the number of questions that exist.
    """
    classes = Counter()

    stream = (Question.objects
              .values("id", "status", "trust_state", "hidden_test_cases",
                      "boilerplate_code", "content")
              .iterator(chunk_size=200))

    for row in stream:
        cases = row["hidden_test_cases"]
        languages = row["boilerplate_code"] or {}

        if row["status"] == Question.STATUS_BLOCKED:
            classes[BLOCKED] += 1
            continue

        if not isinstance(cases, list):
            classes[NEEDS_MANUAL_REVIEW] += 1
            continue

        if not cases or len(cases) < hidden_tests.MIN_HIDDEN_TESTS:
            classes[GENERATE_TESTS] += 1
            continue

        if hidden_tests.validate_suite(cases) and not hidden_tests.is_gradable(
                cases):
            classes[NEEDS_MANUAL_REVIEW] += 1
            continue

        if row["trust_state"] != Question.TRUST_ORACLE_VERIFIED:
            # Has a plausible suite but no verified answer key: the oracle is
            # what it needs, not regeneration.
            classes[ORACLE_REQUIRED] += 1
            continue

        if not isinstance(languages, dict) or not any(
                lang in languages for lang in EXPECTED_LANGUAGES):
            classes[REPAIR_BOILERPLATE] += 1
            continue

        if not (row["content"] or "").strip():
            classes[REPAIR_CONTENT] += 1
            continue

        classes[SAFE] += 1

    return dict(classes)


# ═════════════════════════════════════════════════════════════
# Report hash
# ═════════════════════════════════════════════════════════════

def report_hash(report):
    """
    Digest over the COUNTS only.

    `generated_at`, timings and the database identity are excluded: they change
    every run, and a hash that always changes identifies nothing. Two censuses
    of an unchanged bank therefore hash identically, which is what makes drift
    detectable.
    """
    payload = {k: v for k, v in report.as_dict().items()
               if k not in {"generated_at", "timings_ms", "report_hash",
                            "database_identity"}}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
