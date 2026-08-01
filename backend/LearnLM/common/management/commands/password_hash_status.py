"""
M3 Phase C — report progress of the PBKDF2 → Argon2id migration.

Existing accounts migrate transparently on their next successful login, so
the migration has no completion event: it finishes when the last legacy
account happens to sign in. Operators need a way to ask "is the window
closed yet?" without hand-writing a query.

docs/DEPLOYMENT.md previously answered that with a seven-line shell snippet
to paste into `manage.py shell`. That snippet counted `u.password.split('$')[0]`
across every user, which quietly mislabels unusable-password (Google SSO)
accounts and cannot see a hash no configured hasher recognises — the exact
condition that means someone dropped a hasher from PASSWORD_HASHERS and
locked people out.
"""

from collections import Counter

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.core.management.base import BaseCommand

# Hashes that no configured hasher can read. This is not a curiosity: it is
# the signature of a PASSWORD_HASHERS removal, and every account in this
# bucket is locked out right now.
UNREADABLE = "UNREADABLE"
# Google SSO accounts. set_unusable_password() stores a '!' sentinel, which
# is not a hash and will never migrate — it must stay out of the denominator
# or the migration can never read as complete.
UNUSABLE = "unusable (SSO)"


def classify(user):
    password = user.password or ""
    if not password or password.startswith("!"):
        return UNUSABLE
    try:
        return identify_hasher(password).algorithm
    except Exception:       # noqa: BLE001 - any failure means "cannot verify"
        return UNREADABLE


class Command(BaseCommand):
    help = "Report PBKDF2 -> Argon2id migration progress (M3)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-if-incomplete",
            action="store_true",
            help=(
                "Exit non-zero while any account still holds a legacy hash. "
                "For a scheduled check that should page someone, not for CI."
            ),
        )

    def handle(self, *args, **options):
        User = get_user_model()
        counts = Counter()
        unreadable_names = []

        # .only() keeps this cheap on a cold free-tier instance.
        for user in User.objects.only("password", "username", "is_active").iterator():
            bucket = classify(user)
            counts[bucket] += 1
            if bucket == UNREADABLE:
                unreadable_names.append(user.username)

        total = sum(counts.values())
        argon2 = counts.get("argon2", 0)
        migratable = total - counts.get(UNUSABLE, 0) - counts.get(UNREADABLE, 0)
        legacy = migratable - argon2

        self.stdout.write(f"Accounts: {total}")
        for algorithm, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {algorithm:<16} {n}")

        if migratable:
            pct = argon2 / migratable * 100
            self.stdout.write("")
            self.stdout.write(
                f"Migrated: {argon2}/{migratable} ({pct:.0f}%) — {legacy} awaiting "
                f"first sign-in since the M3 deploy."
            )
        else:
            self.stdout.write("")
            self.stdout.write("No password-bearing accounts to migrate.")

        # An account can be migrated without ever signing in successfully:
        # ModelBackend runs check_password (which carries the rehash setter)
        # BEFORE the is_active check. Pinned by
        # test_disabled_account_is_still_rehashed_on_a_correct_password.
        inactive = User.objects.filter(is_active=False).count()
        if inactive:
            self.stdout.write(
                f"Note: {inactive} account(s) are disabled. A disabled account "
                f"still rehashes on a correct password, so 'migrated' does not "
                f"mean 'signed in'."
            )

        if counts.get(UNREADABLE):
            self.stderr.write(self.style.ERROR(
                f"\n{counts[UNREADABLE]} account(s) have a hash NO configured "
                f"hasher can read: {', '.join(unreadable_names[:10])}"
                f"{' ...' if len(unreadable_names) > 10 else ''}\n"
                f"Those users are locked out and Django reports it as an "
                f"ordinary failed login. Something was removed from "
                f"PASSWORD_HASHERS — see docs/DEPLOYMENT.md "
                f"('rolling back is a REORDER, never a REMOVAL')."
            ))
            raise SystemExit(2)

        if legacy == 0 and migratable:
            self.stdout.write(self.style.SUCCESS(
                "\nMigration window CLOSED — no legacy hashes remain."
            ))
        elif options["fail_if_incomplete"]:
            raise SystemExit(1)
