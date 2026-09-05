"""
Routing-evaluation data readiness (M2 P2.30).

The property under test is that the report tells the truth about whether the
Traffic Cop can be evaluated — including, and especially, when the truth is
"no". A readiness report that cannot distinguish a trustworthy label from a
contaminated one is worse than no report, because it launders the second into
the first.

So the tests below are mostly about what the report REFUSES to count.

Local/synthetic database only.
"""

import ast
import inspect
import json
import textwrap
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from groups import routing_readiness as rr
from groups.management.commands import retrain_ai
from groups.management.commands import routing_data_readiness as cmd
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
    UserCodingProfile,
)

User = get_user_model()


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Readiness Portal")


@pytest.fixture
def topic(db, portal):
    made, _ = Topic.objects.get_or_create(
        name="ReadinessTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def learner(db):
    user = User.objects.create_user(username="rr-learner", password="pw",
                                    email="l@example.com")
    UserCodingProfile.objects.get_or_create(user=user)
    return user


def make_question(topic, question_id, *, verified):
    """A question that is either fully trusted or fully untrusted."""
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}", content="Statement.",
        topic=topic, base_difficulty=1200.0,
        status=(Question.STATUS_PUBLISHED if verified
                else Question.STATUS_DRAFT),
        trust_state=(Question.TRUST_ORACLE_VERIFIED if verified
                     else Question.TRUST_UNVERIFIED),
        boilerplate_code={"python": "def f(): pass\n"},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


def make_decision(user, topic, question, *, when, outcome=None,
                  engine="hierarchical", policy="v2-exposure-aware"):
    log = RecommendationLog.objects.create(
        user=user, recommended_topic=topic, engine_used=engine,
        problem_id=str(question.id) if question else None,
        actual_result_correct=outcome, policy_version=policy)
    # created_at is auto_now_add; set it explicitly so ordering is controlled.
    RecommendationLog.objects.filter(pk=log.pk).update(created_at=when)
    log.refresh_from_db()
    return log


def make_submission(user, question, *, when, eligible, status="accepted"):
    sub = CodeSubmission.objects.create(
        user=user, question=question, language="python", code="x",
        status=status, adaptive_eligible=eligible)
    CodeSubmission.objects.filter(pk=sub.pk).update(submitted_at=when)
    sub.refresh_from_db()
    return sub


# ═════════════════════════════════════════════════════════════
# A — an unverified interaction does not enter routing telemetry
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_unverified_submission_produces_no_trustworthy_label(
        learner, topic):
    """
    The central property. A learner solved something, a decision was closed,
    and none of it counts — because the question's answer key has never been
    checked by an oracle.
    """
    question = make_question(topic, 7100, verified=False)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=True)
    make_submission(learner, question, when=now - timedelta(hours=1),
                    eligible=False)

    census = rr.collect_census()

    assert census.decisions_closed == 1
    assert census.decisions_trustworthy == 0
    assert census.decisions_contaminated == 1


@pytest.mark.django_db
def test_an_unverified_interaction_is_absent_from_router_telemetry(
        learner, topic):
    """
    Belt and braces: the readiness report and the router must agree about
    what counts. If they disagreed, the report would be describing a dataset
    the router never sees.
    """
    from groups.hybrid_router import compute_routing_telemetry

    question = make_question(topic, 7101, verified=False)
    make_submission(learner, question, when=timezone.now(), eligible=False)

    avg_acc, runs_z, sample_size = compute_routing_telemetry(learner)

    assert sample_size == 0                       # cold start, not "one solve"
    assert (avg_acc, runs_z) == (0.7, 0.0)
    assert rr.collect_census().adaptive_eligible_submissions == 0


# ═════════════════════════════════════════════════════════════
# B — a verified, adaptive-eligible interaction DOES enter
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_verified_interaction_produces_a_trustworthy_label(learner, topic):
    question = make_question(topic, 7110, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=True)
    make_submission(learner, question, when=now - timedelta(hours=1),
                    eligible=True)

    census = rr.collect_census()

    assert census.decisions_trustworthy == 1
    assert census.decisions_contaminated == 0
    assert census.adaptive_eligible_submissions == 1
    assert census.learners_with_adaptive_interactions == 1


@pytest.mark.django_db
def test_the_report_counts_verified_questions_and_the_trusted_share(
        learner, topic):
    make_question(topic, 7111, verified=True)
    make_question(topic, 7112, verified=False)

    census = rr.collect_census()

    assert census.oracle_verified_questions == 1
    assert census.questions_total == 2
    # Both are servable (real content, non-empty tests), only one is trusted.
    assert census.servable_questions == 2
    assert census.trusted_share_of_servable == pytest.approx(0.5)


# ═════════════════════════════════════════════════════════════
# C — outcome is associated with the eligible interaction
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_outcome_balance_is_measured_over_trustworthy_pairs_only(
        learner, topic):
    """
    Balance of a contaminated set describes a dataset that must not be used.
    Reporting it would invite someone to read it as a property of the
    training data.
    """
    trusted = make_question(topic, 7120, verified=True)
    untrusted = make_question(topic, 7121, verified=False)
    now = timezone.now()

    make_decision(learner, topic, trusted, when=now - timedelta(hours=4),
                  outcome=True)
    make_submission(learner, trusted, when=now - timedelta(hours=3),
                    eligible=True)
    # Three contaminated negatives that would swamp the balance if counted.
    for offset in (2, 1, 0):
        make_decision(learner, topic, untrusted,
                      when=now - timedelta(hours=offset), outcome=False)

    census = rr.collect_census()

    assert census.label_positive == 1
    assert census.label_negative == 0
    assert census.decisions_contaminated == 3


@pytest.mark.django_db
def test_a_false_outcome_is_recorded_as_a_negative_not_dropped(
        learner, topic):
    question = make_question(topic, 7122, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=False)
    make_submission(learner, question, when=now - timedelta(hours=1),
                    eligible=True, status="wrong_answer")

    census = rr.collect_census()

    assert census.decisions_trustworthy == 1
    assert census.label_negative == 1


# ═════════════════════════════════════════════════════════════
# D — decision and outcome can be joined
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_submission_before_the_decision_cannot_close_it(learner, topic):
    """
    Causality. A solve that happened BEFORE the recommendation is not
    evidence about that recommendation, and treating it as such would
    manufacture pairs out of ordering alone.
    """
    question = make_question(topic, 7130, verified=True)
    now = timezone.now()
    make_submission(learner, question, when=now - timedelta(hours=3),
                    eligible=True)
    make_decision(learner, topic, question, when=now - timedelta(hours=1),
                  outcome=True)

    assert rr.collect_census().decisions_trustworthy == 0


@pytest.mark.django_db
def test_another_learners_submission_cannot_close_this_decision(
        learner, topic, db):
    other = User.objects.create_user(username="rr-other", password="pw",
                                     email="o@example.com")
    question = make_question(topic, 7131, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=True)
    make_submission(other, question, when=now - timedelta(hours=1),
                    eligible=True)

    assert rr.collect_census().decisions_trustworthy == 0


@pytest.mark.django_db
def test_a_submission_to_a_different_problem_cannot_close_this_decision(
        learner, topic):
    asked = make_question(topic, 7132, verified=True)
    solved = make_question(topic, 7133, verified=True)
    now = timezone.now()
    make_decision(learner, topic, asked, when=now - timedelta(hours=2),
                  outcome=True)
    make_submission(learner, solved, when=now - timedelta(hours=1),
                    eligible=True)

    assert rr.collect_census().decisions_trustworthy == 0


# ═════════════════════════════════════════════════════════════
# E — cold-start learners stay correctly identified
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_decision_with_no_prior_eligible_history_is_cold_start(
        learner, topic):
    question = make_question(topic, 7140, verified=True)
    make_decision(learner, topic, question, when=timezone.now(), outcome=None)

    assert rr.collect_census().cold_start_decisions == 1


@pytest.mark.django_db
def test_cold_start_is_evaluated_as_of_the_decision_not_now(learner, topic):
    """
    A learner who is experienced TODAY was still cold when they were routed.
    Counting by current state would erase every cold-start row the moment the
    learner's second solve landed.
    """
    question = make_question(topic, 7141, verified=True)
    now = timezone.now()

    cold = make_decision(learner, topic, question,
                         when=now - timedelta(hours=3), outcome=None)
    make_submission(learner, question, when=now - timedelta(hours=2),
                    eligible=True)
    warm = make_decision(learner, topic, question,
                         when=now - timedelta(hours=1), outcome=None)

    census = rr.collect_census()

    assert cold.created_at < warm.created_at
    assert census.decisions_total == 2
    assert census.cold_start_decisions == 1        # only the first


@pytest.mark.django_db
def test_an_ineligible_prior_solve_does_not_warm_a_learner(learner, topic):
    """Cold start mirrors the router: only adaptive-eligible history counts."""
    question = make_question(topic, 7142, verified=False)
    now = timezone.now()
    make_submission(learner, question, when=now - timedelta(hours=2),
                    eligible=False)
    make_decision(learner, topic, question, when=now - timedelta(hours=1),
                  outcome=None)

    assert rr.collect_census().cold_start_decisions == 1


# ═════════════════════════════════════════════════════════════
# F — duplicates do not create false pairs
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_repeated_submissions_do_not_multiply_a_single_decision(
        learner, topic):
    """
    One decision is one pair however many times the learner resubmits.
    Counting per submission would inflate the dataset with re-solves.
    """
    question = make_question(topic, 7150, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=5),
                  outcome=True)
    for offset in (4, 3, 2, 1):
        make_submission(learner, question, when=now - timedelta(hours=offset),
                        eligible=True)

    census = rr.collect_census()

    assert census.adaptive_eligible_submissions == 4
    assert census.decisions_trustworthy == 1       # not 4


@pytest.mark.django_db
def test_two_decisions_for_the_same_problem_stay_two_pairs(learner, topic):
    """The converse: genuine repeat recommendations are genuinely two rows."""
    question = make_question(topic, 7151, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=4),
                  outcome=True)
    make_submission(learner, question, when=now - timedelta(hours=3),
                    eligible=True)
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=False)
    make_submission(learner, question, when=now - timedelta(hours=1),
                    eligible=True)

    assert rr.collect_census().decisions_trustworthy == 2


# ═════════════════════════════════════════════════════════════
# G — invalid records do not corrupt the report
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_decision_with_no_problem_id_is_counted_not_crashed(
        learner, topic):
    make_decision(learner, topic, None, when=timezone.now(), outcome=True)

    census = rr.collect_census()

    assert census.decisions_missing_problem_id == 1
    assert census.decisions_trustworthy == 0


@pytest.mark.django_db
def test_a_problem_id_naming_no_question_is_reported(learner, topic):
    log = make_decision(learner, topic, None, when=timezone.now(),
                        outcome=True)
    RecommendationLog.objects.filter(pk=log.pk).update(problem_id="999999")

    census = rr.collect_census()

    assert census.decisions_with_unresolvable_problem == 1
    assert census.decisions_trustworthy == 0


@pytest.mark.django_db
def test_a_non_numeric_problem_id_does_not_raise(learner, topic):
    """`problem_id` is a CharField with no foreign key. It can hold anything."""
    log = make_decision(learner, topic, None, when=timezone.now(),
                        outcome=True)
    RecommendationLog.objects.filter(pk=log.pk).update(problem_id="not-an-id")

    census = rr.collect_census()                   # must not raise

    assert census.decisions_trustworthy == 0
    assert census.decisions_with_unresolvable_problem == 1


@pytest.mark.django_db
def test_an_empty_database_produces_a_report_rather_than_an_error(db):
    census = rr.collect_census()
    gate = rr.evaluate_gate(census)

    assert census.decisions_total == 0
    assert gate.verdict == rr.NOT_READY
    assert census.minority_outcome_rate == 0.0     # not a ZeroDivisionError


# ═════════════════════════════════════════════════════════════
# H — the report is read-only
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_running_the_report_mutates_nothing(learner, topic):
    """
    Behavioural, not structural: a full snapshot of every table the report
    touches, compared after the command runs.
    """
    question = make_question(topic, 7160, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  outcome=True)
    make_submission(learner, question, when=now - timedelta(hours=1),
                    eligible=True)

    def snapshot():
        return {
            "questions": list(Question.objects.order_by("pk").values()),
            "submissions": list(CodeSubmission.objects.order_by("pk").values()),
            "decisions": list(RecommendationLog.objects.order_by("pk").values()),
            "profiles": list(UserCodingProfile.objects.order_by("pk").values()),
        }

    before = snapshot()
    call_command("routing_data_readiness")
    assert snapshot() == before


def test_the_command_has_no_write_verb_in_its_source():
    """
    Structural, so a later edit cannot quietly add one. The behavioural test
    above only proves the CURRENT code path writes nothing.
    """
    source = inspect.getsource(cmd) + inspect.getsource(rr)
    for verb in (".save(", ".create(", ".update(", ".delete(",
                 ".get_or_create(", ".bulk_create(", "atomic("):
        assert verb not in source, verb


def test_the_command_offers_no_flag_that_could_write():
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(cmd.Command.add_arguments)))
    flags = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant)
             and isinstance(node.value, str) and node.value.startswith("--")]
    assert set(flags) == {"--json", "--contract"}


# ═════════════════════════════════════════════════════════════
# I — policy version is retained
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_policy_versions_are_tallied_including_the_unversioned_ones(
        learner, topic):
    """
    A null policy_version means "before policy versioning", not "unknown".
    Dropping those rows from the tally would hide 181 production rows.
    """
    question = make_question(topic, 7170, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=3),
                  policy="v2-exposure-aware")
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  policy="v1-runs-test-elo-band")
    make_decision(learner, topic, question, when=now - timedelta(hours=1),
                  policy=None)

    census = rr.collect_census()

    assert census.policy_versions["v2-exposure-aware"] == 1
    assert census.policy_versions["v1-runs-test-elo-band"] == 1
    assert census.policy_versions["(none)"] == 1
    assert census.decisions_without_policy_version == 1


@pytest.mark.django_db
def test_route_counts_distinguish_flat_from_hierarchical(learner, topic):
    question = make_question(topic, 7171, verified=True)
    now = timezone.now()
    make_decision(learner, topic, question, when=now - timedelta(hours=2),
                  engine="hierarchical")
    make_decision(learner, topic, question, when=now - timedelta(hours=1),
                  engine="flat")

    census = rr.collect_census()

    assert census.hierarchical == 1
    assert census.flat == 1


# ═════════════════════════════════════════════════════════════
# J — Traffic Cop behaviour is unchanged
# ═════════════════════════════════════════════════════════════

def test_the_readiness_module_never_imports_the_router_decision_path():
    """
    A report that could influence routing would stop being a report. It reads
    models; it does not touch `decide_route`, `predict_route`, or the policy.
    """
    source = inspect.getsource(rr)
    for forbidden in ("decide_route", "predict_route", "RoutingClassifier",
                      "log_routing_decision", "ROUTING_POLICY_VERSION"):
        assert forbidden not in source, forbidden


@pytest.mark.django_db
def test_running_the_report_does_not_change_a_routing_decision(
        learner, topic):
    from groups.hybrid_router import compute_routing_telemetry

    question = make_question(topic, 7180, verified=True)
    make_submission(learner, question, when=timezone.now(), eligible=True)

    before = compute_routing_telemetry(learner)
    call_command("routing_data_readiness")
    assert compute_routing_telemetry(learner) == before


# ═════════════════════════════════════════════════════════════
# The threshold has exactly one definition
# ═════════════════════════════════════════════════════════════

def test_the_threshold_is_the_one_retrain_ai_enforces():
    """
    Not a restated 100. If someone changes the training gate, this report
    follows automatically rather than reporting READY against a stale number.
    """
    assert rr.rf_min_labels() == retrain_ai.MIN_TRAINING_LABELS


def test_retrain_ai_enforces_the_named_constant_not_a_literal():
    source = inspect.getsource(retrain_ai.Command.handle)
    assert "MIN_TRAINING_LABELS" in source
    assert "< 100" not in source


@pytest.mark.django_db
def test_the_gate_applies_the_threshold_to_trustworthy_not_closed_labels(
        learner, topic):
    """
    The whole point. A pile of closed-but-contaminated labels must not read
    as readiness, however large it grows.
    """
    untrusted = make_question(topic, 7190, verified=False)
    now = timezone.now()
    for offset in range(150):
        make_decision(learner, topic, untrusted,
                      when=now - timedelta(minutes=offset + 1),
                      outcome=(offset % 2 == 0))

    census = rr.collect_census()
    gate = rr.evaluate_gate(census)

    assert census.decisions_closed == 150          # over the threshold
    assert census.decisions_trustworthy == 0
    assert gate.verdict == rr.NOT_READY


# ═════════════════════════════════════════════════════════════
# Output
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_json_output_is_machine_readable(learner, topic):
    from io import StringIO

    make_question(topic, 7200, verified=True)
    buffer = StringIO()
    call_command("routing_data_readiness", "--json", stdout=buffer)

    payload = json.loads(buffer.getvalue())

    assert payload["gate"]["verdict"] == rr.NOT_READY
    assert payload["gate"]["threshold"] == retrain_ai.MIN_TRAINING_LABELS
    assert payload["census"]["oracle_verified_questions"] == 1
    assert len(payload["label_contract"]) == len(rr.LABEL_CONTRACT)


@pytest.mark.django_db
def test_the_report_states_that_it_wrote_nothing(learner, topic):
    from io import StringIO

    buffer = StringIO()
    call_command("routing_data_readiness", stdout=buffer)
    text = buffer.getvalue()

    assert "read-only" in text
    assert "Nothing was written" in text
    assert text.encode("utf-8").decode("utf-8") == text     # cp1252-safe


@pytest.mark.django_db
def test_no_hidden_grading_truth_reaches_the_output(learner, topic):
    """
    The report reads questions. It must never print what is inside them.
    """
    from io import StringIO

    question = make_question(topic, 7210, verified=True)
    question.hidden_test_cases = [
        {"stdin": "SENTINEL-INPUT", "expected_output": "SENTINEL-ANSWER"}]
    question.save(update_fields=["hidden_test_cases"])

    buffer = StringIO()
    call_command("routing_data_readiness", "--contract", stdout=buffer)

    assert "SENTINEL" not in buffer.getvalue()
