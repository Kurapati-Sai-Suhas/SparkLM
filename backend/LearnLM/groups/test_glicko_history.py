"""
Point-in-time Glicko history (M2 P2.9b).

The invariant everything else serves: a snapshot's `*_before` fields are the
exact values `glicko.rate` consumed, and no consumer can reach the `*_after`
fields — which encode the outcome of the very interaction a KT model would be
predicting.
"""

import ast
import inspect
import pathlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from groups import glicko, glicko_history, shadow
from groups.models import (
    CodeSubmission, CodingPortal, GlickoSnapshot, LearnerTopicSkill, Question,
    QuestionSkill, Topic,
)


class GlickoHistoryTestCase(TestCase):

    def setUp(self):
        self.User = get_user_model()
        portal = CodingPortal.objects.create(name="Glicko Hist Portal")
        self.topic = Topic.objects.create(name="GH1", structure_type="flat",
                                          portal=portal)
        self.other_topic = Topic.objects.create(name="GH2",
                                                structure_type="flat",
                                                portal=portal)
        self.learner = self.User.objects.create_user(
            username="gh", email="gh@t.test", password="Pv#2026xyz")

    def question(self, title="GQ", topic=None, difficulty=1200.0):
        return Question.objects.create(
            title=title, content="c", topic=topic or self.topic,
            base_difficulty=difficulty,
            hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
            boilerplate_code={"python": "x"}, hidden_wrapper_code={},
            status=Question.STATUS_PUBLISHED)

    def submit(self, question=None, *, status="accepted", eligible=True,
               user=None):
        return CodeSubmission.objects.create(
            user=user or self.learner, question=question or self.question(),
            language="python", code="x", status=status,
            adaptive_eligible=eligible)


# ═════════════════════════════════════════════════════════════
# Recording
# ═════════════════════════════════════════════════════════════

class RecordingTests(GlickoHistoryTestCase):

    def test_first_ever_interaction_records_the_defaults(self):
        """
        A brand-new learner has no rating row yet. The snapshot must record the
        DEFAULTS that were actually used, not nulls.
        """
        submission = self.submit()
        shadow.apply_submission(submission)

        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)
        self.assertEqual(snapshot.learner_rating_before, glicko.DEFAULT_RATING)
        self.assertEqual(snapshot.learner_rd_before, glicko.DEFAULT_RD)
        self.assertEqual(snapshot.learner_volatility_before,
                         glicko.DEFAULT_VOLATILITY)
        self.assertEqual(snapshot.learner_periods_inactive, 0.0)

    def test_question_prior_is_recorded_as_the_before_state(self):
        question = self.question(difficulty=1450.0)
        submission = self.submit(question)
        shadow.apply_submission(submission)

        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)
        self.assertEqual(snapshot.question_rating_before, 1450.0)
        self.assertEqual(snapshot.question_rd_before, glicko.DEFAULT_RD)

    def test_second_interaction_records_the_state_the_first_produced(self):
        """
        The chain property: `rating_after(n) == rating_before(n+1)` exactly.
        Rating does not drift between updates; only RD inflates.
        """
        question = self.question()
        first = self.submit(question)
        shadow.apply_submission(first)
        second = self.submit(question)
        shadow.apply_submission(second)

        a, b = GlickoSnapshot.objects.order_by("submission_id_value")
        self.assertEqual(b.learner_rating_before, a.learner_rating_after)
        self.assertEqual(b.question_rating_before, a.question_rating_after)

    def test_before_state_is_what_glicko_actually_consumed(self):
        """
        §10's consistency requirement, proven by RE-DERIVING the update from
        the snapshot. If the recorded inputs reproduce the recorded outputs,
        the snapshot describes the arithmetic that ran.
        """
        submission = self.submit(status="wrong_answer")
        shadow.apply_submission(submission)
        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)

        rating, rd, _volatility = glicko.rate(
            snapshot.learner_rating_before,
            snapshot.learner_rd_before,
            snapshot.learner_volatility_before,
            [(snapshot.question_rating_before, snapshot.question_rd_before,
              0.0)],
            periods_inactive=snapshot.learner_periods_inactive)

        self.assertAlmostEqual(rating, snapshot.learner_rating_after, places=9)
        self.assertAlmostEqual(rd, snapshot.learner_rd_after, places=9)

    def test_after_state_matches_the_live_rows(self):
        submission = self.submit()
        shadow.apply_submission(submission)

        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)
        skill = LearnerTopicSkill.objects.get(user=self.learner,
                                              topic=self.topic)
        self.assertAlmostEqual(snapshot.learner_rating_after, skill.rating,
                               places=9)
        self.assertAlmostEqual(snapshot.learner_rd_after,
                               skill.rating_deviation, places=9)

    def test_inactivity_periods_are_recorded(self):
        question = self.question()
        first = self.submit(question)
        start = timezone.now() - timedelta(days=10)
        shadow.apply_submission(first, now=start)

        second = self.submit(question)
        shadow.apply_submission(second, now=start + timedelta(days=4))

        latest = GlickoSnapshot.objects.order_by(
            "-submission_id_value").first()
        self.assertAlmostEqual(latest.learner_periods_inactive, 4.0, places=6)

    def test_glicko_version_is_stamped(self):
        submission = self.submit()
        shadow.apply_submission(submission)
        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)
        self.assertEqual(snapshot.glicko_version, glicko.IMPLEMENTATION_VERSION)

    def test_multiple_topics_stay_isolated(self):
        first = self.question(title="A", topic=self.topic)
        second = self.question(title="B", topic=self.other_topic)
        shadow.apply_submission(self.submit(first))
        shadow.apply_submission(self.submit(second))

        topics = set(GlickoSnapshot.objects.values_list("topic_id", flat=True))
        self.assertEqual(topics, {self.topic.pk, self.other_topic.pk})

    def test_learners_stay_isolated(self):
        other = self.User.objects.create_user(
            username="gh2", email="gh2@t.test", password="Pv#2026xyz")
        question = self.question()
        shadow.apply_submission(self.submit(question))
        shadow.apply_submission(self.submit(question, user=other))

        self.assertEqual(
            glicko_history.history_for(self.learner, self.topic).count(), 1)
        self.assertEqual(
            glicko_history.history_for(other, self.topic).count(), 1)


# ═════════════════════════════════════════════════════════════
# Trust boundary — P2.7c must not weaken
# ═════════════════════════════════════════════════════════════

class TrustBoundaryTests(GlickoHistoryTestCase):

    def test_ineligible_submission_records_no_snapshot(self):
        shadow.apply_submission(self.submit(eligible=False))
        self.assertEqual(GlickoSnapshot.objects.count(), 0)

    def test_compile_error_records_no_snapshot(self):
        """P2.8b: not evidence about the learner, so no rating moved."""
        for status in ("compile_error", "runtime_error", "time_limit"):
            shadow.apply_submission(self.submit(status=status))
        self.assertEqual(GlickoSnapshot.objects.count(), 0)

    def test_wrong_answer_does_record_a_snapshot(self):
        shadow.apply_submission(self.submit(status="wrong_answer"))
        self.assertEqual(GlickoSnapshot.objects.count(), 1)

    def test_a_snapshot_exists_exactly_when_a_rating_moved(self):
        """The invariant tying history to state: no snapshot without an update,
        and no update without a snapshot."""
        self.submit(eligible=False)
        self.submit(status="compile_error")
        shadow.apply_submission(self.submit())

        self.assertEqual(GlickoSnapshot.objects.count(),
                         LearnerTopicSkill.objects.get(
                             user=self.learner, topic=self.topic
                         ).evidence_count)


# ═════════════════════════════════════════════════════════════
# Leakage — the reason the phase exists
# ═════════════════════════════════════════════════════════════

class LeakageTests(GlickoHistoryTestCase):

    def snapshot(self, status="accepted"):
        submission = self.submit(status=status)
        shadow.apply_submission(submission)
        return GlickoSnapshot.objects.get(submission_id_value=submission.pk)

    def test_default_features_are_pre_interaction_only(self):
        features = glicko_history.kt_features(self.snapshot())
        self.assertEqual(set(features), glicko_history.KT_ADMISSIBLE_FIELDS)
        for name in features:
            self.assertNotIn("after", name)

    def test_the_post_interaction_field_set_is_populated(self):
        """
        A loop over an empty set passes vacuously.

        The test below iterates POST_INTERACTION_FIELDS; emptying that
        frozenset made it assert nothing at all, and a mutation sweep caught
        exactly that. The membership is pinned here so the loop can never be
        silently disarmed.
        """
        self.assertEqual(
            glicko_history.POST_INTERACTION_FIELDS,
            {"learner_rating_after", "learner_rd_after",
             "question_rating_after", "question_rd_after"})
        self.assertEqual(
            glicko_history.KT_ADMISSIBLE_FIELDS
            & glicko_history.POST_INTERACTION_FIELDS, set(),
            "a field cannot be both admissible and post-interaction")

    def test_requesting_post_interaction_state_raises_for_the_right_reason(self):
        """
        The MESSAGE is asserted, not just the exception.

        Both guards in `kt_features` raise `PostInteractionLeakage`: one for a
        field that encodes the outcome, one for a field nobody has vetted.
        Asserting only "it raised" cannot tell them apart, and a mutation sweep
        showed the outcome-guard could be deleted entirely while the
        unknown-field branch kept the test green — a defence in depth quietly
        becoming a defence in one.
        """
        snapshot = self.snapshot()
        for field in sorted(glicko_history.POST_INTERACTION_FIELDS):
            with self.assertRaises(
                    glicko_history.PostInteractionLeakage) as caught:
                glicko_history.kt_features(snapshot, fields=[field])
            self.assertIn("hands the model its own label", str(caught.exception),
                          f"{field} was refused as an UNKNOWN field rather "
                          f"than as post-interaction state")

    def test_a_mixed_request_raises_rather_than_silently_filtering(self):
        """
        Dropping the bad field and returning the rest would let a caller
        believe they got what they asked for.
        """
        with self.assertRaises(glicko_history.PostInteractionLeakage):
            glicko_history.kt_features(
                self.snapshot(),
                fields=["learner_rating_before", "learner_rating_after"])

    def test_unknown_fields_raise(self):
        with self.assertRaises(glicko_history.PostInteractionLeakage):
            glicko_history.kt_features(self.snapshot(),
                                       fields=["learner_rating_next_week"])

    def test_after_state_provably_encodes_the_outcome(self):
        """
        ADVERSARIAL, and the justification for the whole guard.

        Two identical learners, one correct and one wrong. Their PRE-interaction
        state is identical; their POST-interaction state differs in a direction
        that reveals the answer. A model given `*_after` would score perfectly
        by reading the label.
        """
        winner = self.User.objects.create_user(
            username="win", email="w@t.test", password="Pv#2026xyz")
        loser = self.User.objects.create_user(
            username="lose", email="l@t.test", password="Pv#2026xyz")
        question = self.question()

        shadow.apply_submission(
            self.submit(question, status="accepted", user=winner))
        shadow.apply_submission(
            self.submit(question, status="wrong_answer", user=loser))

        won = GlickoSnapshot.objects.get(user=winner)
        lost = GlickoSnapshot.objects.get(user=loser)

        # Identical before...
        self.assertEqual(won.learner_rating_before, lost.learner_rating_before)
        # ...and after, the outcome is legible.
        self.assertGreater(won.learner_rating_after, won.learner_rating_before)
        self.assertLess(lost.learner_rating_after, lost.learner_rating_before)
        self.assertNotEqual(won.learner_rating_after, lost.learner_rating_after)

        # Which is exactly why the extractor cannot return it. Compared on the
        # LEARNER fields only: the question side legitimately differs, because
        # the winner's update moved the shared question's rating and clock
        # before the loser attempted it. That divergence is real history, not
        # leakage — it reflects what was known at each moment.
        learner_fields = [f for f in glicko_history.KT_ADMISSIBLE_FIELDS
                          if f.startswith("learner_")]
        self.assertEqual(
            glicko_history.kt_features(won, fields=learner_fields),
            glicko_history.kt_features(lost, fields=learner_fields))

    def test_there_is_no_flag_that_admits_post_interaction_state(self):
        """
        Checked on the SIGNATURE, not the source text.

        A first version grepped for "allow_after" and failed — because
        `kt_features`' own docstring says "there is deliberately no
        `allow_after`". Prose describing the absence of a thing is not the
        thing, and this is the fifth phase in which a text-matching guard has
        been defeated that way. Parameters are what a caller can actually pass.
        """
        parameters = set(inspect.signature(
            glicko_history.kt_features).parameters)
        self.assertEqual(parameters, {"snapshot", "fields"})

        for name in ("allow_after", "include_all", "strict", "include_post",
                     "unsafe", "force"):
            self.assertNotIn(name, parameters)


# ═════════════════════════════════════════════════════════════
# Immutability and idempotency
# ═════════════════════════════════════════════════════════════

class ImmutabilityTests(GlickoHistoryTestCase):

    def test_a_snapshot_cannot_be_edited(self):
        submission = self.submit()
        shadow.apply_submission(submission)
        snapshot = GlickoSnapshot.objects.get(
            submission_id_value=submission.pk)

        snapshot.learner_rating_before = 9999.0
        with self.assertRaises(ValidationError):
            snapshot.save()

    def test_one_interaction_yields_at_most_one_snapshot(self):
        submission = self.submit()
        shadow.apply_submission(submission)
        shadow.apply_submission(submission)      # retry
        self.assertEqual(
            GlickoSnapshot.objects.filter(
                submission_id_value=submission.pk).count(), 1)

    def test_a_rolled_back_update_leaves_no_snapshot(self):
        """History and state roll back together or not at all."""
        submission = self.submit()
        try:
            with transaction.atomic():
                shadow.apply_submission(submission)
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        self.assertEqual(GlickoSnapshot.objects.count(), 0)
        self.assertEqual(LearnerTopicSkill.objects.count(), 0)

    def test_shadow_failure_still_cannot_reach_the_learner(self):
        """P2.9a's guarantee, re-verified now that the path writes more."""
        submission = self.submit()
        submission.question = None       # forces failure inside the update
        self.assertIsNone(shadow.record_submission_safely(submission))


# ═════════════════════════════════════════════════════════════
# Gap detection
# ═════════════════════════════════════════════════════════════

class GapDetectionTests(GlickoHistoryTestCase):

    def test_a_complete_history_reports_no_gaps(self):
        question = self.question()
        for _ in range(4):
            shadow.apply_submission(self.submit(question))
        self.assertEqual(
            glicko_history.detect_gaps(self.learner, self.topic), [])

    def test_an_unrecorded_update_is_detected(self):
        """
        A rating that moved without a snapshot. Exact, not heuristic: rating is
        constant between updates, so a mismatch is proof.
        """
        question = self.question()
        shadow.apply_submission(self.submit(question))

        # An update whose snapshot never landed.
        skill = LearnerTopicSkill.objects.get(user=self.learner,
                                              topic=self.topic)
        skill.rating += 25.0
        skill.save(update_fields=["rating"])

        shadow.apply_submission(self.submit(question))
        gaps = glicko_history.detect_gaps(self.learner, self.topic)
        self.assertTrue(gaps)
        self.assertIn("not recorded", gaps[0])


# ═════════════════════════════════════════════════════════════
# Behaviour preservation
# ═════════════════════════════════════════════════════════════

class NoBehaviourChangeTests(GlickoHistoryTestCase):

    def test_ratings_are_unchanged_by_the_addition_of_history(self):
        """
        Recording state must not alter it. The expected values come from
        calling `glicko.rate` directly with the documented inputs.
        """
        question = self.question(difficulty=1300.0)
        submission = self.submit(question, status="accepted")
        shadow.apply_submission(submission)

        expected = glicko.rate(
            glicko.DEFAULT_RATING, glicko.DEFAULT_RD, glicko.DEFAULT_VOLATILITY,
            [(1300.0, glicko.DEFAULT_RD, 1.0)], periods_inactive=0.0)

        skill = LearnerTopicSkill.objects.get(user=self.learner,
                                              topic=self.topic)
        self.assertAlmostEqual(skill.rating, expected[0], places=9)
        self.assertAlmostEqual(skill.rating_deviation, expected[1], places=9)

    def test_snapshot_write_costs_no_extra_state_read(self):
        """
        Performance: the snapshot reuses the locals the update already had.

        Asserts the PROPERTY rather than a magic total. An absolute count is
        brittle — `get_or_create` costs 4 statements when it inserts and 1 when
        it finds — and it would not say which query was the expensive one. What
        matters on a 512 MB instance is that recording history adds no second
        READ of the rating rows.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        question = self.question()
        shadow.apply_submission(self.submit(question))       # warm the rows

        second = self.submit(question)
        with CaptureQueriesContext(connection) as captured:
            shadow.apply_submission(second)

        statements = [q["sql"] for q in captured.captured_queries]
        snapshot_statements = [s for s in statements if "glickosnapshot" in s]

        # Exactly two: the idempotency check and the insert.
        self.assertEqual(len(snapshot_statements), 2, statements)

        # And no SELECT of the skill rows AFTER they were updated — which is
        # what re-reading "before" state would look like.
        after_update = statements[
            max(i for i, s in enumerate(statements) if s.startswith("UPDATE")):]
        self.assertEqual(
            [s for s in after_update
             if s.startswith("SELECT") and "learnertopicskill" in s], [],
            "the skill row was re-read after being updated")

    def test_question_skill_is_updated_exactly_as_before(self):
        question = self.question(difficulty=1200.0)
        shadow.apply_submission(self.submit(question, status="accepted"))
        skill = QuestionSkill.objects.get(question=question)
        self.assertLess(skill.rating, 1200.0)      # learner won, item eased


# ═════════════════════════════════════════════════════════════
# Structural guards
# ═════════════════════════════════════════════════════════════

FORBIDDEN = {"expected_output", "hidden_test_cases", "trust_state", "status",
             "adaptive_eligible", "content", "is_active", "review_state"}


def _writes(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned = set()

    def names(node):
        if isinstance(node, ast.Attribute):
            return [node.attr]
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return [key.value]
            return []
        if isinstance(node, (ast.Tuple, ast.List)):
            return [n for e in node.elts for n in names(e)]
        return []

    def visit(node, inside):
        entering = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        scope = inside or entering
        if scope:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                assigned.update(names(target))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, False)
    return assigned


class StructuralGuardTests(TestCase):

    def test_history_module_touches_no_grading_truth(self):
        self.assertEqual(
            _writes(inspect.getfile(glicko_history)) & FORBIDDEN, set())

    def test_history_module_does_not_write_live_glicko_state(self):
        """
        Recording history must never become a second writer of the ratings
        themselves — `shadow.apply_submission` stays the only one.
        """
        assigned = _writes(inspect.getfile(glicko_history))
        for field in ("rating", "rating_deviation", "volatility",
                      "evidence_count", "last_evidence_at"):
            self.assertNotIn(field, assigned)

    def test_no_backfill_exists_anywhere(self):
        """
        Historical Glicko is unrecoverable, so any function claiming to
        reconstruct it would be fabricating data.
        """
        source = pathlib.Path(
            inspect.getfile(glicko_history)).read_text(encoding="utf-8")
        for forbidden in ("def backfill", "def reconstruct", "def replay",
                          "def infer_history"):
            self.assertNotIn(forbidden, source)

    def test_migration_creates_no_rows(self):
        path = (pathlib.Path(inspect.getfile(glicko_history)).parent
                / "migrations" / "0043_glicko_snapshot.py")
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("RunPython", source)
        self.assertNotIn("RunSQL", source)
        self.assertNotIn("bulk_create", source)

    def test_snapshot_is_not_a_foreign_key_to_the_partitioned_table(self):
        """
        Measured in P2.9b: `groups_codesubmission` is range-partitioned, its
        PK is (id, submitted_at), and PostgreSQL rejects a reference to `id`
        alone. A future refactor "tidying" this into a ForeignKey would fail
        at migrate time on a partitioned database.
        """
        field = GlickoSnapshot._meta.get_field("submission_id_value")
        self.assertEqual(field.get_internal_type(), "BigIntegerField")
        self.assertTrue(field.unique)
