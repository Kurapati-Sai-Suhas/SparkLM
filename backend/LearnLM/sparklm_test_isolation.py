"""
Earliest-possible test-database isolation (M2 P2.7e follow-up).

Loaded via `-p sparklm_test_isolation` in `pytest.ini`, which imports this
module during pytest's plugin-registration phase — BEFORE pytest-django's
`pytest_load_initial_conftests` hook calls `django.setup()`, and therefore
before `LearnLM.settings` is ever imported.

That ordering is the whole point, and it was found the hard way:

  * Overriding `settings.DATABASES` in a conftest `pytest_configure` hook is
    TOO LATE — Django's `ConnectionHandler.settings` is a cached_property and
    the wrappers already hold the old config.
  * Setting os.environ at rootdir-conftest import time is ALSO TOO LATE —
    pytest-django configures Django in `pytest_load_initial_conftests`, which
    runs before rootdir conftests are imported.

A `-p` plugin is imported earlier than both.

── Why this makes production access impossible rather than unlikely ────────

`LearnLM.settings` reads the database from `os.getenv("POSTGRES_*")` and calls
`load_dotenv()`, which defaults to `override=False` — it will not replace a
variable that is already set. Writing the local values into os.environ here
means `.env`'s production credentials are IGNORED by pytest, whatever they
contain.

The credentials below are the ones committed in `docker-compose.yml`. They are
not secrets, and nothing from `.env` is read here.
"""

import os

#: Local Docker Postgres. `TEST_POSTGRES_*` allows a developer running
#: Postgres elsewhere on their machine to redirect it — but conftest's
#: locality assertion still applies afterwards, so it cannot be pointed at a
#: remote host.
_LOCAL = {
    "POSTGRES_DB": os.getenv("TEST_POSTGRES_DB", "learnlm_db"),
    "POSTGRES_USER": os.getenv("TEST_POSTGRES_USER", "postgres"),
    "POSTGRES_PASSWORD": os.getenv("TEST_POSTGRES_PASSWORD", "yourpassword"),
    "POSTGRES_HOST": os.getenv("TEST_POSTGRES_HOST", "127.0.0.1"),
    "POSTGRES_PORT": os.getenv("TEST_POSTGRES_PORT", "5432"),
}

#: What the environment held before the redirect, for the report header. Only
#: the HOST is retained — never a password.
ORIGINAL_HOST = os.environ.get("POSTGRES_HOST")

for _name, _value in _LOCAL.items():
    os.environ[_name] = _value

# ── The pre-image capture alias must never exist under pytest ──────────────
#
# `settings.py` creates the `preimage` alias whenever PREIMAGE_USER is set, and
# those credentials point at PRODUCTION with INSERT rights on the pre-image
# tables. Redirecting POSTGRES_* alone would leave that alias pointing at Neon
# during the whole test run — one `.using("preimage")` in a test, or one
# fixture that reached it, and the suite would be writing to production.
#
# Blanked rather than deleted. Deleting is not enough: `settings.py` calls
# `load_dotenv()`, which skips a key already present in os.environ but happily
# populates one that is absent — so a popped variable would be restored from
# `.env` moments later. An EMPTY value is present, so dotenv leaves it alone,
# and `if os.getenv("PREIMAGE_USER")` is falsy, so the alias is never created.
#
# The alias then does not exist at all: a test that reaches for it fails with
# "connection 'preimage' doesn't exist" rather than quietly succeeding against
# production.
_PREIMAGE_KEYS = ("PREIMAGE_DB", "PREIMAGE_USER", "PREIMAGE_PASSWORD",
                  "PREIMAGE_HOST", "PREIMAGE_PORT")

PREIMAGE_WAS_CONFIGURED = bool(os.environ.get("PREIMAGE_USER"))

for _name in _PREIMAGE_KEYS:
    os.environ[_name] = ""
