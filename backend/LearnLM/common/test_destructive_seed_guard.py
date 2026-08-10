"""
Production cannot execute a destructive reseed (M2 P2.5, Phase 4).

`seed_data` ran `Question.objects.all().delete()` with no flag, no
confirmation and no environment check. `CodeSubmission.question` and
`AgenticCoachLog.question` are both `on_delete=CASCADE`, so that single
statement destroys every learner's submission history along with the question
bank. Nothing scheduled it — but the roadmap calls for a DAILY reseed, and a
daily job is how an unguarded destructive command eventually gets aimed at
production.

The guard is deliberately two independent conditions (environment AND explicit
flag) so that no single mistake is sufficient, and it is fail-safe: an unset
or misspelled `SPARKLM_ENV` classifies as production.

The cascade is asserted here with real rows rather than asserted from the
model definition, because the model definition is what everyone reads and
still nobody noticed.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from common import environment
from common.environment import (
    DISPOSABLE_ENVIRONMENTS, current_environment, is_disposable_environment,
    require_disposable_environment,
)
from groups.models import CodeSubmission, CodingPortal, Question, Topic

User = get_user_model()


# ─────────────────────────────────────────────────────────────
# Environment classification — fail safe
# ─────────────────────────────────────────────────────────────

def test_an_unset_environment_is_production(monkeypatch):
    """
    The whole point of the guard. An unconfigured host is exactly the one that
    cannot be vouched for, so it must get the strictest treatment.
    """
    monkeypatch.delenv(environment.ENV_VAR, raising=False)

    assert current_environment() == "production"
    assert is_disposable_environment() is False


@pytest.mark.parametrize(
    "value",
    ["production", "prod", "staging", "develpment", "PRODUCTION ", "", "   ",
     "dev", "local", "sandbox"],
)
def test_anything_not_explicitly_disposable_is_production(monkeypatch, value):
    """
    Allowlist, not denylist. `prod`, `dev` and a misspelt `develpment` are all
    treated as production — a typo lands on the safe side.
    """
    monkeypatch.setenv(environment.ENV_VAR, value)

    assert is_disposable_environment() is False, f"{value!r} was treated as disposable"


@pytest.mark.parametrize("value", sorted(DISPOSABLE_ENVIRONMENTS))
def test_declared_disposable_environments_are_recognised(monkeypatch, value):
    """The positive control: without this the guard could reject everything."""
    monkeypatch.setenv(environment.ENV_VAR, value)

    assert is_disposable_environment() is True


def test_the_guard_is_not_derived_from_debug(monkeypatch, settings):
    """
    DEBUG defaults to true when DJANGO_DEBUG is unset, so deriving the guard
    from it would make an unconfigured host disposable — backwards.
    """
    settings.DEBUG = True
    monkeypatch.delenv(environment.ENV_VAR, raising=False)

    assert is_disposable_environment() is False


# ─────────────────────────────────────────────────────────────
# require_disposable_environment
# ─────────────────────────────────────────────────────────────

def test_production_is_refused_even_when_acknowledged(monkeypatch):
    """
    The flag must not be able to override the environment. This is the
    "someone accidentally supplies the wrong flag" case.
    """
    monkeypatch.setenv(environment.ENV_VAR, "production")

    with pytest.raises(CommandError, match="REFUSED"):
        require_disposable_environment("wipe everything", acknowledged=True)


def test_a_disposable_environment_still_needs_acknowledgement(monkeypatch):
    """
    The environment must not be able to override the flag either. Otherwise a
    scheduled job inheriting SPARKLM_ENV=development destroys a developer's
    database without anyone typing the intent.
    """
    monkeypatch.setenv(environment.ENV_VAR, "development")

    with pytest.raises(CommandError, match="not acknowledged"):
        require_disposable_environment("wipe everything", acknowledged=False)


def test_both_conditions_together_permit_the_operation(monkeypatch):
    """Positive control — the guard must not be unconditionally closed."""
    monkeypatch.setenv(environment.ENV_VAR, "development")

    require_disposable_environment("wipe everything", acknowledged=True)


def test_the_refusal_explains_the_cascade(monkeypatch):
    """
    The operator reading this message is about to go looking for a way around
    it. It has to say why, or they will find one.
    """
    monkeypatch.setenv(environment.ENV_VAR, "production")

    with pytest.raises(CommandError) as exc:
        require_disposable_environment("seed_data", acknowledged=True)

    message = str(exc.value)
    assert "CodeSubmission" in message
    assert environment.ENV_VAR in message


# ─────────────────────────────────────────────────────────────
# The commands themselves
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def learner_history(db):
    """A question with a real submission attached — what the cascade eats."""
    portal = CodingPortal.objects.create(name="Guard Portal")
    topic, _ = Topic.objects.get_or_create(
        name="GuardTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    question = Question.objects.create(
        title="Guarded", content="c", topic=topic, base_difficulty=1200.0,
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={},
    )
    user = User.objects.create_user(
        username="guarded", password="Guard#2026x", email="g@t.com"
    )
    CodeSubmission.objects.create(
        user=user, question=question, language="python",
        code="print(1)", status="accepted",
    )
    return question


def test_seed_data_refuses_in_production(monkeypatch, learner_history):
    monkeypatch.setenv(environment.ENV_VAR, "production")

    with pytest.raises(CommandError, match="REFUSED"):
        call_command("seed_data", "--wipe-all-questions")

    assert Question.objects.count() == 1, "questions were deleted in production"
    assert CodeSubmission.objects.count() == 1, "learner history was deleted"


def test_seed_data_refuses_with_no_environment_set(monkeypatch, learner_history):
    """The realistic accident: a scheduled job that never sets SPARKLM_ENV."""
    monkeypatch.delenv(environment.ENV_VAR, raising=False)

    with pytest.raises(CommandError, match="REFUSED"):
        call_command("seed_data", "--wipe-all-questions")

    assert CodeSubmission.objects.count() == 1


def test_seed_data_refuses_without_the_flag_even_in_development(
    monkeypatch, learner_history
):
    monkeypatch.setenv(environment.ENV_VAR, "development")

    with pytest.raises(CommandError, match="not acknowledged"):
        call_command("seed_data")

    assert Question.objects.count() == 1


def test_cleanup_question_bank_dry_run_needs_no_environment(
    monkeypatch, learner_history
):
    """
    A dry run deletes nothing, so gating it would be noise. It must keep
    working in production — that is how an operator inspects the damage
    before deciding.
    """
    monkeypatch.setenv(environment.ENV_VAR, "production")

    call_command("cleanup_question_bank")

    assert Question.objects.count() == 1


def test_cleanup_question_bank_apply_refuses_in_production(
    monkeypatch, learner_history
):
    monkeypatch.setenv(environment.ENV_VAR, "production")

    with pytest.raises(CommandError, match="REFUSED"):
        call_command("cleanup_question_bank", "--apply")

    assert Question.objects.count() == 1
    assert CodeSubmission.objects.count() == 1


# ─────────────────────────────────────────────────────────────
# The cascade this guard exists to prevent
# ─────────────────────────────────────────────────────────────

def test_deleting_questions_really_does_destroy_learner_history(learner_history):
    """
    Proves the premise with rows rather than by reading the model. If this
    ever stops being true — say `question` becomes SET_NULL — the guard's
    justification changes and this test should be revisited deliberately
    rather than silently.
    """
    assert CodeSubmission.objects.count() == 1

    Question.objects.all().delete()

    assert CodeSubmission.objects.count() == 0, (
        "submissions survived — the CASCADE assumption behind the guard changed"
    )


def test_no_seed_command_deletes_users_or_groups(monkeypatch, learner_history):
    """
    Scope check on the blast radius. Even the permitted destructive path is
    only allowed to touch the question bank; users, groups and their content
    are never in scope for a reseed.
    """
    monkeypatch.setenv(environment.ENV_VAR, "development")
    users_before = User.objects.count()
    assert users_before >= 1

    with pytest.raises(CommandError):
        call_command("seed_data")

    assert User.objects.count() == users_before
