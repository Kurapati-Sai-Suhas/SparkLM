"""
groups/engines/elo_engine.py

Fixes applied:
- FIX-05 (HIGH / BUG-04): apply_time_decay() no longer resets
  last_practiced as a side effect of merely being called. Reworked
  further beyond the original fix spec: last_practiced is ONLY updated
  on real submissions (see calling code), and a separate
  last_decay_applied_at timestamp tracks decay application so that
  calling apply_time_decay() multiple times while the user stays
  inactive (e.g. on every dashboard load) does NOT reapply the same
  penalty repeatedly. The original FIX-05 spec removed the
  last_practiced reset but left days_inactive computed off
  last_practiced every call — that still double/triple-penalizes a
  user if this function fires more than once during one inactive
  window. This version fixes that by decaying only for days elapsed
  since the LAST decay check, not since last_practiced.

  Requires a new field on your user-progress model:
      last_decay_applied_at = models.DateTimeField(null=True, blank=True)
  (defaults to last_practiced if never set.)
"""

import math
from django.utils import timezone
from groups.models import UserTopicMastery, Question


class EloEngine:
    K_FACTOR = 32

    @staticmethod
    def calculate_new_rating(user_rating: float, question_difficulty: float, is_correct: bool,
                              execution_time_ms=None, memory_used_kb=None) -> dict:
        expected_score = 1 / (1 + math.pow(10, (question_difficulty - user_rating) / 400))
        actual_score = 1.0 if is_correct else 0.0
        raw_change = EloEngine.K_FACTOR * (actual_score - expected_score)

        if not is_correct:
            return {
                "old_rating": round(user_rating, 2),
                "new_rating": round(user_rating + raw_change, 2),
                "rating_change": round(raw_change, 2),
                "insight": "❌ Failed hidden test cases. Focus on logical correctness before optimizing."
            }

        time_multiplier = 1.0
        space_multiplier = 1.0
        insight = "✅ Solution Accepted! Solid algorithmic logic."

        if execution_time_ms is not None and memory_used_kb is not None:
            if execution_time_ms < 60:
                time_multiplier = 1.5  # Highly optimal
                insight = "🚀 Blazing fast! Your Time Complexity is highly optimal."
            elif execution_time_ms > 250:
                time_multiplier = 0.5  # Brute-force penalty
                insight = "⚠️ Correct, but slow. You might be using an O(n²) brute-force approach. Try to optimize!"

            if memory_used_kb < 30000:
                space_multiplier = 1.2  # Highly optimal memory management
            elif memory_used_kb > 55000:
                space_multiplier = 0.8  # Memory leak penalty
                if time_multiplier >= 1.0:
                    insight = "⚠️ Fast, but heavy memory usage. Can you solve this without creating extra arrays?"

        final_change = raw_change * time_multiplier * space_multiplier
        # Clamp gains to [0, 50]. The old floor of +2 guaranteed rating
        # points for ANY accepted solution, letting users inflate Elo by
        # grinding problems far below their level — the expected-score
        # curve should be what makes trivial wins worth ~0.
        final_change = min(max(final_change, 0.0), 50.0)
        new_rating = round(user_rating + final_change, 2)

        return {
            "old_rating": round(user_rating, 2),
            "new_rating": new_rating,
            "rating_change": round(final_change, 2),
            "insight": insight
        }

    @staticmethod
    def apply_time_decay(user_progress) -> dict:
        """
        FIX-05: Apply Ebbinghaus-style decay based on inactivity.

        - days_inactive: how long since the user's last REAL submission
          (last_practiced). Used to decide WHETHER decay applies (>7 days)
          and to size the penalty on the first decay check.
        - last_decay_applied_at: when we last actually charged a penalty.
          Prevents re-charging the same inactive window every time this
          function is invoked (e.g. once per page load).
        """
        now = timezone.now()
        days_inactive = (now - user_progress.last_practiced).days

        if days_inactive <= 7:
            return {"decayed": False, "days_inactive": days_inactive}

        last_check = getattr(user_progress, 'last_decay_applied_at', None) or user_progress.last_practiced
        days_since_last_check = (now - last_check).days

        if days_since_last_check <= 0:
            # Already charged decay for this window very recently — no-op.
            return {"decayed": False, "days_inactive": days_inactive}

        penalty = days_since_last_check * 2.0
        new_rating = max(800.0, user_progress.elo_rating - penalty)

        # FIX-05: DO NOT touch last_practiced here — that field should only
        # move forward on a real submission. We only stamp last_decay_applied_at.
        user_progress.elo_rating = new_rating
        user_progress.last_decay_applied_at = now
        user_progress.save(update_fields=['elo_rating', 'last_decay_applied_at'])

        return {
            "decayed": True,
            "days_inactive": days_inactive,
            "penalty": round(penalty, 2),
            "new_rating": round(new_rating, 2)
        }

    @staticmethod
    def record_real_submission(user_progress):
        """
        Call this ONLY when the user actually submits code — this is the
        sole place last_practiced should move forward. Also resets the
        decay checkpoint since the inactive window has ended.
        """
        now = timezone.now()
        user_progress.last_practiced = now
        user_progress.last_decay_applied_at = now
        user_progress.save(update_fields=['last_practiced', 'last_decay_applied_at'])
