"""
Module D.2: Explainable AI (XAI) Engine.

Production-readiness rewrite:
- The old view-side wiring built a FRESH, UNTRAINED TrueGCNKnowledgeGraph
  per request and handed it straight to shap.DeepExplainer. That crashed
  immediately (the GCN forward takes (x, edge_index) but SHAP calls
  model(x)), and even without the crash the attributions would have been
  explanations of random weights.
- get_xai_engine() now builds the engine ONCE per process, thread-safely
  (Daphne runs sync views in a thread pool), over the TRAINED weights in
  models_data/gcn_dsa.pth, with the two-arg forward adapted for SHAP.
- SHAP value computation is serialized with a lock (explainers are not
  guaranteed thread-safe) and any failure marks the engine unavailable so
  callers fall back to the heuristic instead of 500ing the request.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_engine_lock = threading.Lock()
_cached_engine = None
_engine_failed = False

FEATURE_NAMES = ['Time Complexity', 'Space Complexity', 'Logic Accuracy', 'Topic Recency']


def get_xai_engine():
    """
    Returns a process-wide cached XAIEngine over the trained DSA GCN, or
    None when SHAP explanations are unavailable (missing libs, missing
    weights, unsupported ops). Never raises.
    """
    global _cached_engine, _engine_failed

    if _cached_engine is not None or _engine_failed:
        return _cached_engine

    with _engine_lock:
        if _cached_engine is not None or _engine_failed:
            return _cached_engine
        try:
            import torch
            from groups.engines.gnn_engine import load_gcn

            model = load_gcn("dsa")  # trained weights if models_data/gcn_dsa.pth exists
            wrapped = _EdgeFixedModel(model)
            # Spread background across the feature range instead of zeros:
            # the trained GCN has dead-ReLU regions near the origin where
            # gradients (and thus SHAP attributions) are exactly zero, and
            # expected-gradient paths from a zero background never leave
            # that region. A spread background crosses live regions.
            background = torch.linspace(0.1, 0.9, steps=10).unsqueeze(1).repeat(1, 4)
            _cached_engine = XAIEngine(wrapped, background)
            logger.info("SHAP XAI engine initialized over trained DSA GCN.")
        except Exception:
            logger.exception("SHAP XAI engine unavailable — callers will use the heuristic payload.")
            _engine_failed = True
            _cached_engine = None
    return _cached_engine


def _EdgeFixedModel(gcn_model):
    """
    Adapts the GCN's forward(x, edge_index) to the forward(x) signature
    SHAP expects. Uses an empty edge set: each row of x is an isolated
    node, which is exactly right here — the input is a single user's
    feature vector, not a graph batch. (GCNConv adds self-loops, so the
    layers still apply their learned transforms.)
    """
    import torch
    import torch.nn as nn

    class EdgeFixed(nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner
            self.register_buffer("edge_index", torch.empty((2, 0), dtype=torch.long))

        def forward(self, x):
            return self.inner(x, self.edge_index)

    wrapped = EdgeFixed(gcn_model)
    wrapped.eval()
    return wrapped


class XAIEngine:
    """
    Wraps a 1-arg PyTorch model with a SHAP explainer and formats
    attributions for the React radar chart.
    """

    def __init__(self, pytorch_model, background_tensor):
        import numpy as np
        import shap
        import torch

        self.model = pytorch_model
        self.model.eval()
        self._lock = threading.Lock()

        # DeepExplainer is fastest but doesn't support every op — and with
        # torch-geometric's message-passing layers it can fail SILENTLY,
        # returning all-zero attributions instead of raising. Probe with a
        # nonzero input and fall back to GradientExplainer (expected
        # gradients — works for any differentiable model) on zeros.
        probe = torch.ones((1, background_tensor.shape[1]))
        self.explainer = None
        try:
            candidate = shap.DeepExplainer(self.model, background_tensor)
            vals = candidate.shap_values(probe)
            arr = np.asarray(vals[0] if isinstance(vals, list) else vals)
            if float(np.abs(arr).sum()) > 0:
                self.explainer = candidate
            else:
                logger.info("DeepExplainer returned degenerate zero attributions; using GradientExplainer.")
        except Exception:
            logger.info("DeepExplainer unsupported for this model; using GradientExplainer.")

        if self.explainer is None:
            self.explainer = shap.GradientExplainer(self.model, background_tensor)
            # Surface unsupported-op failures at build time, not on a live request.
            self.explainer.shap_values(probe)

    def predict_success(self, user_feature_tensor) -> float:
        """Model's predicted success probability (0..1) for this user state."""
        import torch
        with torch.no_grad():
            out = self.model(user_feature_tensor.unsqueeze(0))
        return float(out.squeeze())

    def generate_radar_data(self, user_feature_tensor, feature_names=FEATURE_NAMES):
        """
        SHAP attributions for one user, formatted for Recharts:
        {radar_data: [{subject, A, fullMark}], dominant_factor, insight_text}
        """
        import numpy as np

        with self._lock:
            shap_values = self.explainer.shap_values(user_feature_tensor.unsqueeze(0))

        if isinstance(shap_values, list):
            attributions = np.asarray(shap_values[0])[0]
        else:
            attributions = np.asarray(shap_values)[0]
        attributions = attributions.reshape(-1)[:len(feature_names)]

        abs_attributions = np.abs(attributions)
        total_impact = float(np.sum(abs_attributions))
        if total_impact == 0:
            # Degenerate attributions (input sits in a dead-ReLU region of
            # the model) — no explanation to give. Callers fall back to the
            # heuristic payload rather than showing a misleading
            # "performance is balanced" radar of zeros.
            return None
        percentages = (abs_attributions / total_impact) * 100

        radar_chart_data = []
        dominant_factor = {"name": "None", "impact": 0}
        for i, name in enumerate(feature_names):
            impact_score = round(float(percentages[i]), 1)
            radar_chart_data.append({"subject": name, "A": impact_score, "fullMark": 100})
            if impact_score > dominant_factor["impact"]:
                dominant_factor = {"name": name, "impact": impact_score}

        return {
            "radar_data": radar_chart_data,
            "dominant_factor": dominant_factor["name"],
            "insight_text": self._generate_text_insight(dominant_factor),
        }

    def _generate_text_insight(self, dominant_factor):
        """Human-readable text for the highest SHAP attribution."""
        df_name = dominant_factor["name"]

        if df_name == "Time Complexity":
            return "⏳ XAI Insight: Your logic is sound, but execution speed is your biggest bottleneck. Try replacing nested loops with a Hash Map."
        elif df_name == "Space Complexity":
            return "💾 XAI Insight: You passed, but memory usage is heavily dragging your Elo down. Are you creating unnecessary arrays?"
        elif df_name == "Topic Recency":
            return "🧠 XAI Insight: SHAP detects significant skill decay. You haven't practiced this data structure recently — review before advancing."
        elif df_name == "Logic Accuracy":
            return "⚠️ XAI Insight: Core algorithmic logic is the dominant factor. Review the foundational concepts before worrying about speed."
        return "📈 XAI Insight: Performance is balanced. Keep practicing to raise your baseline Elo."
