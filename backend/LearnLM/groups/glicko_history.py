"""
Point-in-time Glicko history (M2 P2.9b).

Two jobs, deliberately in one module so they cannot drift apart:

  1. **Record** the Glicko state that existed immediately before an
     interaction, from the values the update actually used.
  2. **Refuse** to hand any post-interaction state to a feature consumer.

── Why (1) has to happen inside the update ────────────────────────────────

`shadow.apply_submission` already reads both sides' state into locals before
either moves — the two-sided update requires it, or the learner's new rating
would feed the question's update and count one piece of evidence twice. Those
locals ARE the before-state, so recording them costs no extra query and, more
importantly, cannot disagree with what the arithmetic consumed.

Re-reading the row afterwards and calling it "before" would be wrong in a way
that is invisible: the row has already been written, so the read returns the
AFTER values and every snapshot would silently encode its own outcome.

── Why (2) is a hard refusal rather than a convention ─────────────────────

`learner_rating_after` moves up when the learner was correct. A model given it
while predicting that same interaction is being handed the label, and would
report an excellent AUC that means nothing. `kt_features` therefore has no
parameter that admits an `*_after` field; asking for one raises.
"""

from groups import glicko
from groups.models import GlickoSnapshot

#: Fields a KT feature extractor may read. PRE-interaction state only.
KT_ADMISSIBLE_FIELDS = frozenset({
    "learner_rating_before", "learner_rd_before", "learner_volatility_before",
    "learner_periods_inactive",
    "question_rating_before", "question_rd_before",
    "question_volatility_before", "question_periods_inactive",
})

#: Fields that encode the outcome of the interaction they belong to.
#:
#: Stored for auditing and gap detection, and structurally unavailable to any
#: feature consumer. Naming them explicitly means a future field added to the
#: model is admissible only if someone deliberately adds it above.
POST_INTERACTION_FIELDS = frozenset({
    "learner_rating_after", "learner_rd_after",
    "question_rating_after", "question_rd_after",
})


class PostInteractionLeakage(Exception):
    """Raised when a caller asks for state that did not exist yet."""


def record_snapshot(*, submission, topic, before, after, periods,
                    recorded_at):
    """
    Persist one snapshot. Returns the row, or None if one already exists.

    `before` and `after` are plain tuples supplied by the caller — the values
    the update used — rather than re-read from the database. That is the whole
    point: this function cannot look up the "before" state itself without
    reading rows the update has already overwritten.

    Idempotent on `submission_id_value`, so a retried shadow update cannot
    write a second, contradictory snapshot for one interaction.
    """
    learner_before, question_before = before
    learner_after, question_after = after
    learner_periods, question_periods = periods

    if GlickoSnapshot.objects.filter(
            submission_id_value=submission.pk).exists():
        return None

    snapshot = GlickoSnapshot(
        submission_id_value=submission.pk,
        submission_submitted_at=submission.submitted_at,
        user_id=submission.user_id,
        topic=topic,
        question_id=submission.question_id,

        learner_rating_before=learner_before[0],
        learner_rd_before=learner_before[1],
        learner_volatility_before=learner_before[2],
        learner_periods_inactive=learner_periods,

        question_rating_before=question_before[0],
        question_rd_before=question_before[1],
        question_volatility_before=question_before[2],
        question_periods_inactive=question_periods,

        learner_rating_after=learner_after[0],
        learner_rd_after=learner_after[1],
        question_rating_after=question_after[0],
        question_rd_after=question_after[1],

        recorded_at=recorded_at,
        glicko_version=glicko.IMPLEMENTATION_VERSION,
    )
    snapshot.save()
    return snapshot


def kt_features(snapshot, fields=None):
    """
    Pre-interaction Glicko state as a feature dict.

    Raises `PostInteractionLeakage` if asked for anything that did not exist
    before the interaction. There is deliberately no `allow_after`, no
    `include_all` and no `strict=False`: a flag that relaxes this would be
    used, and the resulting metric would look like success.
    """
    requested = KT_ADMISSIBLE_FIELDS if fields is None else set(fields)

    forbidden = requested & POST_INTERACTION_FIELDS
    if forbidden:
        raise PostInteractionLeakage(
            f"{sorted(forbidden)} describe state produced BY the interaction "
            f"being predicted. A rating that rose encodes a correct answer, so "
            f"including it hands the model its own label. Pre-interaction "
            f"fields are {sorted(KT_ADMISSIBLE_FIELDS)}.")

    unknown = requested - KT_ADMISSIBLE_FIELDS
    if unknown:
        raise PostInteractionLeakage(
            f"{sorted(unknown)} are not established pre-interaction fields. "
            f"Add them to KT_ADMISSIBLE_FIELDS deliberately, after checking "
            f"they cannot encode the outcome.")

    return {name: getattr(snapshot, name) for name in sorted(requested)}


def history_for(user, topic):
    """One learner's snapshots in one topic, oldest first — the KT sequence."""
    return (GlickoSnapshot.objects
            .filter(user=user, topic=topic)
            .order_by("recorded_at", "submission_id_value"))


def detect_gaps(user, topic):
    """
    Interactions that updated Glicko without leaving a snapshot.

    Exact, not heuristic. Rating does not drift between updates — only RD
    inflates with inactivity — so for consecutive snapshots of one
    (learner, topic):

        rating_after(n) == rating_before(n+1)

    holds exactly whenever no unrecorded update happened in between. A
    mismatch is proof that one did.
    """
    gaps, previous = [], None
    for snapshot in history_for(user, topic):
        if previous is not None:
            if snapshot.learner_rating_before != previous.learner_rating_after:
                gaps.append(
                    f"between submissions {previous.submission_id_value} and "
                    f"{snapshot.submission_id_value}: rating_after "
                    f"{previous.learner_rating_after} != rating_before "
                    f"{snapshot.learner_rating_before} — an update happened "
                    f"that was not recorded")
        previous = snapshot
    return gaps
