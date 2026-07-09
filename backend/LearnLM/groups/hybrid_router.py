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
import networkx as nx
import joblib
import numpy as np

from django.core.cache import cache
from django.utils import timezone
import os

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
            features = [avg_acc, var_acc, avg_elo]
            if self.clf.n_features_in_ > 3:
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


def invalidate_dag_cache(subject: str = None):
    """
    FIX-04 helper: call this from TopicPrerequisite.save()/delete() to bust
    the cache when curriculum edges change. If your cache backend is
    django-redis, cache.delete_pattern('dag_graph_*') works directly.
    If you're on Django's default LocMemCache, delete_pattern is NOT
    available and will raise AttributeError — in that case, either:
      (a) switch CACHES to django_redis.cache.RedisCache, or
      (b) track known subject keys explicitly and delete them one by one.
    """
    try:
        cache.delete_pattern('dag_graph_*')
    except AttributeError:
        # Fallback for non-Redis cache backends: caller should pass the
        # specific subject so we can at least clear that one key.
        if subject:
            cache_key = f'dag_graph_{hashlib.md5(subject.lower().strip().encode()).hexdigest()}'
            cache.delete(cache_key)
