import json
import os
from datetime import datetime, timezone as dt_timezone

import joblib
import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from groups.hybrid_router import RoutingClassifier
from groups.models import RecommendationLog, UserCodingProfile
from learning.router import FEATURES_V2, outcome_stats

# Serve-time cold-start telemetry (compute_routing_telemetry with no
# submissions). Train-time rows for logs that predate any submission must
# mirror it exactly — train/serve feature parity is the v2 contract.
COLD_START_ACC = 0.7
COLD_START_RUNS_Z = 0.0

OUTCOME_WINDOW = 20


def build_outcome_dataset(logs):
    """
    v2 feature builder. Rows: FEATURES_V2 = [avg_acc, runs_z, avg_elo,
    engine_flag], labeled with actual_result_correct.

    Two fixes carried by this builder:
    - Outcomes as labels (Phase 3): the pre-flywheel pipeline trained on
      engine_used — the router's own past output — a self-fulfilling loop
      with no ground truth.
    - Point-in-time features (M5): the v1 builder aggregated the user's
      CURRENT topic-mastery rows, which is not what the router sees at
      serve time. v2 reconstructs the same last-20 outcome window the
      router uses, as of the moment the recommendation was made
      (submitted_at < log.created_at) — train and serve now compute the
      identical statistics via learning.router.

    Elo remains the profile's current value scaled by 2000 — rating
    history isn't persisted, so this one feature stays an approximation
    (documented limitation, not a silent one).
    """
    from groups.models import CodeSubmission

    X, y = [], []
    for log in logs:
        profile = UserCodingProfile.objects.filter(user=log.user).first()
        if not profile:
            continue

        statuses = list(
            CodeSubmission.objects.filter(
                user=log.user, submitted_at__lt=log.created_at
            ).order_by('-submitted_at').values_list('status', flat=True)[:OUTCOME_WINDOW]
        )
        if statuses:
            outcomes = [1.0 if s == 'accepted' else 0.0 for s in reversed(statuses)]
            avg_acc, runs_z, _ = outcome_stats(outcomes)
        else:
            avg_acc, runs_z = COLD_START_ACC, COLD_START_RUNS_Z

        avg_elo = profile.elo_rating / 2000.0
        engine_flag = 1.0 if log.engine_used == 'hierarchical' else 0.0

        X.append([avg_acc, runs_z, avg_elo, engine_flag])
        y.append(1 if log.actual_result_correct else 0)
    return X, y


def train_outcome_classifier(X, y):
    """
    Fitted classifier, or None when the data can't support training.

    A random forest rather than plain logistic regression: the decision
    "which engine suits this student" is an interaction between the
    engine flag and the ability features, which a linear model cannot
    express (its engine coefficient would be a constant, so it would
    recommend the same engine for everyone).
    """
    if len(X) < 2 or len(set(y)) < 2:
        return None
    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(np.array(X), np.array(y))
    return clf


def evaluate_holdout(X, y, test_size=0.2):
    """
    §5 evaluation gate: holdout AUC + Brier for the artifact about to
    ship. A fresh model with the production hyperparameters is fitted on
    the train split and scored on the stratified holdout; the shipped
    artifact is then trained on ALL rows (standard practice — the eval
    estimates the pipeline, the product uses every observation).

    Returns the metrics dict, or None when a meaningful holdout cannot
    be built (too few rows, or a split without both classes) — in which
    case the gate fails and nothing may ship.
    """
    if len(X) < 10 or len(set(y)) < 2:
        return None
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            np.array(X), np.array(y),
            test_size=test_size, stratify=np.array(y), random_state=42,
        )
    except ValueError:
        return None
    if len(set(y_test)) < 2:
        return None

    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(X_train, y_train)
    probs = clf.predict_proba(X_test)[:, 1]

    return {
        "auc": round(float(roc_auc_score(y_test, probs)), 4),
        "brier": round(float(brier_score_loss(y_test, probs)), 4),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "positive_rate": round(float(np.mean(y)), 4),
    }


class Command(BaseCommand):
    """
    Retrains the production routing classifier from real recommendation
    outcomes, behind the §5 evaluation gate.

    Deliberately torch-free (M1/P1.1). This command previously also carried
    two dead sections: a "FINE-TUNE GCNs" loop whose body only printed, and
    a DKT/LSTM block that assembled sequences, constructed an nn.BCELoss()
    and discarded both — its own trailing note recorded that the LSTM had
    "been removed as the experimental model was trimmed for production".
    Between them they were the only reason this module imported torch, and
    the only reason the retraining path needed the ML extras at all.

    What survives is the path that actually reaches production:
    build_outcome_dataset -> train_outcome_classifier -> evaluate_holdout
    -> routing_classifier_v2.pkl, loaded by RoutingClassifier in
    groups/hybrid_router.py.
    """

    help = 'Retrains the production routing classifier from real RecommendationLogs'

    def handle(self, *args, **kwargs):
        self.stdout.write("[START] Starting Autonomous MLOps Retraining Pipeline...")

        logs = RecommendationLog.objects.filter(actual_result_correct__isnull=False)
        log_count = logs.count()

        if log_count < 100:
            self.stdout.write(self.style.WARNING(f"[WARN] Only {log_count} logs found. We need at least 100 to start fine-tuning. Skipping."))
            return

        self.stdout.write(f"[INFO] Found {log_count} real interactions. Re-training models...")

        # 1. RETRAIN META-CLASSIFIER on OUTCOMES (not on its own past routing)
        self.stdout.write("[INFO] Retraining outcome classifier v2 (Random Forest)...")
        X_route, y_route = build_outcome_dataset(logs)
        clf = train_outcome_classifier(X_route, y_route)

        if clf is None:
            self.stdout.write(self.style.WARNING(
                "[WARN] Not enough outcome variance to train. Skipping Meta-Classifier."
            ))
        else:
            # §5 evaluation gate — absolute: no artifact ships without a
            # holdout evaluation written to docs/evals/.
            evaluation = evaluate_holdout(X_route, y_route)
            if evaluation is None:
                self.stdout.write(self.style.WARNING(
                    "[GATE] Cannot compute a stratified holdout evaluation "
                    "(too few rows or single-class holdout). Artifact NOT saved."
                ))
            else:
                artifact_path = RoutingClassifier.artifact_path()
                os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
                trained_at = datetime.now(dt_timezone.utc)

                joblib.dump(clf, artifact_path)

                # Feature contract lives alongside the artifact (§5).
                contract = {
                    "artifact": RoutingClassifier.ARTIFACT_NAME,
                    "features": list(FEATURES_V2),
                    "target": "actual_result_correct",
                    "trained_at": trained_at.isoformat(),
                    "n_rows": len(X_route),
                }
                contract_path = artifact_path.replace(".pkl", ".contract.json")
                with open(contract_path, "w", encoding="utf-8") as fh:
                    json.dump(contract, fh, indent=2)

                # Evaluation artifact checked into docs/evals/ (§5).
                evals_dir = os.path.join(settings.BASE_DIR, "..", "..", "docs", "evals")
                os.makedirs(evals_dir, exist_ok=True)
                eval_path = os.path.join(
                    evals_dir,
                    f"routing_classifier_v2_{trained_at.strftime('%Y%m%d')}.json",
                )
                with open(eval_path, "w", encoding="utf-8") as fh:
                    json.dump({**contract, "holdout": evaluation}, fh, indent=2)

                self.stdout.write(self.style.SUCCESS(
                    f"[OK] v2 classifier trained on {len(X_route)} interactions "
                    f"(holdout AUC={evaluation['auc']}, Brier={evaluation['brier']}); "
                    f"artifact + contract saved, eval written to {eval_path}"
                ))

        self.stdout.write(self.style.SUCCESS('Routing classifier retraining complete.'))
