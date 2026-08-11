"""
Environment classification for destructive operations (M2 P2.5, Phase 4).

`seed_data` ran `Question.objects.all().delete()` unconditionally — no flag,
no confirmation, no environment check. `CodeSubmission.question` and
`AgenticCoachLog.question` are both `on_delete=CASCADE`, so that one statement
deletes every learner's submission history along with the questions. Nothing
scheduled it, but the roadmap calls for a DAILY reseed, and a daily job is
exactly how an unguarded destructive command eventually gets pointed at
production.

Two properties matter more than convenience here:

**Fail safe, not fail open.** `SPARKLM_ENV` unset means PRODUCTION. A guard
that defaults to permissive protects nothing — the machine that has not been
configured is precisely the one you cannot vouch for. Deliberately not derived
from `settings.DEBUG`, which defaults to `true` when `DJANGO_DEBUG` is unset
and would therefore treat an unconfigured host as disposable.

**Allowlist, not denylist.** Only the names below are disposable. A typo
(`SPARKLM_ENV=prod`, `SPARKLM_ENV=develpment`) lands on production, which is
the safe direction to be wrong in.

Production needs no change to be protected: `render.yaml` does not set
`SPARKLM_ENV`, so the deployed service is already classified production.
"""

import os

from django.core.management.base import CommandError

#: Environments whose data may be destroyed and rebuilt. Everything else —
#: including anything unset, misspelled or unknown — is treated as production.
DISPOSABLE_ENVIRONMENTS = frozenset({"development", "test", "ci"})

ENV_VAR = "SPARKLM_ENV"


def current_environment():
    """The declared environment name, defaulting to the safest answer."""
    return (os.getenv(ENV_VAR) or "production").strip().lower() or "production"


def is_disposable_environment():
    """True only where wiping data is acceptable."""
    return current_environment() in DISPOSABLE_ENVIRONMENTS


def require_disposable_environment(operation, acknowledged=False):
    """
    Gate a destructive operation behind BOTH an environment declaration and an
    explicit per-invocation acknowledgement.

    Two independent conditions on purpose. The environment variable alone
    would let a scheduled job inherit a disposable environment and destroy a
    developer's working database without anyone typing the intent; the flag
    alone would let a mistyped command destroy production. Requiring both
    means no single mistake is sufficient.

    Raises CommandError — the operation must abort, never proceed partially.
    """
    env = current_environment()

    if env not in DISPOSABLE_ENVIRONMENTS:
        raise CommandError(
            f"REFUSED: '{operation}' destroys data and {ENV_VAR}={env!r}.\n"
            f"This operation is permitted only where "
            f"{ENV_VAR} is one of: {', '.join(sorted(DISPOSABLE_ENVIRONMENTS))}.\n"
            f"Deleting questions cascades into CodeSubmission and "
            f"AgenticCoachLog, which would destroy real learner history.\n"
            f"If this really is a disposable environment, set {ENV_VAR} "
            f"explicitly — it is never inferred."
        )

    if not acknowledged:
        raise CommandError(
            f"REFUSED: '{operation}' destroys data and was not acknowledged.\n"
            f"{ENV_VAR}={env!r} permits it, but the destructive flag was not "
            f"passed. Re-run with the flag shown in the command's --help once "
            f"you are certain which database you are pointed at."
        )
