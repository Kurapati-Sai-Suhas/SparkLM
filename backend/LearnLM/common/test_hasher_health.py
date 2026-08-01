"""
M3 Phase C — operational close-out for the password migration.

Two things Phase A/B left without runtime coverage:

  * `verify_password_hashers()` — a boot probe for the one M3 hazard that is
    otherwise invisible. Dropping the Argon2 hasher locks out every migrated
    account, and Django reports it as an ordinary failed login.
  * `manage.py password_hash_status` — the answer to "is the migration
    window closed yet?", which docs/DEPLOYMENT.md previously answered with a
    shell snippet operators had to paste by hand.
"""

import logging
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import override_settings

from common.apps import verify_password_hashers

User = get_user_model()

PASSWORD = "M3Phase#2026c"
ARGON2_ONLY = ["common.hashers.TunedArgon2PasswordHasher"]
PBKDF2_ONLY = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
ROLLBACK_ORDER = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "common.hashers.TunedArgon2PasswordHasher",
]


# ── boot probe ───────────────────────────────────────────────────────────

class TestVerifyPasswordHashers:
    def test_passes_on_the_shipped_configuration(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert verify_password_hashers() is True
        assert caplog.records == [], "shipped config should log nothing above DEBUG"

    @override_settings(PASSWORD_HASHERS=PBKDF2_ONLY)
    def test_reports_an_error_when_argon2_is_removed(self, caplog):
        """
        The deploy accident this exists to catch. Must be an ERROR and must
        name the consequence, because whoever reads it is looking at a wave
        of 'wrong password' reports with no other clue.
        """
        with caplog.at_level(logging.ERROR):
            assert verify_password_hashers() is False

        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert "ARGON2 HASHER ABSENT" in message
        assert "CANNOT authenticate" in message
        assert "REORDER" in message, "must point the reader at the safe rollback"

    @override_settings(PASSWORD_HASHERS=ROLLBACK_ORDER)
    def test_a_deliberate_rollback_warns_but_does_not_error(self, caplog):
        """
        Reordering is the SUPPORTED rollback, so it must not look like an
        outage. Warn (the preferred hasher changed) without crying wolf.
        """
        with caplog.at_level(logging.DEBUG):
            assert verify_password_hashers() is True

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not errors, "a supported rollback must not log an ERROR"
        assert len(warnings) == 1
        assert "rollback" in warnings[0].getMessage().lower()

    def test_never_raises_even_when_hasher_construction_fails(self, caplog):
        """A boot probe that can crash the process is worse than no probe."""
        with override_settings(PASSWORD_HASHERS=["nonexistent.module.NoSuchHasher"]):
            with caplog.at_level(logging.ERROR):
                assert verify_password_hashers() is False
        assert "PASSWORD HASHERS UNUSABLE" in caplog.records[0].getMessage()


# ── management command ───────────────────────────────────────────────────

def run_status(**kwargs):
    out, err = StringIO(), StringIO()
    code = 0
    try:
        call_command("password_hash_status", stdout=out, stderr=err, **kwargs)
    except SystemExit as exc:
        code = exc.code
    return out.getvalue(), err.getvalue(), code


@pytest.mark.django_db
class TestPasswordHashStatus:
    def test_counts_each_population_separately(self):
        User.objects.create_user(username="new1", password=PASSWORD, email="n1@t.com")
        with override_settings(PASSWORD_HASHERS=PBKDF2_ONLY):
            legacy = User.objects.create_user(
                username="old1", password=PASSWORD, email="o1@t.com"
            )
            legacy.password = make_password(PASSWORD)
            legacy.save(update_fields=["password"])
        sso = User.objects.create_user(username="sso1", email="s1@t.com")
        sso.set_unusable_password()
        sso.save(update_fields=["password"])

        out, _, code = run_status()
        assert code == 0
        assert "argon2" in out and "pbkdf2_sha256" in out
        # SSO must be excluded from the denominator or the window can never close.
        assert "1/2" in out, f"expected 1 of 2 migratable accounts, got:\n{out}"
        assert "unusable (SSO)" in out

    def test_reports_the_window_closed_when_no_legacy_hashes_remain(self):
        User.objects.create_user(username="a1", password=PASSWORD, email="a1@t.com")
        out, _, code = run_status()
        assert code == 0
        assert "CLOSED" in out

    def test_an_open_window_is_reported_but_is_not_an_error(self):
        """
        An unmigrated account is the normal, expected state for months — it
        must read as progress, never as a fault. Only an actual lockout
        (unreadable hash) is allowed to exit non-zero.
        """
        with override_settings(PASSWORD_HASHERS=PBKDF2_ONLY):
            u = User.objects.create_user(
                username="old2", password=PASSWORD, email="o2@t.com"
            )
            u.password = make_password(PASSWORD)
            u.save(update_fields=["password"])

        out, _, code = run_status()
        assert code == 0
        assert "awaiting first sign-in" in out
        assert "CLOSED" not in out

    def test_unreadable_hashes_are_escalated_not_counted_as_migrated(self):
        """
        The lockout signature. An unreadable hash means someone removed a
        hasher and those users cannot log in RIGHT NOW, so this has to be
        loud and non-zero — never folded into the progress percentage.
        """
        u = User.objects.create_user(username="broken", password=PASSWORD, email="b@t.com")
        u.password = "somealgo$99$deadbeef$notarealhash"
        u.save(update_fields=["password"])

        out, err, code = run_status()
        assert code == 2, "an active lockout must not exit 0"
        assert "NO configured hasher can read" in err
        assert "broken" in err
        assert "PASSWORD_HASHERS" in err

    def test_flags_that_disabled_accounts_can_be_migrated_without_signing_in(self):
        """
        Guards the misreading. A disabled account still rehashes on a correct
        password (ModelBackend checks the password before is_active), so the
        counter can advance for accounts that cannot log in.
        """
        User.objects.create_user(
            username="off", password=PASSWORD, email="off@t.com", is_active=False
        )
        out, _, _ = run_status()
        assert "disabled" in out and "does not" in out
