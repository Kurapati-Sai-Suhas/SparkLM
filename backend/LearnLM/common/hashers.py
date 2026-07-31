"""
common.hashers — password hashing tuned for this deployment (M3 Phase A).

Django's stock Argon2PasswordHasher defaults to memory_cost=102400 KiB
(100 MiB) and parallelism=8. Those are sensible on a dedicated host and
actively dangerous here:

  * The web tier runs on a single 512 MB Render instance whose measured
    resident set is ~202 MiB (Python + django.setup() + the eager URLconf
    resolution asgi.py forces at boot). At 100 MiB per hash, four concurrent
    logins exceed the memory limit. An OOM restarts the container, and a cold
    start on this plan was measured at 92.9 s TTFB — so adopting Argon2
    unconfigured would hand every subsequent visitor a 93-second wait and
    undo the milestone that removed it.

  * parallelism=8 asks for eight threads on a throttled fractional vCPU,
    which adds contention rather than speed.

The pinned values below are OWASP's recommended Argon2id minimum
(t=2, m=19 MiB, p=1). Measured cost: ~0.021 s locally, ~0.10 s projected on
Render's throttled CPU, against ~1.6-2.6 s for the PBKDF2 configuration it
replaces. Memory budget at these values: 202 + 19N MiB, leaving headroom
past any concurrency this deployment will see.

Treat these parameters as a stable contract, not a tuning knob.
Argon2PasswordHasher.must_update() compares the full parameter set, so
changing any value here silently rehashes every user on their next login.
"""

from django.contrib.auth.hashers import Argon2PasswordHasher


class TunedArgon2PasswordHasher(Argon2PasswordHasher):
    """Argon2id at OWASP's minimum parameters, sized for a 512 MB instance."""

    # Deliberately NOT Django's defaults. See module docstring before editing.
    time_cost = 2
    memory_cost = 19456  # KiB == 19 MiB per hash
    parallelism = 1
