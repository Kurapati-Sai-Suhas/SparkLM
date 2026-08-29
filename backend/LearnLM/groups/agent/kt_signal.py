"""
The TA-GTKT read interface (M2 P2.14 §24E).

READ-ONLY, and deliberately not a model.

── Why this does not load the checkpoint ───────────────────────────────────

The obvious implementation — import `kt_research`, load the `.pt`, predict —
would be wrong twice over:

1. **It puts torch back in the web tier.** M1 P1.1 removed the deep-learning
   dependency tier precisely because it cannot execute there. Re-importing it
   through the agent would undo that phase and add ~2 GB to every deploy for
   code the request path cannot run.
2. **It couples a research artifact to production.** `kt_research` is kept
   structurally unable to import the application; a production module
   importing it in the other direction makes the checkpoint a deployment
   dependency, so a research experiment could break a learner's request.

So the boundary is a FILE, the same shape of boundary `kt_dataset` and
`kt_research` already use between them. The research side exports predictions
and model metadata offline; production reads the export and never computes
anything. Absent export means absent signal, reported as such.

── What this signal is honestly worth ──────────────────────────────────────

**The model has never seen a SparkLM learner.** It was trained on ASSISTments
2009: different learners, different items, and a concept vocabulary of
ASSISTments skill ids that has no mapping to SparkLM topics. Every reading
therefore carries `applicability`, and it says so.

That is not a disclaimer bolted on — it is the finding. A number from this
interface demonstrates that the seam works, and nothing about the learner it
is attached to. `predicted_mastery` for a SparkLM learner will be
`unavailable` until a model is trained on SparkLM interactions, which needs a
question bank that can produce them.
"""

import json
import logging
import pathlib

from django.conf import settings

logger = logging.getLogger(__name__)

AVAILABLE = "available"
UNAVAILABLE = "unavailable"

#: Why a reading, when present, must not be read as a claim about this learner.
NOT_TRAINED_ON_SPARKLM = (
    "This model was trained on the ASSISTments 2009 public benchmark. It has "
    "never seen a SparkLM learner or a SparkLM question, and its concept "
    "vocabulary does not map to SparkLM topics. Treat any value as a "
    "demonstration that the interface works, not as a measurement of this "
    "learner."
)


def export_path():
    """Configured location of the offline export, or None."""
    configured = getattr(settings, "KT_PREDICTION_EXPORT", "") or ""
    return pathlib.Path(configured) if configured else None


def _unavailable(reason):
    return {
        "status": UNAVAILABLE,
        "predicted_mastery": None,
        "predicted_next_correct": None,
        "reason": reason,
        "applicability": NOT_TRAINED_ON_SPARKLM,
    }


def load_export():
    """
    The offline export, or None. Never raises.

    A malformed or missing export must degrade to "no signal", not to a
    failed request — this is an advisory input to a recommendation, and the
    recommendation has to survive without it.
    """
    path = export_path()
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        logger.warning("KT prediction export at %s is unreadable", path,
                       exc_info=True)
        return None
    if not isinstance(payload, dict) or "model" not in payload:
        logger.warning("KT prediction export at %s has no model metadata",
                       path)
        return None
    return payload


def predict(user, topic_name=None):
    """
    `predicted_mastery` and `predicted_next_correct` for one learner, or an
    explicit unavailable.

    Reads a file. Computes nothing, loads no weights, writes nothing, and
    touches no learner state.
    """
    export = load_export()
    if export is None:
        return _unavailable(
            "No KT prediction export is configured or readable. Set "
            "KT_PREDICTION_EXPORT to a file produced by the research side.")

    key = str(getattr(user, "pk", "") or "")
    entry = (export.get("learners") or {}).get(key)
    if entry is None:
        return _unavailable(
            f"The export holds no prediction for this learner. It was built "
            f"from {export.get('trained_on', 'an unnamed corpus')}, which "
            f"does not contain SparkLM learners.")

    if topic_name:
        entry = (entry.get("topics") or {}).get(str(topic_name), entry)

    return {
        "status": AVAILABLE,
        "predicted_mastery": entry.get("predicted_mastery"),
        "predicted_next_correct": entry.get("predicted_next_correct"),
        "model": export.get("model"),
        "model_version": export.get("model_version"),
        "trained_on": export.get("trained_on"),
        "dataset_fingerprint": export.get("dataset_fingerprint"),
        "exported_at": export.get("exported_at"),
        "applicability": NOT_TRAINED_ON_SPARKLM,
    }
