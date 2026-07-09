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
