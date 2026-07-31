"""
Password hashing contract (M3 Phase A).

Migrating the hasher of a live user base has one unforgiving failure mode:
every legacy account must keep working, and the rollback path must not lock
anyone out. Both are behavioural, not theoretical, so they are executed here
rather than reasoned about.

The parameter assertions matter as much as the login tests. Django's stock
Argon2 defaults (100 MiB, parallelism 8) would OOM the 512 MB production
instance at four concurrent logins, and an OOM there costs every subsequent
visitor a ~93 s cold start. Anyone "simplifying" common.hashers back to the
defaults must fail CI.
"""

import pytest
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.hashers import TunedArgon2PasswordHasher

User = get_user_model()

PASSWORD = "M3Migrate#2026x"
LEGACY_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]


def legacy_pbkdf2_hash(raw=PASSWORD):
    """A password stored the way every pre-M3 account is stored."""
    with override_settings(PASSWORD_HASHERS=LEGACY_HASHERS):
        return make_password(raw)


# ── configuration ────────────────────────────────────────────────────────

class TestHasherConfiguration:
    def test_argon2_is_preferred_and_pbkdf2_is_retained(self):
        assert settings.PASSWORD_HASHERS[0] == "common.hashers.TunedArgon2PasswordHasher"
        assert "django.contrib.auth.hashers.PBKDF2PasswordHasher" in settings.PASSWORD_HASHERS, (
            "PBKDF2 must remain listed or every pre-M3 account is locked out"
        )

    def test_parameters_are_pinned_away_from_django_defaults(self):
        """
        Django defaults: time_cost=2, memory_cost=102400 (100 MiB), parallelism=8.
        At 100 MiB/hash, 4 concurrent logins exceed the 512 MB instance.
        """
        assert TunedArgon2PasswordHasher.time_cost == 2
        assert TunedArgon2PasswordHasher.memory_cost == 19456  # 19 MiB
        assert TunedArgon2PasswordHasher.parallelism == 1

        from django.contrib.auth.hashers import Argon2PasswordHasher
        assert TunedArgon2PasswordHasher.memory_cost < Argon2PasswordHasher.memory_cost
        assert TunedArgon2PasswordHasher.parallelism < Argon2PasswordHasher.parallelism

    def test_memory_budget_fits_the_production_instance(self):
        """202 MiB measured resident set + N concurrent hashes must stay under 512 MB."""
        app_mib, limit_mib = 202, 512
        per_hash_mib = TunedArgon2PasswordHasher.memory_cost / 1024
        assert app_mib + per_hash_mib * 10 < limit_mib, (
            "10 concurrent logins must not exhaust the instance"
        )

    def test_algorithm_is_argon2id(self):
        encoded = make_password(PASSWORD)
        assert encoded.startswith("argon2$argon2id$"), encoded[:24]


# ── legacy accounts ──────────────────────────────────────────────────────

@pytest.mark.django_db
class TestLegacyPbkdf2Accounts:
    def test_legacy_hash_still_verifies(self):
        legacy = legacy_pbkdf2_hash()
        assert legacy.startswith("pbkdf2_sha256$")
        assert check_password(PASSWORD, legacy) is True

    def test_legacy_user_can_still_log_in(self):
        user = User.objects.create(username="legacy_login", email="l@test.com")
        user.password = legacy_pbkdf2_hash()
        user.save(update_fields=["password"])

        assert authenticate(username="legacy_login", password=PASSWORD) is not None

    def test_wrong_password_is_still_rejected_for_a_legacy_account(self):
        user = User.objects.create(username="legacy_wrong", email="w@test.com")
        user.password = legacy_pbkdf2_hash()
        user.save(update_fields=["password"])

        assert authenticate(username="legacy_wrong", password="not-the-password") is None
        user.refresh_from_db()
        assert user.password.startswith("pbkdf2_sha256$"), "a failed login must never rehash"


@pytest.mark.django_db
class TestTransparentRehash:
    def test_successful_login_upgrades_the_stored_hash_in_the_database(self):
        """
        The migration path for the whole user base. AbstractBaseUser.check_password
        supplies a setter that calls save(update_fields=["password"]), so the
        upgrade must be persisted — not merely computed in memory.
        """
        user = User.objects.create(username="rehash_me", email="r@test.com")
        user.password = legacy_pbkdf2_hash()
        user.save(update_fields=["password"])

        assert authenticate(username="rehash_me", password=PASSWORD) is not None

        user.refresh_from_db()
        assert user.password.startswith("argon2$"), "hash was not upgraded on login"
        assert identify_hasher(user.password).algorithm == "argon2"

    def test_the_upgraded_hash_still_authenticates_afterwards(self):
        user = User.objects.create(username="rehash_twice", email="r2@test.com")
        user.password = legacy_pbkdf2_hash()
        user.save(update_fields=["password"])

        authenticate(username="rehash_twice", password=PASSWORD)
        assert authenticate(username="rehash_twice", password=PASSWORD) is not None

    def test_rehash_does_not_change_the_password_itself(self):
        user = User.objects.create(username="rehash_same_pw", email="r3@test.com")
        user.password = legacy_pbkdf2_hash()
        user.save(update_fields=["password"])

        authenticate(username="rehash_same_pw", password=PASSWORD)
        user.refresh_from_db()
        assert user.check_password(PASSWORD) is True


# ── new accounts and API surface ─────────────────────────────────────────

@pytest.mark.django_db
class TestNewAccounts:
    def test_new_user_is_stored_with_argon2(self):
        user = User.objects.create_user(username="fresh", password=PASSWORD, email="f@test.com")
        assert user.password.startswith("argon2$")

    def test_registration_endpoint_stores_argon2(self):
        response = APIClient().post(
            reverse("register"),
            {"username": "api_reg", "email": "api_reg@test.com", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 201
        assert User.objects.get(username="api_reg").password.startswith("argon2$")

    def test_token_endpoint_authenticates_a_new_account(self):
        User.objects.create_user(username="jwt_user", password=PASSWORD, email="j@test.com")
        response = APIClient().post(
            reverse("token_obtain_pair"),
            {"username": "jwt_user", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200
        assert "access" in response.data

    def test_token_endpoint_rejects_a_wrong_password(self):
        User.objects.create_user(username="jwt_bad", password=PASSWORD, email="jb@test.com")
        response = APIClient().post(
            reverse("token_obtain_pair"),
            {"username": "jwt_bad", "password": "wrong-password"},
            format="json",
        )
        assert response.status_code == 401


@pytest.mark.django_db
class TestGoogleSsoAccounts:
    """
    SSO users have set_unusable_password(); their stored value is '!'-prefixed
    and is never hashed. The hasher swap must not touch them.
    """

    def test_unusable_password_is_untouched_by_the_migration(self):
        user = User.objects.create(username="sso_user", email="sso@test.com")
        user.set_unusable_password()
        user.save(update_fields=["password"])

        assert user.password.startswith("!")
        assert user.has_usable_password() is False
        assert authenticate(username="sso_user", password="anything") is None

        user.refresh_from_db()
        assert user.password.startswith("!"), "an SSO account must never be rehashed"

    def test_sso_account_cannot_be_logged_into_with_the_placeholder_value(self):
        user = User.objects.create(username="sso_probe", email="sso2@test.com")
        user.set_unusable_password()
        user.save(update_fields=["password"])
        assert authenticate(username="sso_probe", password=user.password) is None


@pytest.mark.django_db
class TestAdminLogin:
    def test_superuser_created_before_the_swap_can_still_sign_in(self):
        admin = User.objects.create_superuser(
            username="legacy_admin", password="ignored", email="a@test.com"
        )
        admin.password = legacy_pbkdf2_hash()
        admin.save(update_fields=["password"])

        authenticated = authenticate(username="legacy_admin", password=PASSWORD)
        assert authenticated is not None
        assert authenticated.is_staff and authenticated.is_superuser

        admin.refresh_from_db()
        assert admin.password.startswith("argon2$")


@pytest.mark.django_db
class TestRollbackSafety:
    def test_reordering_keeps_both_populations_working(self):
        """
        The supported rollback: PBKDF2 first, Argon2 RETAINED. Both a migrated
        and an unmigrated account must still authenticate.
        """
        argon_hash = make_password(PASSWORD)
        legacy = legacy_pbkdf2_hash()

        with override_settings(PASSWORD_HASHERS=[
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
            "common.hashers.TunedArgon2PasswordHasher",
        ]):
            assert check_password(PASSWORD, argon_hash) is True
            assert check_password(PASSWORD, legacy) is True

    def test_removing_argon2_locks_out_migrated_users(self):
        """
        Documents WHY rollback is a reorder and never a removal: Django reports
        this as an ordinary failed login, with nothing in the logs to explain it.
        """
        argon_hash = make_password(PASSWORD)
        with override_settings(PASSWORD_HASHERS=LEGACY_HASHERS):
            assert check_password(PASSWORD, argon_hash) is False
