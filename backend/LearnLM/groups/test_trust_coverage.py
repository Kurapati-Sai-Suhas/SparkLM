"""
Trusted-content coverage reporting (M2 P2.32).

Two things are under test. The first is that the report counts trust the way
the rest of the system defines it — a topic is covered only when it holds a
question in the canonical PUBLISHED + ORACLE_VERIFIED state, reached through
the pipeline and not by any shortcut this report could offer.

The second matters more: that the report CANNOT advance anything. Every
uncovered topic is blocked at `reference_create`, which needs an authored
answer key. A reporting tool that could quietly manufacture one would defeat
the architecture it is reporting on.

Local/synthetic database only. The questions below are fixtures, not claims
about production content.
"""

import inspect
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from groups import coding_views as cv
from groups import trust_coverage as tc
from groups.models import (
    CodeSubmission, CodingPortal, Question, RecommendationLog, Topic,
    TopicPrerequisite, UserCodingProfile,
)

User = get_user_model()

DEFAULT_ELO = 1200.0


@pytest.fixture
def portal(db):
    return CodingPortal.objects.create(name="Coverage Portal")


@pytest.fixture
def root_topic(db, portal):
    made, _ = Topic.objects.get_or_create(
        name="CoverageRoot",
        defaults={"structure_type": "flat", "portal": portal})
    return made


@pytest.fixture
def leaf_topic(db, portal, root_topic):
    made, _ = Topic.objects.get_or_create(
        name="CoverageLeaf",
        defaults={"structure_type": "flat", "portal": portal})
    TopicPrerequisite.objects.get_or_create(topic=made,
                                            prerequisite=root_topic)
    return made


@pytest.fixture
def learner(db):
    user = User.objects.create_user(username="cov-learner", password="pw",
                                    email="c@example.com")
    UserCodingProfile.objects.get_or_create(user=user)
    return user


def make_question(topic, question_id, *, difficulty=1300.0, verified=False,
                  servable=True):
    return Question.objects.create(
        id=question_id, title=f"Q{question_id}",
        content=("Statement." if servable
                 else Question.PLACEHOLDER_MARKER + " placeholder"),
        topic=topic, base_difficulty=difficulty,
        status=(Question.STATUS_PUBLISHED if verified
                else Question.STATUS_DRAFT),
        trust_state=(Question.TRUST_ORACLE_VERIFIED if verified
                     else Question.TRUST_UNVERIFIED),
        boilerplate_code={"python": "def f(): pass\n"},
        hidden_test_cases=([{"stdin": "1", "expected_output": "1"}]
                           if servable else []),
        hidden_wrapper_code={}, execution_contract_version="v1")


def coverage_for(name, **kwargs):
    return next((c for c in tc.collect(**kwargs) if c.name == name), None)


# ═════════════════════════════════════════════════════════════
# A/B — trust is counted only in the canonical state
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_topic_counts_as_covered_only_when_a_question_is_fully_trusted(
        root_topic):
    make_question(root_topic, 9000, verified=True)

    assert coverage_for(root_topic.name) is None          # excluded: covered
    assert coverage_for(root_topic.name, include_covered=True).trusted == 1


@pytest.mark.django_db
def test_published_but_unverified_does_not_count_as_covered(root_topic):
    """
    Every legacy question in the bank is PUBLISHED + UNVERIFIED. Counting
    `status` alone would report the whole bank as trusted.
    """
    question = make_question(root_topic, 9010)
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_UNVERIFIED)

    entry = coverage_for(root_topic.name)

    assert entry is not None and entry.trusted == 0
    assert entry.blocker == tc.BLOCKING_ARTIFACT


@pytest.mark.django_db
def test_trust_state_alone_does_not_count_either(root_topic):
    """
    BLOCKED + ORACLE_VERIFIED is legal — a trustworthy key withdrawn for an
    unrelated reason — and is not adaptive-eligible.
    """
    question = make_question(root_topic, 9020)
    Question.objects.filter(pk=question.pk).update(
        status="BLOCKED", trust_state=Question.TRUST_ORACLE_VERIFIED)

    assert coverage_for(root_topic.name).trusted == 0


@pytest.mark.django_db
def test_coverage_uses_the_canonical_predicate(root_topic):
    """
    Not a restated status/trust pair. The report must move if the definition
    of adaptive eligibility ever does.
    """
    source = inspect.getsource(tc)
    assert "adaptive_eligible_q" in source
    assert 'status="PUBLISHED"' not in source
    assert "TRUST_ORACLE_VERIFIED" not in source


# ═════════════════════════════════════════════════════════════
# C/I — the report advances nothing
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_running_the_report_mutates_nothing(learner, root_topic):
    make_question(root_topic, 9030)
    make_question(root_topic, 9031, verified=True)

    def snapshot():
        return {
            "questions": list(Question.objects.order_by("pk").values()),
            "submissions": list(CodeSubmission.objects.order_by("pk").values()),
            "decisions": list(RecommendationLog.objects.order_by("pk").values()),
            "profiles": list(UserCodingProfile.objects.order_by("pk").values()),
        }

    before = snapshot()
    call_command("trust_coverage", "--all")
    assert snapshot() == before


def test_the_report_contains_no_write_verb():
    from groups.management.commands import trust_coverage as cmd

    source = inspect.getsource(tc) + inspect.getsource(cmd)
    for verb in (".save(", ".create(", ".update(", ".delete(",
                 ".get_or_create(", ".bulk_create(", "atomic("):
        assert verb not in source, verb


def test_the_report_offers_no_flag_that_could_write():
    import ast
    import textwrap

    from groups.management.commands import trust_coverage as cmd

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(cmd.Command.add_arguments)))
    flags = {node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant)
             and isinstance(node.value, str) and node.value.startswith("--")}
    assert flags == {"--all", "--json", "--pipeline", "--candidates"}


def test_the_report_never_touches_a_trust_writing_command():
    """
    It names the pipeline steps as data for printing. It must not import or
    call them — a report that could promote is not a report.
    """
    source = inspect.getsource(tc)
    for forbidden in ("call_command", "question_promote(", "question_approve(",
                      "reference_create(", "oracle_execute("):
        assert forbidden not in source, forbidden


def test_the_pipeline_records_which_steps_are_human():
    """
    The three irreducibly human steps must stay named as such, so a later
    edit cannot quietly reclassify one as automatable.
    """
    authorities = {step: authority
                   for step, authority, _ in tc.TRUST_PIPELINE}

    assert authorities["reference_create"] == "OPERATOR"
    assert authorities["reference_review"] == "HUMAN REVIEW"
    assert authorities["question_approve"] == "HUMAN REVIEW"
    assert authorities["oracle_execute"] == "AUTOMATED"


def test_reference_create_still_requires_an_authored_file():
    """
    The blocker this whole milestone rests on. If `reference_create` ever
    grew a way to synthesise a solution, the worklist would be wrong and the
    trust boundary would have moved.
    """
    from groups.management.commands import reference_create

    source = inspect.getsource(reference_create)
    assert "--source-file" in source
    assert '"--source"' not in source


# ═════════════════════════════════════════════════════════════
# The blocker classification
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_topic_with_no_servable_question_reports_the_harder_blocker(
        root_topic):
    """
    Bit Manipulation and Sorting are in this state in production: questions
    exist but none is servable, so no reference can be authored yet. Reporting
    that as "needs a reference solution" would send an operator to write one
    for a question that cannot be served.
    """
    make_question(root_topic, 9040, servable=False)

    entry = coverage_for(root_topic.name)

    assert entry.servable == 0
    assert "content repair" in entry.blocker
    assert entry.candidates == []      # nothing to shortlist


@pytest.mark.django_db
def test_a_topic_with_servable_questions_reports_the_reference_blocker(
        root_topic):
    make_question(root_topic, 9050)

    entry = coverage_for(root_topic.name)

    assert entry.blocker == tc.BLOCKING_ARTIFACT
    assert [c["id"] for c in entry.candidates] == [9050]


@pytest.mark.django_db
def test_the_summary_separates_the_two_blocker_classes(root_topic, leaf_topic):
    make_question(root_topic, 9060, servable=False)
    make_question(leaf_topic, 9061, servable=True)

    summary = tc.summarise(tc.collect())

    assert summary["blocked_on_content_repair"] >= 1
    assert summary["blocked_on_reference_authoring"] >= 1
    assert root_topic.name in summary["content_repair_topics"]


# ═════════════════════════════════════════════════════════════
# Candidate ranking
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_reachable_candidate_outranks_an_unreachable_one(root_topic,
                                                           learner):
    """
    A verified question the exposure policy would never prefer is verified
    content nobody is offered. The shortlist says so rather than ranking by
    id and leaving the operator to discover it.
    """
    make_question(root_topic, 9070, difficulty=1600.0)   # |400| out of band
    make_question(root_topic, 9071, difficulty=1300.0)   # |100| in band

    candidates = coverage_for(root_topic.name).candidates

    assert candidates[0]["id"] == 9071
    assert candidates[0]["reachable"] is True
    assert candidates[1]["reachable"] is False


@pytest.mark.django_db
def test_a_candidate_with_more_peers_outranks_a_lonelier_one(root_topic,
                                                             learner):
    make_question(root_topic, 9080, difficulty=1000.0)
    for offset in range(3):
        make_question(root_topic, 9081 + offset, difficulty=1300.0)

    assert coverage_for(root_topic.name).candidates[0]["id"] == 9081


@pytest.mark.django_db
def test_the_shortlist_is_deterministic(root_topic, learner):
    for offset in range(4):
        make_question(root_topic, 9090 + offset, difficulty=1300.0)

    runs = {tuple(c["id"] for c in coverage_for(root_topic.name).candidates)
            for _ in range(3)}

    assert len(runs) == 1


@pytest.mark.django_db
def test_the_band_rule_is_imported_not_restated(root_topic):
    """One definition of 'reachable', shared with the code that serves."""
    source = inspect.getsource(tc)
    assert "EXPOSURE_ELO_BAND" in source
    assert "300" not in source.replace("P2.30", "").replace("P2.31", "")


@pytest.mark.django_db
def test_the_median_elo_is_read_from_live_profiles_not_assumed(db):
    User.objects.create_user(username="a", password="p", email="a@x.com")
    User.objects.create_user(username="b", password="p", email="b@x.com")
    for index, user in enumerate(User.objects.all()):
        UserCodingProfile.objects.update_or_create(
            user=user, defaults={"elo_rating": 800.0 + index * 400})

    assert tc.median_learner_elo() == 1000.0


# ═════════════════════════════════════════════════════════════
# D/E — exposure still reaches trusted content
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_newly_trusted_question_becomes_reachable_in_its_topic(
        learner, root_topic):
    """
    Part 5, end to end on the real selector: a topic that was uncovered
    becomes covered, and the exposure policy immediately serves the trusted
    question over the unverified peers it competes with.
    """
    make_question(root_topic, 9100, difficulty=1300.0)
    make_question(root_topic, 9101, difficulty=1300.0)

    assert coverage_for(root_topic.name).trusted == 0
    assert cv._select_question(learner, root_topic.name,
                               DEFAULT_ELO).pk == 9100

    promoted = make_question(root_topic, 9102, difficulty=1000.0,
                             verified=True)

    assert coverage_for(root_topic.name) is None          # now covered
    assert cv._select_question(learner, root_topic.name,
                               DEFAULT_ELO) == promoted


@pytest.mark.django_db
def test_a_trusted_question_survives_the_repeat_solve_guard(
        learner, root_topic):
    """Covering a topic is not the same as covering it forever."""
    trusted = make_question(root_topic, 9110, difficulty=1300.0,
                            verified=True)
    fallback = make_question(root_topic, 9111, difficulty=1300.0)
    CodeSubmission.objects.create(
        user=learner, question=trusted, language="python", code="x",
        status="accepted", adaptive_eligible=True)

    assert cv._select_question(learner, root_topic.name,
                               DEFAULT_ELO) == fallback


@pytest.mark.django_db
def test_an_out_of_band_trusted_question_is_reported_as_such(root_topic):
    """
    Production has exactly this case: Trie's only servable question sits at
    1600, so authoring a reference for it would produce trusted content the
    median learner is never offered. The report must not hide that.
    """
    make_question(root_topic, 9120, difficulty=1600.0)

    candidate = coverage_for(root_topic.name).candidates[0]

    assert candidate["reachable"] is False


# ═════════════════════════════════════════════════════════════
# F/G — no reclassification; readiness agrees
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_no_historical_row_is_reclassified_by_promoting_a_question(
        learner, root_topic):
    """
    The invariant carried from P2.30/P2.31. Covering a topic must not convert
    a past impression or a past submission into trusted evidence.
    """
    from groups import routing_readiness as rr

    question = make_question(root_topic, 9130, difficulty=1300.0)
    CodeSubmission.objects.create(
        user=learner, question=question, language="python", code="x",
        status="accepted", adaptive_eligible=False)
    RecommendationLog.objects.create(
        user=learner, recommended_topic=root_topic, engine_used="flat",
        problem_id=str(question.pk), actual_result_correct=True,
        served_adaptive_eligible=False)

    before = rr.collect_census()
    Question.objects.filter(pk=question.pk).update(
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED)
    after = rr.collect_census()

    assert before.decisions_trustworthy == after.decisions_trustworthy == 0
    assert before.adaptive_eligible_submissions == 0
    assert after.adaptive_eligible_submissions == 0        # frozen at write
    assert after.trusted_exposures == 0                    # frozen at exposure


@pytest.mark.django_db
def test_readiness_counts_a_newly_trusted_question(root_topic):
    from groups import routing_readiness as rr

    assert rr.collect_census().oracle_verified_questions == 0
    make_question(root_topic, 9140, verified=True)
    assert rr.collect_census().oracle_verified_questions == 1


# ═════════════════════════════════════════════════════════════
# H — output carries no grading truth
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_no_hidden_test_data_reaches_the_output(root_topic):
    from io import StringIO

    question = make_question(root_topic, 9150)
    question.hidden_test_cases = [
        {"stdin": "SENTINEL-IN", "expected_output": "SENTINEL-OUT"}]
    question.save(update_fields=["hidden_test_cases"])

    buffer = StringIO()
    call_command("trust_coverage", "--all", "--pipeline", stdout=buffer)

    assert "SENTINEL" not in buffer.getvalue()


@pytest.mark.django_db
def test_json_output_is_machine_readable(root_topic):
    from io import StringIO

    make_question(root_topic, 9160, difficulty=1300.0)
    buffer = StringIO()
    call_command("trust_coverage", "--json", stdout=buffer)

    payload = json.loads(buffer.getvalue())

    assert payload["summary"]["blocked_on_reference_authoring"] >= 1
    assert len(payload["pipeline"]) == len(tc.TRUST_PIPELINE)
    entry = next(t for t in payload["topics"] if t["name"] == root_topic.name)
    assert entry["blocker"] == tc.BLOCKING_ARTIFACT


@pytest.mark.django_db
def test_the_report_states_it_advanced_nothing(root_topic):
    from io import StringIO

    make_question(root_topic, 9170)
    buffer = StringIO()
    call_command("trust_coverage", stdout=buffer)
    text = buffer.getvalue()

    assert "read-only" in text
    assert "advances nothing" in text
    assert text.encode("utf-8").decode("utf-8") == text     # cp1252-safe
