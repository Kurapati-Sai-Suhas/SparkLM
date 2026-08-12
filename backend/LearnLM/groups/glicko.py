"""
Glicko-2 rating math (M2 P2.9a).

Pure functions. No ORM, no Django, no I/O — so the arithmetic can be validated
against Glickman's published worked example rather than against our own
expectations of it.

── Why Glicko-2 rather than the Elo already here ───────────────────────────

The existing `EloEngine` moves ONE number: the learner's. A question's
`base_difficulty` is a three-valued label from a CSV that no code path has ever
updated, so the learner is measured against a ruler with three marks that never
move. That is not a rating system; it is a score, and it cannot converge.

Glicko-2 adds the two things the platform has no representation for:

  * **Uncertainty.** RD (rating deviation) distinguishes "1400, measured over
    forty solves" from "1400, guessed". Without it there is no principled way
    to explore, to cold-start, or to know when a rating means anything.
  * **Time.** RD inflates during inactivity, which is a forgetting model that
    actually feeds selection — unlike the existing HLR half-life, which is
    computed, displayed, emailed, and never routed on.

It is also two-sided: the question is an opponent and its rating moves too, so
difficulty becomes learned instead of asserted.

── Conventions, stated because they are choices ────────────────────────────

**Rating period = one submission.** Glicko-2 is defined over batched rating
periods. Updating once per game is a standard online simplification; it makes
each update slightly more reactive than a batched one. Recorded here rather
than discovered later.

**Scale centre is 1200, not Glickman's 1500.** The 1500 in the paper is a pure
offset: every comparison uses differences, so it cancels. Only the slope
constant (400/ln 10) is meaningful, and it is unchanged. 1200 keeps the shadow
model on the same scale as the Elo the UI already shows, so the two are
directly comparable — which is the entire point of shadow mode.

**Inactivity is measured in days.** `RATING_PERIOD_DAYS = 1.0`.
"""

import math

#: Glicko-2's scale factor, 400/ln(10). Converts between the human-readable
#: rating scale and the internal one.
SCALE = 173.7178

#: Centre of the human-readable scale. See the module docstring — this is an
#: offset that cancels in every comparison, chosen to match the platform's
#: existing 1200-centred Elo rather than Glickman's 1500.
CENTRE = 1200.0

#: Cold-start values. RD_MAX is also the ceiling that inactivity inflation is
#: clamped to: beyond it the rating carries no information anyway, and letting
#: RD grow without bound makes the sampled ability meaningless.
DEFAULT_RATING = 1200.0
DEFAULT_RD = 350.0
DEFAULT_VOLATILITY = 0.06

RD_MIN = 30.0
RD_MAX = 350.0

#: System constant τ. Constrains how much volatility may move per period.
#: Glickman recommends 0.3–1.2; smaller is more conservative. 0.5 is the
#: paper's own worked-example value and is a deliberate mid-range default —
#: there is no data yet on which to tune it.
TAU = 0.5

#: One rating period. Inactivity of N days inflates RD by N periods.
RATING_PERIOD_DAYS = 1.0

CONVERGENCE = 1e-6
MAX_ITERATIONS = 100


def to_glicko2(rating, rd):
    """Human scale -> Glicko-2 internal scale (mu, phi)."""
    return (rating - CENTRE) / SCALE, rd / SCALE


def from_glicko2(mu, phi):
    """Glicko-2 internal scale -> human scale (rating, rd)."""
    return mu * SCALE + CENTRE, phi * SCALE


def g(phi):
    """Glickman's g(phi): how much an opponent's uncertainty damps the update."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def expected_score(mu, opponent_mu, opponent_phi):
    """E: probability that `mu` beats `opponent_mu`, damped by their phi."""
    return 1.0 / (1.0 + math.exp(-g(opponent_phi) * (mu - opponent_mu)))


def _new_volatility(phi, sigma, delta, v, tau=TAU):
    """
    Step 5 of Glicko-2: solve for the new volatility by the Illinois variant
    of regula falsi, exactly as specified in the paper.
    """
    a = math.log(sigma * sigma)
    delta_sq = delta * delta
    phi_sq = phi * phi

    def f(x):
        ex = math.exp(x)
        numerator = ex * (delta_sq - phi_sq - v - ex)
        denominator = 2.0 * (phi_sq + v + ex) ** 2
        return numerator / denominator - (x - a) / (tau * tau)

    A = a
    if delta_sq > phi_sq + v:
        B = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0 and k < MAX_ITERATIONS:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    iterations = 0
    while abs(B - A) > CONVERGENCE and iterations < MAX_ITERATIONS:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA = fA / 2.0
        B, fB = C, fC
        iterations += 1

    return math.exp(A / 2.0)


def inflate_rd(rd, volatility, periods):
    """
    RD growth over `periods` of inactivity — the forgetting mechanism.

    phi* = sqrt(phi^2 + sigma^2 * t). Monotonically non-decreasing in
    `periods`, clamped to RD_MAX so an abandoned account does not produce a
    meaningless sampled ability.
    """
    if periods <= 0:
        return min(max(rd, RD_MIN), RD_MAX)
    _, phi = to_glicko2(CENTRE, rd)
    phi_star = math.sqrt(phi * phi + volatility * volatility * periods)
    _, inflated = from_glicko2(0.0, phi_star)
    return min(max(inflated, RD_MIN), RD_MAX)


def rate(rating, rd, volatility, opponents, periods_inactive=0.0, tau=TAU):
    """
    One Glicko-2 update.

    `opponents` is a sequence of (opponent_rating, opponent_rd, score) where
    score is 1.0 for a win and 0.0 for a loss. Returns
    (new_rating, new_rd, new_volatility).

    With no opponents this is the "did not compete" case: the rating is
    unchanged and RD inflates by the elapsed periods, which is Glickman's
    step 6 applied on its own.
    """
    rd = inflate_rd(rd, volatility, periods_inactive)

    if not opponents:
        return rating, rd, volatility

    mu, phi = to_glicko2(rating, rd)

    # Step 3: estimated variance of the rating based on game outcomes.
    v_inv = 0.0
    delta_sum = 0.0
    for opp_rating, opp_rd, score in opponents:
        opp_mu, opp_phi = to_glicko2(opp_rating, opp_rd)
        g_phi = g(opp_phi)
        e = expected_score(mu, opp_mu, opp_phi)
        v_inv += g_phi * g_phi * e * (1.0 - e)
        delta_sum += g_phi * (score - e)

    if v_inv <= 0.0:
        # Every opponent is so far away that E saturates; no information.
        return from_glicko2(mu, phi) + (volatility,)

    v = 1.0 / v_inv
    delta = v * delta_sum

    # Step 5: new volatility.
    new_sigma = _new_volatility(phi, volatility, delta, v, tau)

    # Steps 6-7: pre-period RD, then the post-update RD.
    phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)

    # Step 8: the rating itself.
    new_mu = mu + new_phi * new_phi * delta_sum

    new_rating, new_rd = from_glicko2(new_mu, new_phi)
    return new_rating, min(max(new_rd, RD_MIN), RD_MAX), new_sigma


def win_probability(rating, rd, opponent_rating, opponent_rd):
    """
    P(learner solves this question), accounting for BOTH uncertainties.

    Used for reporting and for the target-difficulty band, never for grading.
    """
    mu, phi = to_glicko2(rating, rd)
    opp_mu, opp_phi = to_glicko2(opponent_rating, opponent_rd)
    combined_phi = math.sqrt(phi * phi + opp_phi * opp_phi)
    return expected_score(mu, opp_mu, combined_phi)
