from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from learning.router import OSCILLATION_Z_THRESHOLD, outcome_stats, runs_test_z
from .hybrid_router import RoutingClassifier
from .models import Question, Topic

User = get_user_model()


class RunsTestStatisticTests(TestCase):
    """learning.router.runs_test_z — frozen definitions §6.2."""

    def test_short_windows_are_neutral(self):
        # §6.2: n < 5 carries no pattern evidence — z is exactly 0.
        self.assertEqual(runs_test_z([]), 0.0)
        self.assertEqual(runs_test_z([1, 0, 1, 0]), 0.0)

    def test_uniform_outcomes_are_neutral(self):
        # Single-class sequences leave the statistic undefined — neutral.
        self.assertEqual(runs_test_z([1] * 10), 0.0)
        self.assertEqual(runs_test_z([0] * 10), 0.0)

    def test_alternation_is_strongly_positive(self):
        # Perfect pass/fail alternation over 20: R=20, E[R]=11 -> z≈+4.14.
        self.assertAlmostEqual(runs_test_z([1, 0] * 10), 4.135, places=2)

    def test_blocks_are_strongly_negative(self):
        # One failure block, one success block: R=2 -> z≈-4.14 (streaky).
        self.assertAlmostEqual(runs_test_z([0] * 10 + [1] * 10), -4.135, places=2)

    def test_outcome_stats_returns_the_serve_triple(self):
        avg, z, n = outcome_stats([1, 1, 0, 1, 1, 1, 0, 1, 1, 1])
        self.assertAlmostEqual(avg, 0.8)
        self.assertEqual(n, 10)
        self.assertNotEqual(z, 0.0)


class HybridRouterHeuristicTests(TestCase):
    """
    FR-RTR-02 v2 (frozen architecture §6.2): struggling users — outcome
    oscillation beyond chance (runs-test z > 1.96) OR low accuracy
    (< 0.60) — route to the FLAT Elo engine to rebuild confidence.
    Consistent or streaky-but-performing users route to the HIERARCHICAL
    DAG. (v1 routed on np.var of binary outcomes = p(1-p), which is
    determined by the mean and falsely flagged streaky learners in the
    0.60–0.72 accuracy band.)
    """

    def _heuristic_router(self):
        router = RoutingClassifier()
        router.clf = None  # force the fallback heuristic
        return router

    def test_oscillating_outcomes_route_flat(self):
        z = runs_test_z([1, 0] * 10)
        self.assertGreater(z, OSCILLATION_Z_THRESHOLD)
        # Oscillation routes flat even at healthy accuracy.
        self.assertEqual(self._heuristic_router().predict_route(0.8, z, 0.7), 'flat')

    def test_low_accuracy_routes_flat_regardless_of_pattern(self):
        self.assertEqual(self._heuristic_router().predict_route(0.4, 0.0, 0.7), 'flat')

    def test_streaky_learner_is_not_falsely_flat(self):
        # 6 fails then 14 passes — a breakthrough, not erratic guessing.
        # v1: variance 0.21 > 0.20 -> forced flat. v2: z << 0, accuracy
        # 0.70 >= 0.60 -> the learner advances. The M5 headline case.
        outcomes = [0] * 6 + [1] * 14
        avg, z, _ = outcome_stats(outcomes)
        self.assertAlmostEqual(avg, 0.70)
        self.assertLess(z, 0.0)
        self.assertEqual(self._heuristic_router().predict_route(avg, z, 0.7), 'hierarchical')

    def test_consistent_performer_routes_hierarchical(self):
        self.assertEqual(self._heuristic_router().predict_route(0.8, 0.3, 0.7), 'hierarchical')


class RoutingTelemetryTests(TestCase):
    """
    FR-RTR-01 v2: the Traffic Cop routes on the mean and runs-test
    streakiness of correctness over the user's last 20 submissions —
    computed from real rows, in chronological order (the runs test reads
    sequence order; the old variance never did).
    """

    def setUp(self):
        from .models import CodingPortal
        self.user = User.objects.create_user(
            username='telemetryuser', password='testpassword123', email='tel@test.com'
        )
        portal = CodingPortal.objects.create(name="Telemetry Portal")
        topic = Topic.objects.create(name="TelemetryArrays", structure_type="flat", portal=portal)
        self.question = Question.objects.create(
            topic=topic, title="Telemetry Q", content="c", base_difficulty=1200.0
        )

    def _submit(self, sub_status):
        from .models import CodeSubmission
        CodeSubmission.objects.create(
            user=self.user, question=self.question,
            language='python', code='x', status=sub_status,
        )

    def _telemetry(self):
        from .hybrid_router import compute_routing_telemetry
        return compute_routing_telemetry(self.user)

    def _heuristic_router(self):
        router = RoutingClassifier()
        router.clf = None
        return router

    def test_cold_start_routes_hierarchical(self):
        avg_acc, runs_z, n = self._telemetry()
        self.assertEqual(n, 0)
        self.assertEqual(runs_z, 0.0)  # pattern-neutral default
        self.assertEqual(self._heuristic_router().predict_route(avg_acc, runs_z, 0.6), 'hierarchical')

    def test_erratic_history_routes_flat(self):
        # Alternating pass/fail over 10 submissions: R=10, E[R]=6 -> z≈+2.68.
        for i in range(10):
            self._submit('accepted' if i % 2 == 0 else 'wrong_answer')

        avg_acc, runs_z, n = self._telemetry()
        self.assertEqual(n, 10)
        self.assertAlmostEqual(runs_z, 2.68, places=2)
        self.assertEqual(self._heuristic_router().predict_route(avg_acc, runs_z, 0.6), 'flat')

    def test_streaky_history_is_not_falsely_flat(self):
        # DB-level M5 headline: 6 fails then 14 passes. The old statistic
        # (var 0.21 > 0.20) trapped this learner in flat practice; the
        # runs test reads the block pattern as a breakthrough and advances.
        for _ in range(6):
            self._submit('wrong_answer')
        for _ in range(14):
            self._submit('accepted')

        avg_acc, runs_z, n = self._telemetry()
        self.assertEqual(n, 20)
        self.assertAlmostEqual(avg_acc, 0.70)
        self.assertAlmostEqual(runs_z, -4.09, places=2)
        self.assertEqual(self._heuristic_router().predict_route(avg_acc, runs_z, 0.6), 'hierarchical')

    def test_consistent_history_routes_hierarchical(self):
        for _ in range(10):
            self._submit('accepted')

        avg_acc, runs_z, n = self._telemetry()
        self.assertEqual(n, 10)
        self.assertEqual(avg_acc, 1.0)
        self.assertEqual(runs_z, 0.0)  # single-class window is neutral
        self.assertEqual(self._heuristic_router().predict_route(avg_acc, runs_z, 0.6), 'hierarchical')

    def test_telemetry_only_uses_last_20_submissions(self):
        # 5 old failures followed by 20 clean accepts: the failures fall
        # outside the FR-RTR-01 window and must not affect routing.
        for _ in range(5):
            self._submit('wrong_answer')
        for _ in range(20):
            self._submit('accepted')

        avg_acc, runs_z, n = self._telemetry()
        self.assertEqual(n, 20)
        self.assertEqual(avg_acc, 1.0)
        self.assertEqual(runs_z, 0.0)


class MasteryDefinitionTests(TestCase):
    """SRS FR-HRCH-01: mastery = accuracy >= 0.8 over >= 3 reviews, one shared definition."""

    def test_mastered_topics_use_accuracy_and_review_thresholds(self):
        from .models import CodingPortal, UserTopicMastery
        from .hybrid_router import get_mastered_topic_names

        user = User.objects.create_user(
            username='masteryuser', password='testpassword123', email='m@test.com'
        )
        portal = CodingPortal.objects.create(name="Mastery Portal")
        t_done = Topic.objects.create(name="MasteredTopic", structure_type="hierarchical", portal=portal)
        t_wip  = Topic.objects.create(name="LearningTopic", structure_type="hierarchical", portal=portal)
        t_edge = Topic.objects.create(name="EdgeTopic", structure_type="hierarchical", portal=portal)
        t_luck = Topic.objects.create(name="LuckyTopic", structure_type="hierarchical", portal=portal)

        UserTopicMastery.objects.create(user=user, topic=t_done, accuracy=0.85, reviews=5)
        UserTopicMastery.objects.create(user=user, topic=t_wip, accuracy=0.50, reviews=10)
        UserTopicMastery.objects.create(user=user, topic=t_edge, accuracy=0.80, reviews=3)  # both boundaries: counts
        # One lucky solve — perfect accuracy but only 1 review: must NOT count.
        UserTopicMastery.objects.create(user=user, topic=t_luck, accuracy=1.0, reviews=1)

        mastered = set(get_mastered_topic_names(user))
        self.assertEqual(mastered, {"MasteredTopic", "EdgeTopic"})


class OutcomeClassifierTests(TestCase):
    """
    Phase 3 flywheel + M5 feature contract: the router must learn from
    OUTCOMES on the SAME features it serves with (FEATURES_V2 =
    avg_acc, runs_z, avg_elo, engine_flag) and pick the engine with the
    higher predicted success — not relearn its own past routing.
    """

    def test_predict_route_scores_both_engines(self):
        import numpy as np
        from .management.commands.retrain_ai import train_outcome_classifier

        # Synthetic ground truth with the interaction the model must learn:
        # strong students succeed on the hierarchical track, weak students
        # succeed in flat practice.
        X, y = [], []
        rng = np.random.RandomState(0)
        for _ in range(200):
            acc = rng.uniform(0, 1)
            runs_z = rng.uniform(-3.0, 3.0)
            elo = rng.uniform(0.4, 0.9)
            for engine in (0.0, 1.0):
                if engine == 1.0:
                    success = 1 if acc > 0.6 else 0
                else:
                    success = 1 if acc <= 0.6 else 0
                X.append([acc, runs_z, elo, engine])
                y.append(success)

        clf = train_outcome_classifier(X, y)
        self.assertIsNotNone(clf)

        router = RoutingClassifier()
        router.clf = clf
        self.assertEqual(router.predict_route(0.9, 0.0, 0.6), 'hierarchical')
        self.assertEqual(router.predict_route(0.3, 0.0, 0.6), 'flat')

    def test_train_returns_none_without_outcome_variance(self):
        from .management.commands.retrain_ai import train_outcome_classifier
        # All-success data carries no signal — must refuse to train rather
        # than save a degenerate model.
        X = [[0.5, 0.1, 0.6, 0.0], [0.8, 0.1, 0.7, 1.0]]
        y = [1, 1]
        self.assertIsNone(train_outcome_classifier(X, y))

    def test_build_outcome_dataset_reconstructs_point_in_time_features(self):
        from .models import CodeSubmission, CodingPortal, UserCodingProfile, RecommendationLog
        from .management.commands.retrain_ai import build_outcome_dataset

        user = User.objects.create_user(
            username='flywheeluser', password='testpassword123', email='fly@test.com'
        )
        UserCodingProfile.objects.create(user=user, elo_rating=1400)
        portal = CodingPortal.objects.create(name="Flywheel Portal")
        topic = Topic.objects.create(name="FlywheelTopic", structure_type="hierarchical", portal=portal)
        question = Question.objects.create(
            topic=topic, title="Flywheel Q", content="c", base_difficulty=1200.0
        )

        # Window as of the recommendation: 6 fails then 14 passes.
        for _ in range(6):
            CodeSubmission.objects.create(
                user=user, question=question, language='python', code='x', status='wrong_answer'
            )
        for _ in range(14):
            CodeSubmission.objects.create(
                user=user, question=question, language='python', code='x', status='accepted'
            )

        log = RecommendationLog.objects.create(
            user=user, recommended_topic=topic, engine_used='hierarchical',
            actual_result_correct=True,
        )

        # Post-recommendation noise: MUST NOT leak into the features —
        # that leakage was the v1 builder's train/serve mismatch.
        for _ in range(5):
            CodeSubmission.objects.create(
                user=user, question=question, language='python', code='x', status='wrong_answer'
            )

        X, y = build_outcome_dataset([log])

        self.assertEqual(y, [1])
        avg_acc, runs_z, elo, engine_flag = X[0]
        self.assertAlmostEqual(avg_acc, 0.70)          # 14/20 at log time
        self.assertAlmostEqual(runs_z, -4.09, places=2)  # streaky block pattern
        self.assertAlmostEqual(elo, 0.7)               # 1400 / 2000
        self.assertEqual(engine_flag, 1.0)

    def test_build_outcome_dataset_mirrors_cold_start_defaults(self):
        from .models import CodingPortal, UserCodingProfile, RecommendationLog
        from .management.commands.retrain_ai import (
            COLD_START_ACC, COLD_START_RUNS_Z, build_outcome_dataset,
        )

        user = User.objects.create_user(
            username='coldstartuser', password='testpassword123', email='cold@test.com'
        )
        UserCodingProfile.objects.create(user=user, elo_rating=1200)
        portal = CodingPortal.objects.create(name="Cold Portal")
        topic = Topic.objects.create(name="ColdTopic", structure_type="flat", portal=portal)
        log = RecommendationLog.objects.create(
            user=user, recommended_topic=topic, engine_used='flat',
            actual_result_correct=False,
        )

        X, y = build_outcome_dataset([log])
        # A log with no prior submissions must carry the exact serve-time
        # cold-start telemetry, keeping train/serve parity everywhere.
        self.assertEqual(X[0][:2], [COLD_START_ACC, COLD_START_RUNS_Z])
        self.assertEqual(y, [0])

    def test_evaluate_holdout_returns_metrics(self):
        import numpy as np
        from .management.commands.retrain_ai import evaluate_holdout

        rng = np.random.RandomState(1)
        X, y = [], []
        for _ in range(100):
            acc = rng.uniform(0, 1)
            X.append([acc, rng.uniform(-3, 3), rng.uniform(0.4, 0.9), rng.randint(2)])
            y.append(1 if acc > 0.5 else 0)

        metrics = evaluate_holdout(X, y)
        self.assertIsNotNone(metrics)
        self.assertGreaterEqual(metrics['auc'], 0.5)
        self.assertLessEqual(metrics['auc'], 1.0)
        self.assertGreaterEqual(metrics['brier'], 0.0)
        self.assertEqual(metrics['n_train'] + metrics['n_test'], 100)

    def test_evaluation_gate_refuses_unmeasurable_data(self):
        from .management.commands.retrain_ai import evaluate_holdout
        # Too few rows for a stratified holdout.
        self.assertIsNone(evaluate_holdout([[0.5, 0.0, 0.6, 0.0]] * 6, [1, 0, 1, 0, 1, 0]))
        # Single-class labels: AUC is undefined; nothing may ship.
        self.assertIsNone(evaluate_holdout([[0.5, 0.0, 0.6, 0.0]] * 20, [1] * 20))


class RoutingArtifactRegistryTests(TestCase):
    """
    §5 model registry: RoutingClassifier loads ONLY the versioned v2
    artifact. The unversioned v1 pkl was trained on the degenerate
    variance feature — if it ever loaded against v2 inputs it would score
    runs_z values on a variance scale, silently wrong for every user.
    """

    def setUp(self):
        self._reset_loader_cache()

    def tearDown(self):
        self._reset_loader_cache()

    @staticmethod
    def _reset_loader_cache():
        import groups.hybrid_router as hr
        hr._classifier_model = None
        hr._classifier_loaded = False

    def test_loader_targets_only_the_versioned_artifact(self):
        self.assertTrue(
            RoutingClassifier.artifact_path().endswith('routing_classifier_v2.pkl')
        )

    def test_legacy_v1_pkl_on_disk_is_ignored(self):
        import os
        import tempfile
        import joblib
        from unittest.mock import patch
        from sklearn.ensemble import RandomForestClassifier

        with tempfile.TemporaryDirectory() as tmp:
            # A stale v1 artifact sits in the registry directory…
            legacy = RandomForestClassifier(n_estimators=1, random_state=0)
            legacy.fit([[0.5, 0.1, 0.6, 0.0], [0.8, 0.2, 0.7, 1.0]], [0, 1])
            joblib.dump(legacy, os.path.join(tmp, 'routing_classifier.pkl'))

            # …but the loader only ever asks for the v2 name.
            v2_path = os.path.join(tmp, 'routing_classifier_v2.pkl')
            with patch.object(RoutingClassifier, 'artifact_path', return_value=v2_path):
                router = RoutingClassifier()

            self.assertIsNone(router.clf)  # heuristic fallback, not v1
            self.assertEqual(router.predict_route(0.8, 0.0, 0.7), 'hierarchical')

    def test_v2_artifact_loads_from_the_registry(self):
        import os
        import tempfile
        import joblib
        from unittest.mock import patch
        from sklearn.ensemble import RandomForestClassifier

        with tempfile.TemporaryDirectory() as tmp:
            clf = RandomForestClassifier(n_estimators=5, random_state=0)
            clf.fit(
                [[0.9, 0.0, 0.7, 1.0], [0.9, 0.0, 0.7, 0.0],
                 [0.2, 0.0, 0.5, 1.0], [0.2, 0.0, 0.5, 0.0]],
                [1, 0, 0, 1],
            )
            v2_path = os.path.join(tmp, 'routing_classifier_v2.pkl')
            joblib.dump(clf, v2_path)

            with patch.object(RoutingClassifier, 'artifact_path', return_value=v2_path):
                router = RoutingClassifier()

            self.assertIsNotNone(router.clf)
            self.assertEqual(router.predict_route(0.9, 0.0, 0.7), 'hierarchical')
            self.assertEqual(router.predict_route(0.2, 0.0, 0.5), 'flat')


class DagCacheInvalidationTests(TestCase):
    """
    Curriculum edits must invalidate the cached NetworkX DAG immediately.
    Before the signal wiring, a changed TopicPrerequisite served a stale
    graph for up to 30 minutes (HierarchicalEngine.CACHE_TIMEOUT).
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_prerequisite_change_invalidates_cached_graph(self):
        from .models import CodingPortal, TopicPrerequisite
        from .hybrid_router import HierarchicalEngine

        portal = CodingPortal.objects.create(name="Cache Portal")
        a = Topic.objects.create(name="CacheTopicA", structure_type="hierarchical", portal=portal)
        b = Topic.objects.create(name="CacheTopicB", structure_type="hierarchical", portal=portal)

        graph = HierarchicalEngine._get_graph("Cache Portal")
        self.assertEqual(graph.number_of_edges(), 0)

        edge = TopicPrerequisite.objects.create(topic=b, prerequisite=a)

        graph = HierarchicalEngine._get_graph("Cache Portal")
        self.assertTrue(graph.has_edge("CacheTopicA", "CacheTopicB"))

        edge.delete()

        graph = HierarchicalEngine._get_graph("Cache Portal")
        self.assertEqual(graph.number_of_edges(), 0)

    def test_queryset_delete_also_invalidates(self):
        from .models import CodingPortal, TopicPrerequisite
        from .hybrid_router import HierarchicalEngine

        portal = CodingPortal.objects.create(name="Cache Portal 2")
        a = Topic.objects.create(name="CacheTopicC", structure_type="hierarchical", portal=portal)
        b = Topic.objects.create(name="CacheTopicD", structure_type="hierarchical", portal=portal)
        TopicPrerequisite.objects.create(topic=b, prerequisite=a)

        graph = HierarchicalEngine._get_graph("Cache Portal 2")
        self.assertEqual(graph.number_of_edges(), 1)

        # seed_dsa_dag uses queryset deletes — post_delete must still fire
        TopicPrerequisite.objects.filter(topic=b).delete()

        graph = HierarchicalEngine._get_graph("Cache Portal 2")
        self.assertEqual(graph.number_of_edges(), 0)


class HybridRouterEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='testuser', password='testpassword123', email='test@test.com')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('hybrid-router')

    def test_hybrid_router_view_validation_missing_subject(self):
        """
        The endpoint should return 400 Bad Request if the required 'subject' field is missing.
        """
        payload = {
            "mastered_topics": ["Arrays"],
            "elo_rating": 1200.0
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('subject', response.data)
        
    def test_hybrid_router_view_valid_payload(self):
        """
        The endpoint should return 200 OK for a valid payload.
        """
        payload = {
            "subject": "Data Structures",
            "mastered_topics": ["Arrays"],
            "elo_rating": 1200.0
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CodeSubmitEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='testcoder', password='testpassword123', email='coder@test.com')
        self.client.force_authenticate(user=self.user)
        self.url = reverse('code-submit')
        
        # Setup mock DB Question
        from .models import CodingPortal
        portal = CodingPortal.objects.create(name="Test Portal")
        topic = Topic.objects.create(name="Arrays", structure_type="flat", portal=portal)
        self.question = Question.objects.create(
            topic=topic,
            title="Two Sum",
            content="Solve it.",
            base_difficulty=1200.0
        )

    def test_code_submit_view_invalid_language(self):
        payload = {
            "problem_id": self.question.id,
            "code": "print('hello')",
            "language": "ruby" # Not allowed
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('language', response.data)

    def test_code_submit_view_invalid_problem(self):
        payload = {
            "problem_id": 99999, # Does not exist
            "code": "print('hello')",
            "language": "python"
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('problem_id', response.data)
