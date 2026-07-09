"""
groups/engines/mirt_engine.py

Fixes applied:
- FIX-10 (MEDIUM / BUG-12): calculate_probability() had no guard if
  latent scores somehow exceeded the [-4.0, 4.0] range enforced by
  update_latents() (e.g. if a value was set directly, via a migration,
  admin edit, or a future code path that bypasses update_latents()).
  An out-of-range value could push math.exp()'s argument high enough
  to raise OverflowError, which was silently caught and returned 0.0 —
  a completely wrong probability with no visibility into why.
  Now defensively clamps inputs AND the exponent itself.
"""

import math


class MIRTEngine:
    @staticmethod
    def calculate_probability(latent_logic: float, latent_syntax: float,
                               latent_optimization: float, difficulty: float) -> float:
        # FIX-10: clamp inputs defensively, independent of update_latents()
        latent_logic = max(-4.0, min(4.0, latent_logic))
        latent_syntax = max(-4.0, min(4.0, latent_syntax))
        latent_optimization = max(-4.0, min(4.0, latent_optimization))

        b = (difficulty - 1200) / 400.0
        theta_sum = latent_logic + latent_syntax + latent_optimization
        exponent = -(theta_sum - b)

        # FIX-10: clamp the exponent itself so math.exp() can never overflow
        # (math.exp raises OverflowError above ~709.78)
        exponent = max(-500.0, min(500.0, exponent))

        return 1.0 / (1.0 + math.exp(exponent))

    @staticmethod
    def update_latents(logic: float, syntax: float, opt: float, status: str, difficulty: float):
        lr = 0.1
        if status == "accepted":
            logic += lr
            syntax += lr
            opt += lr
        elif status == "compile_error":
            syntax -= lr * 2
        elif status == "time_limit":
            opt -= lr * 2
        elif status in ["wrong_answer", "runtime_error"]:
            logic -= lr * 2

        return {
            "latent_logic": max(-4.0, min(4.0, logic)),
            "latent_syntax": max(-4.0, min(4.0, syntax)),
            "latent_optimization": max(-4.0, min(4.0, opt))
        }
