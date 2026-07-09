from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model
from .hybrid_router import RoutingClassifier
from .models import Question, Topic

User = get_user_model()

class HybridRouterHeuristicTests(TestCase):
    """
    SRS FR-RTR-02 (as corrected by FIX-03): struggling users — high accuracy
    variance (> 0.20) OR low accuracy (< 0.60) — route to the FLAT Elo engine
    to rebuild confidence with skill-matched problems. Consistent, performing
    users route to the HIERARCHICAL DAG to advance the curriculum.
    (The previous version of these tests asserted the inverted pre-fix
    behavior.)
    """

    def test_routing_heuristic_prefers_flat_for_high_variance(self):
        router = RoutingClassifier()
        # Force classifier to None to test the fallback heuristic
        router.clf = None

        avg_acc = 0.8
        var_acc = 0.25  # High variance (> 0.20) — erratic guessing
        elo = 1400

        route = router.predict_route(avg_acc, var_acc, elo)
        self.assertEqual(route, 'flat')

    def test_routing_heuristic_prefers_flat_for_low_accuracy(self):
        router = RoutingClassifier()
        router.clf = None

        avg_acc = 0.4   # Low accuracy (< 0.60) — struggling
        var_acc = 0.05
        elo = 1400

        route = router.predict_route(avg_acc, var_acc, elo)
        self.assertEqual(route, 'flat')

    def test_routing_heuristic_prefers_hierarchical_for_consistency(self):
        router = RoutingClassifier()
        router.clf = None

        avg_acc = 0.8
        var_acc = 0.05  # Low variance, high accuracy — ready to advance
        elo = 1400

        route = router.predict_route(avg_acc, var_acc, elo)
        self.assertEqual(route, 'hierarchical')


class RoutingTelemetryTests(TestCase):
    """
    SRS FR-RTR-01: the Traffic Cop must route on the mean and variance of
    correctness over the user's last 20 submissions — not placeholders.
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

    def test_cold_start_routes_hierarchical(self):
        from .hybrid_router import compute_routing_telemetry
        avg_acc, var_acc, n = compute_routing_telemetry(self.user)
        self.assertEqual(n, 0)

        router = RoutingClassifier()
        router.clf = None
        self.assertEqual(router.predict_route(avg_acc, var_acc, 0.6), 'hierarchical')

    def test_erratic_history_routes_flat(self):
        # Alternating pass/fail — maximal variance for binary outcomes (0.25 > 0.20)
        for i in range(10):
            self._submit('accepted' if i % 2 == 0 else 'wrong_answer')

        from .hybrid_router import compute_routing_telemetry
        avg_acc, var_acc, n = compute_routing_telemetry(self.user)
        self.assertEqual(n, 10)
        self.assertAlmostEqual(var_acc, 0.25, places=2)

        router = RoutingClassifier()
        router.clf = None
        self.assertEqual(router.predict_route(avg_acc, var_acc, 0.6), 'flat')

    def test_consistent_history_routes_hierarchical(self):
        for _ in range(10):
            self._submit('accepted')

        from .hybrid_router import compute_routing_telemetry
        avg_acc, var_acc, n = compute_routing_telemetry(self.user)
        self.assertEqual(n, 10)
        self.assertEqual(avg_acc, 1.0)
        self.assertEqual(var_acc, 0.0)

        router = RoutingClassifier()
        router.clf = None
        self.assertEqual(router.predict_route(avg_acc, var_acc, 0.6), 'hierarchical')

    def test_telemetry_only_uses_last_20_submissions(self):
        # 5 old failures followed by 20 clean accepts: the failures fall
        # outside the FR-RTR-01 window and must not affect routing.
        for _ in range(5):
            self._submit('wrong_answer')
        for _ in range(20):
            self._submit('accepted')

        from .hybrid_router import compute_routing_telemetry
        avg_acc, var_acc, n = compute_routing_telemetry(self.user)
        self.assertEqual(n, 20)
        self.assertEqual(avg_acc, 1.0)


class MasteryDefinitionTests(TestCase):
    """SRS FR-HRCH-01: mastery = accuracy >= 0.8, one shared definition."""

    def test_mastered_topics_use_accuracy_threshold(self):
        from .models import CodingPortal, UserTopicMastery
        from .hybrid_router import get_mastered_topic_names

        user = User.objects.create_user(
            username='masteryuser', password='testpassword123', email='m@test.com'
        )
        portal = CodingPortal.objects.create(name="Mastery Portal")
        t_done = Topic.objects.create(name="MasteredTopic", structure_type="hierarchical", portal=portal)
        t_wip  = Topic.objects.create(name="LearningTopic", structure_type="hierarchical", portal=portal)
        t_edge = Topic.objects.create(name="EdgeTopic", structure_type="hierarchical", portal=portal)

        UserTopicMastery.objects.create(user=user, topic=t_done, accuracy=0.85)
        UserTopicMastery.objects.create(user=user, topic=t_wip, accuracy=0.50)
        UserTopicMastery.objects.create(user=user, topic=t_edge, accuracy=0.80)  # boundary: counts

        mastered = set(get_mastered_topic_names(user))
        self.assertEqual(mastered, {"MasteredTopic", "EdgeTopic"})


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
