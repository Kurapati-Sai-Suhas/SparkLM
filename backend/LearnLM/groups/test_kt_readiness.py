"""
KT data readiness, causality and gate (M2 P2.10a).

Two things are being tested, and the second matters more:

  1. The census counts what the filtering contract says it counts.
  2. The leakage audit REFUSES data that would produce a good-looking,
     meaningless result — tested by deliberately constructing that data.

A leakage detector that has never been shown a leak is decoration.
"""

import ast
import inspect
import json
import pathlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from groups import kt_features, kt_leakage, kt_readiness
from groups.kt_leakage import Interaction
from groups.models import CodeSubmission, CodingPortal, Question, Topic


#: One base for the whole module, fixed at import.
#:
#: `at()` used to call `timezone.now()` on every invocation, which made the
#: offsets drift against each other. `test_split_is_temporally_ordered` builds
#: thirty rows and THEN computes the split boundaries, so a clock tick landing
#: between row 20 and the `days=20` boundary put that row a few microseconds
#: before the boundary — into `train` instead of `validation`, giving 21/4/5
#: and a red suite. It passed only while all thirty-two calls fell inside one
#: tick, which is why it failed roughly one full run in three.
_BASE = timezone.now() - timedelta(days=400)


def at(days=0, seconds=0):
    return _BASE + timedelta(days=days, seconds=seconds)


class KTTestCase(TestCase):

    def setUp(self):
        self.User = get_user_model()
        portal = CodingPortal.objects.create(name="KT Portal")
        self.topic = Topic.objects.create(
            name="KTTopic", structure_type="flat", portal=portal)
        self.other_topic = Topic.objects.create(
            name="KTTopic2", structure_type="flat", portal=portal)

    def learner(self, name):
        return self.User.objects.create_user(
            username=name, email=f"{name}@t.test", password="Pv#2026xyz")

    def question(self, title="Q", topic=None,
                 trust=Question.TRUST_UNVERIFIED):
        return Question.objects.create(
            title=title, content="c", topic=topic or self.topic,
            base_difficulty=1200.0,
            hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
            boilerplate_code={"python": "x"}, hidden_wrapper_code={},
            status=Question.STATUS_PUBLISHED, trust_state=trust)

    def submit(self, user, question, *, status="accepted", eligible=True,
               when=None, language="python"):
        submission = CodeSubmission.objects.create(
            user=user, question=question, language=language, code="x",
            status=status, adaptive_eligible=eligible)
        if when is not None:
            # auto_now_add ignores assignment; update() bypasses it and is the
            # only way to build a temporal fixture.
            CodeSubmission.objects.filter(pk=submission.pk).update(
                submitted_at=when)
            submission.refresh_from_db()
        return submission


# ═════════════════════════════════════════════════════════════
# The filtering contract
# ═════════════════════════════════════════════════════════════

class FilterContractTests(KTTestCase):

    def test_zero_eligible_when_nothing_is_trusted(self):
        """The current LearnLM state: submissions exist, none are evidence."""
        learner, question = self.learner("a"), self.question()
        for _ in range(10):
            self.submit(learner, question, eligible=False)

        census = kt_readiness.collect_census()
        self.assertEqual(census.total_interactions, 10)
        self.assertEqual(census.eligible_interactions, 0)
        self.assertEqual(census.eligible_percentage, 0.0)

    def test_total_and_eligible_are_never_conflated(self):
        learner, question = self.learner("a"), self.question()
        for _ in range(7):
            self.submit(learner, question, eligible=False)
        for _ in range(3):
            self.submit(learner, question, eligible=True)

        census = kt_readiness.collect_census()
        self.assertEqual(census.total_interactions, 10)
        self.assertEqual(census.eligible_interactions, 3)
        self.assertEqual(census.ineligible_interactions, 7)
        self.assertEqual(census.eligible_percentage, 30.0)

    def test_orphaned_submission_is_excluded(self):
        """`CodeSubmission.question` is nullable; an item-less row is untraceable."""
        learner = self.learner("a")
        self.submit(learner, self.question(), eligible=True)
        CodeSubmission.objects.create(
            user=learner, question=None, language="python", code="x",
            status="accepted", adaptive_eligible=True)

        self.assertEqual(kt_readiness.collect_census().eligible_interactions, 1)

    def test_non_evaluable_outcomes_are_excluded(self):
        """
        P2.8b's decision reused, not re-made: compile_error and time_limit
        conflate not-knowing with mistyping.
        """
        learner, question = self.learner("a"), self.question()
        self.submit(learner, question, status="accepted")
        self.submit(learner, question, status="wrong_answer")
        for status in ("compile_error", "time_limit", "runtime_error"):
            self.submit(learner, question, status=status)

        census = kt_readiness.collect_census()
        self.assertEqual(census.eligible_interactions, 2)
        self.assertEqual(set(census.outcome_distribution),
                         {"accepted", "wrong_answer"})

    def test_filter_contract_matches_the_sql(self):
        """
        The published contract must describe the query that actually runs.

        Two filters cannot be tested behaviourally: `Question.topic` is NOT
        NULL, so the topic filter can never exclude a row (a proven equivalent
        mutant), and it subsumes the question filter via the inner join. A
        mutation sweep showed both surviving deletion.

        They are still worth pinning, because `kt_data_readiness` PRINTS this
        contract to operators. A queryset that silently stopped matching its
        own published description would make the report lie about which rows
        produced its numbers — so the SQL is asserted directly.
        """
        sql = str(kt_readiness.eligible_interactions().query)

        self.assertIn("adaptive_eligible", sql)
        self.assertIn("accepted", sql)
        self.assertIn("wrong_answer", sql)
        for excluded in ("compile_error", "time_limit", "runtime_error"):
            self.assertNotIn(excluded, sql)

        # Column-qualified, because `question_id` also appears in the JOIN
        # clause — a bare substring check passes even with the filter deleted.
        self.assertIn('"groups_codesubmission"."question_id" IS NOT NULL', sql)
        self.assertIn('"groups_question"."topic_id" IS NOT NULL', sql)

        self.assertEqual(
            len(kt_readiness.FILTER_CONTRACT), 4,
            "the printed contract and the queryset must stay in step")

    def test_census_is_deterministic(self):
        learner = self.learner("a")
        for index in range(5):
            self.submit(learner, self.question(title=f"Q{index}"))

        self.assertEqual(kt_readiness.collect_census().as_dict(),
                         kt_readiness.collect_census().as_dict())


# ═════════════════════════════════════════════════════════════
# Census metrics
# ═════════════════════════════════════════════════════════════

class CensusTests(KTTestCase):

    def test_learner_depth_buckets(self):
        for name, depth in (("deep", 100), ("mid", 20), ("shallow", 3)):
            learner = self.learner(name)
            question = self.question(title=f"Q-{name}")
            for _ in range(depth):
                self.submit(learner, question)

        census = kt_readiness.collect_census()
        self.assertEqual(census.eligible_learners, 3)
        self.assertEqual(census.learners_ge_20, 2)
        self.assertEqual(census.learners_ge_100, 1)
        self.assertEqual(census.cold_start_learners, 1)
        self.assertEqual(census.max_depth, 100)

    def test_topic_and_language_coverage(self):
        learner = self.learner("a")
        self.submit(learner, self.question(title="A", topic=self.topic),
                    language="python")
        self.submit(learner,
                    self.question(title="B", topic=self.other_topic),
                    language="java")

        census = kt_readiness.collect_census()
        self.assertEqual(census.eligible_topics, 2)
        self.assertEqual(set(census.language_distribution), {"python", "java"})

    def test_temporal_span(self):
        learner, question = self.learner("a"), self.question()
        self.submit(learner, question, when=at(days=0))
        self.submit(learner, question, when=at(days=45))

        self.assertEqual(kt_readiness.collect_census().span_days, 45)

    def test_outcome_imbalance_is_measured(self):
        learner, question = self.learner("a"), self.question()
        for _ in range(99):
            self.submit(learner, question, status="accepted")
        self.submit(learner, question, status="wrong_answer")

        census = kt_readiness.collect_census()
        self.assertAlmostEqual(census.minority_outcome_rate, 0.01, places=4)

    def test_oracle_verified_questions_are_counted(self):
        self.question(title="unverified")
        self.question(title="verified", trust=Question.TRUST_ORACLE_VERIFIED)

        census = kt_readiness.collect_census()
        self.assertEqual(census.oracle_verified_questions, 1)
        self.assertEqual(census.questions_without_trustworthy_evidence, 1)


# ═════════════════════════════════════════════════════════════
# Leakage — the tests that matter
# ═════════════════════════════════════════════════════════════

class CausalityTests(TestCase):

    def sequence(self, count=5, learner=1):
        return [Interaction(learner_id=learner, question_id=100 + n,
                            topic_id=1, submitted_at=at(days=n), outcome=n % 2,
                            attempt_number=0,
                            lag_seconds=0.0 if n == 0 else 86400.0)
                for n in range(count)]

    def test_a_clean_sequence_passes(self):
        self.assertTrue(kt_leakage.audit_causality(self.sequence()).is_safe)

    def test_a_feature_computed_from_the_future_is_refused(self):
        """
        The core violation: a feature whose value was determined after the
        interaction it describes.
        """
        rows = self.sequence()
        rows[1] = Interaction(**{**rows[1].__dict__,
                                 "feature_asof": at(days=99)})
        report = kt_leakage.audit_causality(rows)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("AFTER submitted_at" in p for p in report.problems))

    def test_out_of_order_sequence_is_refused(self):
        rows = self.sequence()
        rows[0], rows[3] = rows[3], rows[0]
        report = kt_leakage.audit_causality(rows)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("chronological" in p for p in report.problems))

    def test_total_attempt_count_is_refused(self):
        """
        `attempt_number` must count PRIOR attempts. A total count tells the
        model how many attempts follow — which is the answer.
        """
        rows = [
            Interaction(1, 100, 1, at(days=0), 0, attempt_number=3,
                        lag_seconds=0.0),
            Interaction(1, 100, 1, at(days=1), 1, attempt_number=3,
                        lag_seconds=86400.0),
        ]
        report = kt_leakage.audit_causality(rows)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("prior attempts" in p for p in report.problems))

    def test_negative_lag_is_refused(self):
        rows = self.sequence()
        rows[2] = Interaction(**{**rows[2].__dict__, "lag_seconds": -60.0})
        report = kt_leakage.audit_causality(rows)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("negative lag" in p for p in report.problems))

    def test_first_interaction_cannot_have_a_lag(self):
        rows = self.sequence()
        rows[0] = Interaction(**{**rows[0].__dict__, "lag_seconds": 500.0})
        report = kt_leakage.audit_causality(rows)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("no previous interaction" in p
                            for p in report.problems))


class TemporalSplitTests(TestCase):

    def rows(self, count=30):
        return [Interaction(learner_id=n % 3, question_id=n, topic_id=1,
                            submitted_at=at(days=n), outcome=n % 2,
                            attempt_number=0, lag_seconds=0.0)
                for n in range(count)]

    def test_split_is_temporally_ordered(self):
        train, validation, test = kt_leakage.temporal_split(
            self.rows(), at(days=20), at(days=25))
        self.assertTrue(kt_leakage.audit_split(train, validation, test).is_safe)
        self.assertEqual(len(train), 20)
        self.assertEqual(len(validation), 5)
        self.assertEqual(len(test), 5)

    def test_learners_may_span_buckets(self):
        """Correct and intended — it is the production question."""
        train, validation, test = kt_leakage.temporal_split(
            self.rows(), at(days=20), at(days=25))
        self.assertTrue({r.learner_id for r in train}
                        & {r.learner_id for r in test})

    def test_a_shuffled_split_is_detected(self):
        """
        The failure this module exists to prevent: random row assignment,
        which produces an excellent AUC that means nothing.
        """
        rows = self.rows()
        train = rows[0::2]          # interleaved, not temporal
        validation = rows[1::4]
        test = rows[3::4]
        report = kt_leakage.audit_split(train, validation, test)
        self.assertFalse(report.is_safe)

    def test_only_train_validation_overlap_is_detected(self):
        """
        Each boundary check is isolated, because they mask each other.

        A shuffled split violates all three orderings at once, so any ONE
        surviving check makes the test pass and the other two look load-bearing
        when they are not — a mutation sweep found exactly that, with the
        train/validation and train/test checks each surviving deletion. These
        three tests each violate one boundary and leave the others intact.
        """
        rows = self.rows()
        train = [r for r in rows if r.submitted_at < at(days=22)]
        validation = [r for r in rows
                      if at(days=20) <= r.submitted_at < at(days=25)]
        test = [r for r in rows if r.submitted_at >= at(days=25)]

        report = kt_leakage.audit_split(train, validation, test)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("validation start" in p for p in report.problems),
                        report.problems)

    def test_only_validation_test_overlap_is_detected(self):
        rows = self.rows()
        train = [r for r in rows if r.submitted_at < at(days=20)]
        validation = [r for r in rows
                      if at(days=20) <= r.submitted_at < at(days=27)]
        test = [r for r in rows if r.submitted_at >= at(days=25)]

        report = kt_leakage.audit_split(train, validation, test)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("test start" in p for p in report.problems),
                        report.problems)

    def test_only_train_test_overlap_is_detected(self):
        """
        Train overlaps test with **no validation split at all**.

        This is the only configuration in which the train/test check can fire
        alone. Whenever validation is non-empty and ordered, `val_lo <=
        test_lo`, so train reaching into test necessarily reaches into
        validation first and the earlier check fires — making train/test look
        redundant. A mutation sweep proved that: deleting it survived a test
        that had a populated validation split.
        """
        rows = self.rows()
        train = [r for r in rows if r.submitted_at < at(days=25)]
        test = [r for r in rows if r.submitted_at >= at(days=20)]

        report = kt_leakage.audit_split(train, [], test)
        self.assertFalse(report.is_safe)
        self.assertTrue(any("test start" in p for p in report.problems),
                        report.problems)

    def test_duplicated_interaction_across_buckets_is_detected(self):
        rows = self.rows()
        train, validation, test = kt_leakage.temporal_split(
            rows, at(days=20), at(days=25))
        report = kt_leakage.audit_split(train, validation, test + train[:1])
        self.assertFalse(report.is_safe)
        self.assertTrue(any("both" in p for p in report.problems))

    def test_empty_test_split_is_refused(self):
        report = kt_leakage.audit_split(self.rows(), [], [])
        self.assertFalse(report.is_safe)
        self.assertTrue(any("test split is empty" in p
                            for p in report.problems))

    def test_there_is_no_random_split_function(self):
        """
        Prevention by absence. A `random_split` that merely warned would
        eventually be used.
        """
        for forbidden in ("random_split", "shuffle_split", "stratified_split"):
            self.assertFalse(hasattr(kt_leakage, forbidden))

    def test_inverted_boundaries_are_rejected(self):
        with self.assertRaises(ValueError):
            kt_leakage.temporal_split(self.rows(), at(days=25), at(days=20))


# ═════════════════════════════════════════════════════════════
# The gate
# ═════════════════════════════════════════════════════════════

class GateTests(KTTestCase):

    def test_empty_database_is_not_ready(self):
        gate = kt_readiness.evaluate_gate(kt_readiness.collect_census())
        self.assertEqual(gate.verdict, kt_readiness.NOT_READY)
        self.assertTrue(any("zero eligible" in r for r in gate.reasons))

    def test_no_oracle_verified_question_is_reported_as_the_root_cause(self):
        learner = self.learner("a")
        self.submit(learner, self.question(), eligible=False)
        gate = kt_readiness.evaluate_gate(kt_readiness.collect_census())
        self.assertTrue(any("ORACLE_VERIFIED" in r for r in gate.reasons))

    def test_insufficient_depth_blocks_research_ready(self):
        for index in range(60):
            learner = self.learner(f"L{index}")
            self.submit(learner, self.question(title=f"Q{index}"))

        gate = kt_readiness.evaluate_gate(kt_readiness.collect_census())
        self.assertEqual(gate.verdict, kt_readiness.NOT_READY)

    def test_outcome_imbalance_blocks(self):
        census = kt_readiness.Census(
            eligible_interactions=100_000, eligible_learners=1_000,
            learners_ge_20=800, eligible_questions=500, eligible_topics=30,
            span_days=200, cold_start_learners=100,
            oracle_verified_questions=500, minority_outcome_rate=0.01)
        gate = kt_readiness.evaluate_gate(census)
        self.assertEqual(gate.verdict, kt_readiness.NOT_READY)
        self.assertTrue(any("minority outcome" in r for r in gate.reasons))

    def test_leakage_blocks_regardless_of_volume(self):
        """Volume never buys past a causality violation."""
        census = kt_readiness.Census(
            eligible_interactions=1_000_000, eligible_learners=10_000,
            learners_ge_20=9_000, eligible_questions=5_000, eligible_topics=100,
            span_days=900, cold_start_learners=500,
            oracle_verified_questions=5_000, minority_outcome_rate=0.4)
        unsafe = kt_leakage.LeakageReport(problems=["fabricated"])
        gate = kt_readiness.evaluate_gate(census, leakage=unsafe)
        self.assertEqual(gate.verdict, kt_readiness.NOT_READY)

    def test_research_ready_is_reachable(self):
        census = kt_readiness.Census(
            eligible_interactions=10_000, eligible_learners=100,
            learners_ge_20=40, eligible_questions=100, eligible_topics=10,
            span_days=60, cold_start_learners=10,
            oracle_verified_questions=100, minority_outcome_rate=0.35)
        gate = kt_readiness.evaluate_gate(census)
        self.assertEqual(gate.verdict, kt_readiness.RESEARCH_READY)

    def test_training_ready_is_reachable(self):
        census = kt_readiness.Census(
            eligible_interactions=200_000, eligible_learners=2_000,
            learners_ge_20=1_500, eligible_questions=800, eligible_topics=40,
            span_days=365, cold_start_learners=300,
            oracle_verified_questions=800, minority_outcome_rate=0.4)
        gate = kt_readiness.evaluate_gate(census)
        self.assertEqual(gate.verdict, kt_readiness.TRAINING_READY)

    def test_thresholds_are_configurable_not_hardcoded(self):
        census = kt_readiness.Census(
            eligible_interactions=10, eligible_learners=2, learners_ge_20=1,
            eligible_questions=2, eligible_topics=1, span_days=1,
            cold_start_learners=1, oracle_verified_questions=2,
            minority_outcome_rate=0.5)
        relaxed = kt_readiness.ReadinessThresholds(
            research_interactions=5, research_learners=1, research_depth=1,
            research_questions=1, research_topics=1, research_days=0)
        self.assertEqual(
            kt_readiness.evaluate_gate(census, relaxed).verdict,
            kt_readiness.RESEARCH_READY)


# ═════════════════════════════════════════════════════════════
# The command
# ═════════════════════════════════════════════════════════════

class CommandTests(KTTestCase):

    def test_report_runs_on_an_empty_database(self):
        call_command("kt_data_readiness")

    def test_json_output_is_parseable_and_complete(self):
        from io import StringIO
        learner, question = self.learner("a"), self.question()
        self.submit(learner, question)

        buffer = StringIO()
        call_command("kt_data_readiness", "--json", "--features",
                     stdout=buffer)
        payload = json.loads(buffer.getvalue())

        for key in ("census", "gate", "leakage", "filter_contract", "features"):
            self.assertIn(key, payload)
        self.assertEqual(payload["census"]["eligible_interactions"], 1)
        self.assertEqual(payload["gate"]["verdict"], kt_readiness.NOT_READY)

    def test_the_command_writes_nothing(self):
        learner, question = self.learner("a"), self.question()
        for _ in range(5):
            self.submit(learner, question)

        before = {
            "submissions": CodeSubmission.objects.count(),
            "questions": Question.objects.count(),
            "cases": list(Question.objects.values_list(
                "hidden_test_cases", flat=True)),
            "trust": list(Question.objects.values_list("trust_state", flat=True)),
            "status": list(Question.objects.values_list("status", flat=True)),
            "eligible": list(CodeSubmission.objects.values_list(
                "adaptive_eligible", flat=True)),
        }

        call_command("kt_data_readiness")

        self.assertEqual(CodeSubmission.objects.count(), before["submissions"])
        self.assertEqual(Question.objects.count(), before["questions"])
        self.assertEqual(list(Question.objects.values_list(
            "hidden_test_cases", flat=True)), before["cases"])
        self.assertEqual(list(Question.objects.values_list(
            "trust_state", flat=True)), before["trust"])
        self.assertEqual(list(Question.objects.values_list(
            "status", flat=True)), before["status"])
        self.assertEqual(list(CodeSubmission.objects.values_list(
            "adaptive_eligible", flat=True)), before["eligible"])

    def test_report_is_reproducible(self):
        from io import StringIO
        learner = self.learner("a")
        for index in range(4):
            self.submit(learner, self.question(title=f"Q{index}"))

        outputs = []
        for _ in range(2):
            buffer = StringIO()
            call_command("kt_data_readiness", "--json", stdout=buffer)
            outputs.append(json.loads(buffer.getvalue()))
        self.assertEqual(outputs[0], outputs[1])

    def test_command_has_no_write_flags(self):
        from groups.management.commands.kt_data_readiness import Command
        parser = Command().create_parser("manage.py", "kt_data_readiness")
        flags = {action.dest for action in parser._actions}
        for forbidden in ("apply", "fix", "write", "train", "confirm",
                          "promote", "seed", "generate"):
            self.assertNotIn(forbidden, flags)


# ═════════════════════════════════════════════════════════════
# Feature inventory
# ═════════════════════════════════════════════════════════════

class FeatureInventoryTests(TestCase):

    def test_execution_time_is_never_treated_as_deliberation_time(self):
        """
        The specific confusion P2.10a must prevent: `execution_time_ms` is the
        PROGRAM's runtime, not the learner's thinking time.
        """
        by_name = {f.name: f for f in kt_features.FEATURES}
        execution = by_name["execution_time_ms"]
        self.assertIn("EXCLUDE", execution.verdict)
        self.assertIn("UNSAFE", execution.verdict)

        deliberation = by_name["learner_deliberation_time"]
        self.assertEqual(deliberation.status, kt_features.MISSING)
        self.assertIn(deliberation, kt_features.must_have_gaps())

    def test_point_in_time_glicko_is_available_only_going_forward(self):
        """
        M2 P2.9b started recording it. Interactions predating that remain
        permanently unrecoverable and must stay MISSING — not DERIVABLE, since
        replay reconstructs a plausible history rather than the actual one.
        """
        by_name = {f.name: f for f in kt_features.FEATURES}
        for name in ("glicko_rating_before", "glicko_rd_before",
                     "glicko_period_before"):
            self.assertEqual(by_name[name].status, kt_features.AVAILABLE)
        self.assertEqual(
            by_name["glicko_rating_at_interaction_time_historical"].status,
            kt_features.MISSING)

    def test_post_interaction_glicko_is_marked_unsafe(self):
        by_name = {f.name: f for f in kt_features.FEATURES}
        verdict = by_name["glicko_rating_after"].verdict
        self.assertIn("EXCLUDE", verdict)
        self.assertIn("UNSAFE", verdict)

    def test_live_glicko_is_not_a_model_input(self):
        by_name = {f.name: f for f in kt_features.FEATURES}
        self.assertIn("NOT model input", by_name["glicko_rating_live"].verdict)

    def test_inventory_is_machine_readable(self):
        payload = kt_features.as_dict()
        json.dumps(payload)                      # must serialise
        self.assertTrue(payload["must_have_gaps"])
        self.assertEqual(len(payload["features"]), len(kt_features.FEATURES))

    def test_every_feature_has_a_verdict(self):
        for feature in kt_features.FEATURES:
            self.assertTrue(feature.verdict.strip(), feature.name)
            self.assertTrue(feature.note.strip(), feature.name)


# ═════════════════════════════════════════════════════════════
# Safety boundary
# ═════════════════════════════════════════════════════════════

FORBIDDEN_WRITES = {"expected_output", "hidden_test_cases", "trust_state",
                    "status", "adaptive_eligible", "content", "is_active",
                    "review_state", "rating", "rating_deviation", "accuracy"}


def _writes(path):
    """Assignments and persistence calls inside function bodies of `path`."""
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned, called = set(), set()

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

    def persistence(func):
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr in {"save", "delete"}:
            return func.attr
        if func.attr in {"create", "update", "bulk_create", "bulk_update",
                         "get_or_create", "update_or_create"}:
            receiver = func.value
            if isinstance(receiver, ast.Attribute) and receiver.attr == "objects":
                return f"objects.{func.attr}"
        return None

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
            if isinstance(node, ast.Call):
                found = persistence(node.func)
                if found:
                    called.add(found)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, False)
    return assigned, called


KT_MODULES = (kt_readiness, kt_leakage, kt_features)


class SafetyBoundaryTests(TestCase):

    def test_no_kt_module_writes_anything(self):
        for module in KT_MODULES:
            assigned, called = _writes(inspect.getfile(module))
            self.assertEqual(
                assigned & FORBIDDEN_WRITES, set(),
                f"{module.__name__} assigns to protected state")
            self.assertEqual(
                called, set(),
                f"{module.__name__} performs database persistence")

    def test_the_command_writes_nothing(self):
        from groups.management.commands import kt_data_readiness
        assigned, called = _writes(inspect.getfile(kt_data_readiness))
        self.assertEqual(assigned & FORBIDDEN_WRITES, set())
        self.assertEqual(called, set())

    def test_kt_package_does_not_import_torch(self):
        """
        The web-tier constraint, enforced structurally. `requirements.txt:53`
        keeps the web tier torch-free and P2.10's architecture depends on that
        remaining true — training is offline and out-of-process.
        """
        for module in KT_MODULES + (
                __import__("groups.management.commands.kt_data_readiness",
                           fromlist=["Command"]),):
            source = pathlib.Path(
                inspect.getfile(module)).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0],
                                         {"torch", "transformers"})
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0],
                                     {"torch", "transformers"})

    def test_torch_is_not_in_the_web_tier_requirements(self):
        root = pathlib.Path(inspect.getfile(kt_readiness)).parents[2]
        web = (root / "requirements.txt").read_text(encoding="utf-8")
        for line in web.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                self.assertNotIn("torch", stripped.split("=")[0].lower())

    def test_guard_catches_a_real_write(self):
        """A guard that cannot fail proves nothing."""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("def f(q):\n    q.trust_state = 'X'\n    q.save()\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        assigned, called = _writes(path)
        self.assertIn("trust_state", assigned)
        self.assertIn("save", called)
