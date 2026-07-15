"""
groups/hybrid_router.py

Fixes applied:
- FIX-03 (CRITICAL / GAP-01): Routing logic was inverted vs the SRS.
  High variance / low accuracy (struggling) now correctly routes to
  'flat' (Elo confidence-rebuilding) instead of 'hierarchical'.
  Variance threshold corrected 0.15 -> 0.20 to match the SRS.
- FIX-04 (HIGH / BUG-03): HierarchicalEngine._get_graph() is now cached
  via Django's cache framework instead of rebuilding the NetworkX DAG
  from the DB on every single request.
- FIX-07 (HIGH / BUG-06) support: GDCPEngine is unchanged (it already
  worked correctly) — the fix for BUG-06 is that coding_views.py now
  actually calls GDCPEngine.propagate_decay() on submission failure.
  See coding_views.py FIX-07 section.
"""

import hashlib
import math
import networkx as nx
import joblib
import numpy as np

from django.core.cache import cache
from django.utils import timezone
import os

# ─────────────────────────────────────────────────────────────
# SHARED PEDAGOGICAL DEFINITIONS
# ─────────────────────────────────────────────────────────────

# SRS FR-HRCH-01: a topic counts as mastered at >= 80% accuracy over at
# least MASTERY_MIN_REVIEWS attempts. Defined once here so every consumer
# (NextProblemView, HybridRouterView, MasteryMapView) agrees. The review
# floor prevents a single lucky solve (accuracy 1.0 after 1 attempt) from
# unlocking downstream curriculum topics.
MASTERY_ACCURACY_THRESHOLD = 0.8
MASTERY_MIN_REVIEWS = 3


def get_mastered_topic_names(user) -> list:
    """Names of every topic the user has mastered per FR-HRCH-01."""
    from groups.models import UserTopicMastery
    return list(
        UserTopicMastery.objects.filter(
            user=user,
            accuracy__gte=MASTERY_ACCURACY_THRESHOLD,
            reviews__gte=MASTERY_MIN_REVIEWS,
        ).values_list('topic__name', flat=True)
    )


def compute_routing_telemetry(user, window: int = 20):
    """
    SRS FR-RTR-01: mean and variance of correctness over the user's last
    `window` submissions. Correctness is binary per submission (accepted
    or not) — per-test-case pass fractions aren't persisted on
    CodeSubmission, so binary outcomes are the available signal.

    Returns (avg_acc, var_acc, sample_size). Cold start (no submissions)
    returns consistent-looking defaults so a brand-new user routes to the
    hierarchical DAG and starts at a foundation topic, rather than being
    treated as "struggling" and dropped into flat practice.
    """
    from groups.models import CodeSubmission

    statuses = list(
        CodeSubmission.objects.filter(user=user)
        .order_by('-submitted_at')
        .values_list('status', flat=True)[:window]
    )
    if not statuses:
        return 0.7, 0.1, 0

    outcomes = [1.0 if s == 'accepted' else 0.0 for s in statuses]
    avg_acc = float(np.mean(outcomes))
    var_acc = float(np.var(outcomes))
    return avg_acc, var_acc, len(outcomes)


# ─────────────────────────────────────────────────────────────
# META CLASSIFIER (TRAFFIC COP)
# ─────────────────────────────────────────────────────────────
_classifier_model = None
_classifier_loaded = False


class RoutingClassifier:
    # FIX-03: threshold corrected from 0.15 to 0.20 to match the SRS
    VARIANCE_THRESHOLD = 0.20
    ACCURACY_THRESHOLD = 0.60

    def __init__(self):
        global _classifier_model, _classifier_loaded
        if not _classifier_loaded:
            try:
                _classifier_model = joblib.load(
                    os.path.join(os.path.dirname(__file__), "..", "..", "models_data", "routing_classifier.pkl")
                )
            except Exception:
                _classifier_model = None
            _classifier_loaded = True
        self.clf = _classifier_model

    def predict_route(self, avg_acc, var_acc, avg_elo, subject=None):
        if self.clf:
            n_features = getattr(self.clf, 'n_features_in_', 3)

            # Outcome model (Phase 3 retrain_ai): trained on
            # [avg_acc, var_acc, avg_elo, engine_flag] -> success.
            # Score both engines and route to the higher predicted success.
            if n_features == 4 and hasattr(self.clf, 'predict_proba'):
                flat_p = self.clf.predict_proba([[avg_acc, var_acc, avg_elo, 0.0]])[0][1]
                hier_p = self.clf.predict_proba([[avg_acc, var_acc, avg_elo, 1.0]])[0][1]
                return "hierarchical" if hier_p >= flat_p else "flat"

            # Legacy models predicted the engine label directly (3 plain
            # features, or 771 with a subject embedding appended).
            features = [avg_acc, var_acc, avg_elo]
            if n_features > 4:
                if subject:
                    from .ai_services import get_gemini_embedding
                    emb = get_gemini_embedding(subject)
                    features.extend(emb)
                else:
                    features.extend([0.0] * 768)

            route = self.clf.predict([features])[0]
            return "hierarchical" if route == 1 else "flat"

        # FIX-03: CORRECTED FALLBACK HEURISTIC
        # Struggling (high variance OR low accuracy) -> Flat Elo
        # (skill-matched confidence rebuilding, per SRS)
        # Consistent and performing -> Hierarchical DAG
        # (ready to advance to next prerequisite-gated topic)
        if var_acc > self.VARIANCE_THRESHOLD or avg_acc < self.ACCURACY_THRESHOLD:
            return "flat"           # was 'hierarchical' — inverted bug (GAP-01)
        return "hierarchical"       # was 'flat' — inverted bug (GAP-01)


# ─────────────────────────────────────────────────────────────
# 3-PARAMETER ITEM RESPONSE THEORY (IRT) ENGINE
# (restored — the router rewrite dropped it while
#  CodingOnboardingView still uses it for cold-start calibration)
# ─────────────────────────────────────────────────────────────
class IRTEngine:
    @staticmethod
    def expected_score(theta, a, b, c):
        """
        theta: user latent ability
        a: discrimination parameter (how well it separates students)
        b: difficulty parameter
        c: guessing parameter (probability of getting it right by chance)
        """
        # Prevent math overflow
        exponent = min(709.0, max(-709.0, -a * (theta - b)))
        return c + (1 - c) / (1 + math.exp(exponent))


class GDCPEngine:
    @staticmethod
    def propagate_decay(graph, start_node, base_decay=0.1):
        penalties = {}
        if start_node not in graph:
            return penalties
        for desc in nx.descendants(graph, start_node):
            distance = nx.shortest_path_length(graph, start_node, desc)
            penalty = base_decay * (0.5 ** (distance - 1))
            penalties[desc] = penalty
        return penalties


class HierarchicalEngine:
    # FIX-04: cache timeout for the NetworkX DAG
    CACHE_TIMEOUT = 60 * 30  # 30 minutes
    # Registry of every dag_graph_* key we've set, so invalidation works
    # on any cache backend (LocMemCache has no delete_pattern).
    CACHE_KEYS_REGISTRY = 'dag_graph_keys'

    @staticmethod
    def _get_graph(subject: str) -> nx.DiGraph:
        # FIX-04: Cache the graph. Rebuilding it on every single API
        # request (BUG-03) is an O(N) DB query hit under any concurrent
        # load. Cache hit is O(1).
        cache_key = f'dag_graph_{hashlib.md5(subject.lower().strip().encode()).hexdigest()}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        from groups.models import Topic, TopicPrerequisite
        from django.core.exceptions import ValidationError

        graph = nx.DiGraph()
        s = subject.lower().strip()
        topics = Topic.objects.filter(portal__name__icontains=s)

        if not topics.exists():
            topics = Topic.objects.all()
            if not topics.exists():
                raise ValidationError(f"Unknown subject: {subject}")

        for t in topics:
            graph.add_node(t.name)

        prereqs = TopicPrerequisite.objects.filter(topic__in=topics).select_related('topic', 'prerequisite')
        for p in prereqs:
            graph.add_edge(p.prerequisite.name, p.topic.name)

        cache.set(cache_key, graph, HierarchicalEngine.CACHE_TIMEOUT)

        # Track the key so invalidate_dag_cache can clear it later even on
        # backends without pattern deletes.
        known_keys = cache.get(HierarchicalEngine.CACHE_KEYS_REGISTRY) or set()
        if cache_key not in known_keys:
            known_keys.add(cache_key)
            cache.set(HierarchicalEngine.CACHE_KEYS_REGISTRY, known_keys, None)

        return graph

    @classmethod
    def get_next_topic(cls, subject: str, mastered_topics: list) -> dict:
        graph = cls._get_graph(subject)
        mastered = set(mastered_topics)
        candidates = []

        for node in graph.nodes:
            if node in mastered:
                continue
            prerequisites = set(graph.predecessors(node))
            if prerequisites.issubset(mastered):
                candidates.append(node)

        total_nodes = len(graph.nodes)

        if not candidates:
            roots = [n for n in graph.nodes if graph.in_degree(n) == 0]
            unmastered_roots = [r for r in roots if r not in mastered]
            if unmastered_roots:
                first = unmastered_roots[0]
                return {
                    "recommended_topic":    first,
                    "reason":               "Start here — this is the foundation topic.",
                    "prerequisites_needed": [],
                    "unlocks":              list(graph.successors(first)),
                    "mastery_percentage":   round(len(mastered) / total_nodes * 100, 1),
                }
            return {
                "recommended_topic":    None,
                "reason":               "You have mastered all topics! 🎉",
                "prerequisites_needed": [],
                "unlocks":              [],
                "mastery_percentage":   100.0,
            }

        best = max(candidates, key=lambda t: len(list(graph.successors(t))))
        return {
            "recommended_topic":    best,
            "reason":               f"All prerequisites satisfied. Mastering this unlocks {len(list(graph.successors(best)))} new topic(s).",
            "prerequisites_needed": list(graph.predecessors(best)),
            "unlocks":              list(graph.successors(best)),
            "mastery_percentage":   round(len(mastered) / total_nodes * 100, 1),
        }

    @classmethod
    def get_mastery_map(cls, subject: str, mastered_topics: list) -> dict:
        """
        Full prerequisite graph annotated with per-node mastery status.
        (Restored after the router rewrite dropped it — MasteryMapView
        depends on this.) Shape: {topic: {mastered, unlocked,
        prerequisites, unlocks}}.
        """
        graph    = cls._get_graph(subject)
        mastered = set(mastered_topics)

        # Topological depth (0 = root) so the frontend can lay out real
        # DAG columns instead of guessing.
        depth = {}
        for node in nx.topological_sort(graph):
            preds = list(graph.predecessors(node))
            depth[node] = 0 if not preds else max(depth[p] for p in preds) + 1

        result = {}
        for node in graph.nodes:
            prereqs  = list(graph.predecessors(node))
            unlocked = all(p in mastered for p in prereqs) if prereqs else True
            result[node] = {
                "mastered":      node in mastered,
                "unlocked":      unlocked,
                "prerequisites": prereqs,
                "unlocks":       list(graph.successors(node)),
                "depth":         depth.get(node, 0),
            }
        return result


def get_curriculum_graphs(subjects=("dsa", "os", "cn")) -> dict:
    """
    Lazy, DB-backed replacement for the old module-level DSA_GRAPH /
    OS_GRAPH / CN_GRAPH constants (used by retrain_ai, export_onnx and
    synthetic_data_generator). Unlike HierarchicalEngine._get_graph, this
    does NOT fall back to "all topics" when a subject has no matching
    portal — an unseeded subject yields an empty graph so ML pipelines
    skip it instead of training on the wrong curriculum.
    """
    from groups.models import Topic, TopicPrerequisite

    graphs = {}
    for subject in subjects:
        s = subject.lower().strip()
        graph = nx.DiGraph()
        topics = Topic.objects.filter(portal__name__icontains=s)
        for t in topics:
            graph.add_node(t.name)
        prereqs = TopicPrerequisite.objects.filter(topic__in=topics).select_related('topic', 'prerequisite')
        for p in prereqs:
            graph.add_edge(p.prerequisite.name, p.topic.name)
        graphs[subject] = graph
    return graphs


def _pick_next_difficulty(player_rating: float) -> dict:
    """Flat-Elo helper: suggest the next problem difficulty band."""
    target = player_rating + 50
    if target < 1000:   band = "beginner"
    elif target < 1200: band = "easy"
    elif target < 1400: band = "medium"
    elif target < 1600: band = "hard"
    else:               band = "expert"
    return {
        "target_difficulty": round(target),
        "difficulty_band":   band,
        "player_rating":     player_rating,
        "tip":               f"Next question should be rated ~{round(target)} ({band}).",
    }


def route_recommendation(subject: str, user_data: dict) -> dict:
    """
    Entry point for HybridRouterView (/api/ai/recommend/). Restored after
    the router rewrite dropped it. Routes between the hierarchical DAG
    engine and flat Elo practice using the ML Traffic Cop.
    """
    from django.core.exceptions import ValidationError
    from groups.engines.elo_engine import EloEngine

    router = RoutingClassifier()

    # FR-RTR-01: real aggregates over the user's recent submissions.
    user = user_data.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        avg_acc, var_acc, _ = compute_routing_telemetry(user)
    else:
        avg_acc, var_acc = 0.7, 0.1  # no user context — cold-start defaults

    elo_rating = float(user_data.get("elo_rating", 1200.0))

    route_decision = router.predict_route(
        avg_acc, var_acc, elo_rating / 2000.0, subject=subject
    )

    if route_decision == "hierarchical":
        mastered = user_data.get("mastered_topics") or []
        try:
            return {
                "engine_used":    "hierarchical_prerequisite_graph",
                "subject":        subject,
                "recommendation": HierarchicalEngine.get_next_topic(subject, mastered),
            }
        except ValidationError:
            # No topics seeded at all — fall through to flat Elo below.
            pass

    difficulty = user_data.get("question_difficulty")
    correct    = user_data.get("got_correct")

    if difficulty is not None and correct is not None:
        update = EloEngine.calculate_new_rating(elo_rating, float(difficulty), bool(correct))
        return {
            "engine_used":   "elo_rating",
            "subject":       subject,
            "rating_update": update,
            "next_question": _pick_next_difficulty(update["new_rating"]),
        }
    return {
        "engine_used":   "elo_rating",
        "subject":       subject,
        "next_question": _pick_next_difficulty(elo_rating),
    }


def invalidate_dag_cache(subject: str = None):
    """
    FIX-04 helper: bust every cached DAG when curriculum edges change.
    Wired to TopicPrerequisite post_save/post_delete signals in models.py.

    Different callers cache the graph under different subject strings
    ("DSA Masterclass", "DSA", "Array", ...), so a single-subject delete is
    never sufficient — we clear all dag_graph_* keys. django-redis supports
    delete_pattern directly; on other backends we fall back to the key
    registry maintained by HierarchicalEngine._get_graph.

    Note: with the default per-process LocMemCache this only clears the
    process that made the change; other workers stay stale until
    CACHE_TIMEOUT. Use a shared backend (Redis) in production.
    """
    try:
        cache.delete_pattern('dag_graph_*')
        return
    except AttributeError:
        pass

    known_keys = cache.get(HierarchicalEngine.CACHE_KEYS_REGISTRY) or set()
    if known_keys:
        cache.delete_many(list(known_keys))
        cache.delete(HierarchicalEngine.CACHE_KEYS_REGISTRY)

    if subject:
        cache_key = f'dag_graph_{hashlib.md5(subject.lower().strip().encode()).hexdigest()}'
        cache.delete(cache_key)
