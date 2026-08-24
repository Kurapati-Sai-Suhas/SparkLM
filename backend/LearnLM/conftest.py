"""
Test-database isolation guard (M2 P2.7e follow-up).

pytest MUST NEVER touch the production database. This file makes that a
property of the test runner rather than a rule someone has to remember.

── The hazard this closes ──────────────────────────────────────────────────

`pytest.ini` points at `LearnLM.settings`, which calls `load_dotenv()` at
import time. Once `.env` carried production `POSTGRES_*` (added for the P2.7e
census), `DATABASES['default']['HOST']` became the Neon endpoint — so
pytest-django derived its test database as `test_neondb` ON THE PRODUCTION
SERVER and tried to CREATE it there.

It failed only because the census role is read-only. Introduce a write-capable
role for the pilot and the same invocation would CREATE a database on
production and DROP it on teardown.

── Why an override alone is not enough ─────────────────────────────────────

Overriding `DATABASES` would fix the common case and fail silently if it ever
stopped applying — a future settings change, a plugin ordering shift, an
`-p no:cacheprovider`. So the override is followed by an ASSERTION, and a
non-local host aborts the entire session with `pytest.exit`. The safe outcome
of any future breakage is "tests refuse to run", never "tests ran against
production".

The local credentials below are the ones committed in `docker-compose.yml`.
They are not secrets, and nothing from `.env` is read here.
"""

import os

import pytest

#: Hosts a test run may target. Anything else aborts the session.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "localhost.localdomain"})

#: Local Docker Postgres, from docker-compose.yml. Overridable via TEST_* for
#: a developer running Postgres elsewhere locally — but the locality assertion
#: in `pytest_configure` still applies, so TEST_POSTGRES_HOST cannot point at
#: production.
_LOCAL_DATABASE = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": os.getenv("TEST_POSTGRES_DB", "learnlm_db"),
    "USER": os.getenv("TEST_POSTGRES_USER", "postgres"),
    "PASSWORD": os.getenv("TEST_POSTGRES_PASSWORD", "yourpassword"),
    "HOST": os.getenv("TEST_POSTGRES_HOST", "127.0.0.1"),
    "PORT": os.getenv("TEST_POSTGRES_PORT", "5432"),
}


# ── The override has to happen HERE, at import time ─────────────────────────
#
# A first version overrode `settings.DATABASES` inside `pytest_configure` and
# DID NOT WORK: pytest-django calls `django.setup()` in its own
# `pytest_configure`, which runs before this file's, and Django's
# `ConnectionHandler.settings` is a cached_property. By the time the hook fired
# the connection wrappers already held the Neon config, and pytest still tried
# to create `test_neondb` on production.
#
# conftest.py is imported during `pytest_load_initial_conftests`, i.e. BEFORE
# Django is configured. Writing the variables `settings.py` reads into
# os.environ here therefore wins — and `load_dotenv()` defaults to
# `override=False`, so the values in `.env` cannot clobber them afterwards.
#
# This inverts the danger: production credentials in `.env` are IGNORED by
# pytest rather than inherited by it.
_ENV_OVERRIDES = {
    "POSTGRES_DB": _LOCAL_DATABASE["NAME"],
    "POSTGRES_USER": _LOCAL_DATABASE["USER"],
    "POSTGRES_PASSWORD": _LOCAL_DATABASE["PASSWORD"],
    "POSTGRES_HOST": _LOCAL_DATABASE["HOST"],
    "POSTGRES_PORT": _LOCAL_DATABASE["PORT"],
}
for _name, _value in _ENV_OVERRIDES.items():
    os.environ[_name] = _value


def _is_local(host):
    """Whether `host` is unambiguously a local address."""
    if not host:
        # An empty HOST means a unix socket on this machine, which is local.
        return True
    if host.lower() in _LOCAL_HOSTS:
        return True
    import ipaddress
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A hostname that is not in the allowlist. Neon endpoints land here,
        # and so would any other remote name — treated as NOT local, which is
        # the safe direction for an unrecognised value.
        return False
    return address.is_loopback


def pytest_configure(config):
    """
    Prove the override held. Abort the session if it did not.

    The env-var override above is what actually redirects the database; this
    is the assertion that it worked. Deliberately a separate mechanism: if a
    future Django or pytest-django change defeats the override, the safe
    outcome is "tests refuse to run", never "tests ran against production".
    """
    from django.conf import settings

    resolved = settings.DATABASES.get("default", {}).get("HOST")
    if not _is_local(resolved):
        pytest.exit(
            f"REFUSING TO RUN: the test database resolved to a non-local host "
            f"({resolved!r}). pytest must never target production. The "
            f"conftest env-var override in {__file__} did not take effect — "
            f"investigate before running any test.",
            returncode=3,
        )

    name = settings.DATABASES["default"].get("NAME", "")
    if name and name != _LOCAL_DATABASE["NAME"]:
        pytest.exit(
            f"REFUSING TO RUN: test database name {name!r} is not the expected "
            f"local database {_LOCAL_DATABASE['NAME']!r}.",
            returncode=3,
        )


def pytest_report_header(config):
    """Make the test database target visible on every run. No credentials."""
    from django.conf import settings

    database = settings.DATABASES["default"]
    line = (f"sparklm: test database -> {database['HOST']}:{database['PORT']}"
            f"/{database['NAME']} (local only)")
    if getattr(config, "_sparklm_db_redirected", False):
        line += "\nsparklm: NOTE - settings pointed at a REMOTE host; " \
                "redirected to local for testing"
    return line
