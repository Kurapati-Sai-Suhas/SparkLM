"""
groups/engines/hlr_engine.py

Fixes applied:
- FIX-09 (MEDIUM / BUG-07): calculate_memory_state() used to return 0.0
  when halflife <= 0, which for a brand-new user (uninitialized halflife)
  triggered a false "you've forgotten everything" alert on their very
  first session. Now returns 1.0 (full retention / no history yet)
  instead. Also guards against a future-dated last-review timestamp.
"""

import math


class HLREngine:
    """
    Continuous Half-Life Regression model for predicting memory decay.
    """

    @staticmethod
    def calculate_memory_state(time_since_last_review_days: float, halflife: float) -> float:
        # FIX-09: uninitialized halflife (0 or negative) means "no history
        # yet" for this user/topic — treat as fresh (100% retention), not
        # as fully forgotten (0%).
        if halflife <= 0:
            return 1.0

        # FIX-09: guard against a future-dated last_practiced (clock skew,
        # bad data) producing a negative elapsed time.
        if time_since_last_review_days < 0:
            return 1.0

        return math.pow(2.0, -time_since_last_review_days / halflife)

    @staticmethod
    def update_halflife(quality: int, prev_halflife: float) -> float:
        if prev_halflife < 1.0:
            prev_halflife = 1.0

        if quality >= 4:
            return prev_halflife * 2.5  # Mastered easily
        elif quality == 3:
            return prev_halflife * 1.2  # Passed but struggled
        else:
            # Failed to recall, decay half-life drastically to force review
            return max(1.0, prev_halflife * 0.3)
