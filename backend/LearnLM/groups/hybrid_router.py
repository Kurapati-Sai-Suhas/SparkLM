"""
groups/hybrid_router.py

Fixes applied:
- FIX-03 (CRITICAL / GAP-01): Routing logic was inverted vs the SRS.
  Struggling users now correctly route to 'flat' (Elo
  confidence-rebuilding) instead of 'hierarchical'.
- M5 (frozen architecture §6.2): the variance statistic — degenerate for
  binary outcomes (p(1-p), determined by the mean) — is replaced by the
  Wald–Wolfowitz runs-test z. Math lives in learning.router; only the
  versioned v2 classifier artifact is ever loaded.
- FIX-04 (HIGH / BUG-03): HierarchicalEngine._get_graph() is now cached
  via Django's cache framework instead of rebuilding the NetworkX DAG
  from the DB on every single request.
- FIX-07 (HIGH / BUG-06) support: GDCPEngine is unchanged (it already
  worked correctly) — the fix for BUG-06 is that coding_views.py now
  actually calls GDCPEngine.propagate_decay() on submission failure.
  See coding_views.py FIX-07 section.
"""

import hashlib
import json
import logging
import math
import networkx as nx
import joblib

from django.core.cache import cache
from django.utils import timezone
import os

logger = logging.getLogger(__name__)

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

# Identifies the routing policy that produced a recommendation (M4 Phase B),
# recorded on every RecommendationLog row.
#
# ⚠ BUMP THIS whenever routing behaviour changes — the features fed to
# RoutingClassifier, the mastery rule, the candidate filter, or the Elo band
# selection. It is the only way to tell later which policy produced a given
# historical recommendation, and unlike counts and outcomes it CANNOT be
# reconstructed after the fact: it depends on which code was running at
# decision time and nothing else records that.
#
# Format: v<major>-<short-description>. Free text, compared for equality
# only; nothing parses it.
#
# THE RULE (M2 P2.8a): bump when the DISTRIBUTION OF RECOMMENDATIONS changes
# for a fixed learner state. That includes bugfixes — a later analyst cannot
# see intent, only that the same state started producing different answers.
# It excludes frontend-only changes, logging, and refactors with provably
# identical output.
#
# v2-exposure-aware (M2 P2.8a): candidate selection became exposure-aware and
# totally ordered — solved exclusion moved into SQL, failed questions are
# demoted for a cooldown window, and ties break on attempt count, last attempt
# and finally question id. The global cross-topic fallback was removed.
ROUTING_POLICY_VERSION = "v2-exposure-aware"


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
    FR-RTR-01 (v2, frozen architecture §6.2): mean accuracy and
    Wald–Wolfowitz runs-test streakiness z over the user's last `window`
    submissions. Correctness is binary per submission (accepted or not) —
    per-test-case pass fractions aren't persisted on CodeSubmission, so
    binary outcomes are the available signal. The math lives in
    learning.router; this function owns only the DB fetch.

    The window is fetched OLDEST-FIRST once sliced, because the runs test
    reads sequence order (variance didn't care; the z-statistic does).

    Returns (avg_acc, runs_z, sample_size). Cold start (no submissions)
    returns pattern-neutral defaults so a brand-new user routes to the
    hierarchical DAG and starts at a foundation topic, rather than being
    treated as "struggling" and dropped into flat practice.
    """
    from groups.models import CodeSubmission
    from learning.router import outcome_stats

    # adaptive_eligible only (M2 P2.7c): routing telemetry IS the learner
    # model's view of recent performance, and a verdict from a question whose
    # answer key no oracle has checked may be our defect rather than the
    # learner's. Frozen at submission time, so verifying a question later does
    # not retroactively rewrite this window.
    statuses = list(
        CodeSubmission.objects.filter(user=user, adaptive_eligible=True)
        .order_by('-submitted_at')
        .values_list('status', flat=True)[:window]
    )
    if not statuses:
        return 0.7, 0.0, 0

    # Slice is newest-first; restore chronological order for the runs test.
    outcomes = [1.0 if s == 'accepted' else 0.0 for s in reversed(statuses)]
    return outcome_stats(outcomes)


# ─────────────────────────────────────────────────────────────
# META CLASSIFIER (TRAFFIC COP)
# ─────────────────────────────────────────────────────────────
_classifier_model = None
_classifier_loaded = False


class RoutingClassifier:
    """
    v2 Traffic Cop (§5 model registry + §6.2 statistic). Loads ONLY the
    versioned artifact matching the FEATURES_V2 contract
    [avg_acc, runs_z, avg_elo, engine_flag]. The unversioned v1 pkl
    (routing_classifier.pkl) was trained on the degenerate variance
    feature and must never load — a v1 model scoring v2 inputs would be
    silently wrong, which is the exact failure the registry rule exists
    to prevent. Absent an artifact, the learning.router heuristic routes.
    """

    ARTIFACT_NAME = "routing_classifier_v2.pkl"

    @classmethod
    def artifact_path(cls):
        # Anchored to BASE_DIR so loader and retrain_ai agree on one
        # registry location. (The v1 loader resolved a directory two
        # levels up that never existed, so the v1 pkl never actually
        # loaded — versioned names + a shared anchor close that hole.)
        from django.conf import settings
        return os.path.join(settings.BASE_DIR, "models_data", cls.ARTIFACT_NAME)

    def __init__(self):
        global _classifier_model, _classifier_loaded
        if not _classifier_loaded:
            try:
                _classifier_model = joblib.load(self.artifact_path())
            except Exception:
                _classifier_model = None
            _classifier_loaded = True
        self.clf = _classifier_model

    def predict_route(self, avg_acc, runs_z, avg_elo):
        from learning.router import decide_route, feature_rows

        # Outcome model: trained on FEATURES_V2 -> success. Score both
        # engines and route to the higher predicted success. Anything not
        # matching the v2 contract is refused (never scored wrongly).
        if (
            self.clf is not None
            and getattr(self.clf, 'n_features_in_', 0) == 4
            and hasattr(self.clf, 'predict_proba')
        ):
            flat_row, hier_row = feature_rows(avg_acc, runs_z, avg_elo)
            flat_p = self.clf.predict_proba([flat_row])[0][1]
            hier_p = self.clf.predict_proba([hier_row])[0][1]
            return "hierarchical" if hier_p >= flat_p else "flat"

        return decide_route(avg_acc, runs_z)


def log_routing_decision(user, route, avg_acc, runs_z, sample_size,
                         *, router=None, elo=None, latency_ms=None):
    """
    One structured line per routing decision (M2 P2.24).

    The predecessor was a formatted sentence. It was readable and useless:
    answering "how often does the router choose flat?" or "how many learners
    are stuck at cold start?" meant regex over prose, and it recorded neither
    which branch decided nor how long it took. This emits key/value fields so
    those become a query rather than an archaeology exercise.

    ── Reuses, never recomputes ────────────────────────────────────────────

    Every value is passed in by the caller, already computed for the decision
    itself. Recomputing telemetry for a log would be a second read of the
    submission window and — worse — a second answer, since the two reads
    could straddle a write.

    ── Cannot affect serving ───────────────────────────────────────────────

    Wrapped whole. An observability path must never change or break a
    learner's recommendation, so every failure here is swallowed after being
    noted. The routing decision has already been made by the time this is
    called; nothing it does can alter it.

    Carries no source code, no hidden tests, no expected outputs and no
    grading truth. `learner_id` follows the existing convention — the
    primary key, which is what the previous line logged too. The timestamp
    comes from the logging configuration rather than the payload, matching
    the agent's decision line.
    """
    try:
        clf = getattr(router, "clf", None) if router is not None else None
        payload = {
            "event": "routing_decision",
            "learner_id": getattr(user, "pk", None),
            "route": route,
            "avg_acc": round(float(avg_acc), 4),
            "runs_z": round(float(runs_z), 4),
            "n": int(sample_size),
            "cold_start": int(sample_size) == 0,
            # Which branch produced `route`. Reported rather than inferred:
            # a reader must never have to guess whether a model was involved.
            "decided_by": "model" if clf is not None else "heuristic",
            "model_artifact": (RoutingClassifier.ARTIFACT_NAME
                               if clf is not None else None),
            # The same identifier recorded on RecommendationLog.policy_version,
            # so a log line and a stored recommendation can be joined.
            "policy_version": ROUTING_POLICY_VERSION,
            "elo": round(float(elo), 2) if elo is not None else None,
            "latency_ms": (round(float(latency_ms), 2)
                           if latency_ms is not None else None),
        }
        logger.info("routing decision %s", json.dumps(payload, default=str))
    except Exception:                                         # noqa: BLE001
        # Never re-raise: the learner's recommendation is already decided.
        # Deliberately NOT prefixed "routing decision": a consumer greps that
        # prefix and parses the remainder as JSON, so sharing it would feed
        # the failure notice into the parser as a malformed event.
        logger.warning("routing_decision_log_failed", exc_info=False)


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
    OS_GRAPH / CN_GRAPH constants. Unlike HierarchicalEngine._get_graph,
    this does NOT fall back to "all topics" when a subject has no matching
    portal — an unseeded subject yields an empty graph so a consumer skips
    it instead of training on the wrong curriculum.

    ⚠ Currently has NO callers. Its three consumers — retrain_ai's GCN
    section, export_onnx and synthetic_data_generator — were removed in
    M1/P1.1 as part of retiring the un-runnable deep-learning stack. Kept
    rather than deleted because it is torch-free NetworkX code in the
    production routing module, and removing it is a change to this
    module's surface that P1.1 does not need to make. Delete it, or give
    it a consumer, when M6 decides the routing-engine question.
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

    # FR-RTR-01 v2: mean + runs-test streakiness over recent submissions.
    user = user_data.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        avg_acc, runs_z, _ = compute_routing_telemetry(user)
    else:
        avg_acc, runs_z = 0.7, 0.0  # no user context — pattern-neutral cold start

    elo_rating = float(user_data.get("elo_rating", 1200.0))

    route_decision = router.predict_route(avg_acc, runs_z, elo_rating / 2000.0)

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
