"""
LearnLM adapter — the trust firewall (M2 P2.10b).

The one module in `kt_dataset` that touches Django, and it imports lazily so
the rest of the package stays application-free.

── The firewall ────────────────────────────────────────────────────────────

    adaptive_eligible == True   ->  may become a KT label
    adaptive_eligible == False  ->  REJECTED, with a reason

Not a filter that could be relaxed by a flag — there is no parameter that
admits ineligible rows, and a structural test asserts none exists. The reason
is P2.7c's: a submission graded against an answer key nobody had verified is
not evidence about the learner, it is evidence about the answer key. Training
on it would teach the model the bank's defects with more sophistication than a
rule could.

It reuses `groups.kt_readiness.eligible_interactions()` rather than restating
the predicate. P2.10a already published that queryset as the single definition
of eligibility, and a second copy here would eventually disagree with it —
which is precisely the failure mode the FILTER_CONTRACT exists to prevent.

── Today this adapter yields nothing ──────────────────────────────────────

LearnLM has 0 ORACLE_VERIFIED questions, so 0 submissions are eligible. The
adapter is written and tested against synthetic fixtures so it is ready the
day the trust pipeline promotes its first question, and so the firewall is
proven before there is any pressure to relax it.
"""

REJECT_NOT_ELIGIBLE = "not_adaptive_eligible"


def learnlm_capabilities():
    """What LearnLM can supply today. Deliberately honest about the gaps."""
    from kt_dataset.schema import SourceCapabilities
    return SourceCapabilities(
        name="learnlm",
        version="live",
        has_wall_clock_time=True,          # CodeSubmission.submitted_at
        has_concepts=True,                 # Question.topic
        has_attempt_counts=True,           # derivable
        has_response_time=False,
        has_point_in_time_rating=False,
        notes=("Only adaptive_eligible submissions are admissible. "
               "Currently yields zero rows: no question has reached "
               "ORACLE_VERIFIED."),
        unavailable_reasons={
            "response_time":
                "execution_time_ms is the PROGRAM's Judge0 runtime, not the "
                "learner's deliberation time. Using it as a cognitive feature "
                "would encode algorithmic efficiency as thinking time and is "
                "confounded by Judge0 queue load. No substitute exists; this "
                "is P2.10a's MUST-HAVE instrumentation gap.",
            "point_in_time_rating":
                "LearnerTopicSkill/QuestionSkill store CURRENT state with "
                "updated_at only. No history table, and Glicko-2 updates in "
                "rating periods whose boundaries are not recorded, so replay "
                "would reconstruct a plausible history rather than the actual "
                "one. See groups.kt_leakage.GLICKO_RECONSTRUCTION.",
        },
    )


def read_learnlm(queryset=None):
    """
    Yield eligible LearnLM submissions in the pipeline's intermediate shape.

    READ-ONLY. `.values()` over an existing queryset; no write path exists in
    this module.

    `order_key` is the submission's own primary key ordered by `submitted_at`,
    NOT the timestamp itself — CodeSubmission is range-partitioned by month of
    `submitted_at` and two submissions can share a timestamp to the microsecond
    under load, so the pk breaks ties into a total order.
    """
    from groups import kt_readiness

    rows = (queryset if queryset is not None
            else kt_readiness.eligible_interactions())

    for row in (rows.values("id", "user_id", "question_id",
                            "question__topic_id", "status", "submitted_at",
                            "language")
                .order_by("submitted_at", "id")):
        yield {
            "source_row_id": f"codesubmission:{row['id']}",
            "learner_id": str(row["user_id"]),
            "question_id": str(row["question_id"]),
            "concept_id": str(row["question__topic_id"]),
            "correct": "1" if row["status"] == "accepted" else "0",
            "order_key": str(row["id"]),
            "attempt_count_raw": "",
            "outcome_label": row["status"],
            "occurred_at": row["submitted_at"],
            "raw_dataset_hash": "learnlm-live-no-raw-file",
            "source_file": "CodeSubmission",
            "language": row["language"] or "",
        }


def partition_by_eligibility(submissions):
    """
    (admissible, rejections) for an explicit list of submission-like objects.

    The firewall made testable on objects rather than a queryset. Used by the
    adversarial tests to prove an ineligible row is refused even when handed
    directly to the adapter, bypassing the queryset that would normally have
    excluded it.
    """
    from kt_dataset.validation import Rejection

    admissible, rejections = [], []
    for submission in submissions:
        if not getattr(submission, "adaptive_eligible", False):
            rejections.append(Rejection(
                f"codesubmission:{getattr(submission, 'pk', '?')}",
                REJECT_NOT_ELIGIBLE,
                "graded against a question that was not ORACLE_VERIFIED at "
                "the time; not evidence about the learner"))
            continue
        admissible.append(submission)
    return admissible, rejections
