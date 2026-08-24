"""
Offline content generation for reseed candidates (M2 P2.7h-19).

Local/synthetic database only, deterministic provider only. No network call
is made from any test here, and no test writes to the database — which is
also the property under test.

The boundary these hold: the generator produces exactly two files and a
manifest, proves them well-formed and self-consistent before calling them
ready, and can neither write a question nor invent an answer key.
"""

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from groups import pre_image, reseed_generation as gen
from groups import reseed_specification as spec_mod
from groups.models import (
    CodingPortal, Question, RemediationAction, RemediationBatch, ReseedLedger,
    Topic,
)

VARIADIC = ("class Solution:\n"
            "    def widgetCount(self, *args, **kwargs):\n"
            "        # TODO: Implement your solution for Widget Count\n"
            "        pass\n")

SPECIFICATION = {
    "question_id": 9880,
    "canonical_identity": {"source": "operator", "problem_number": "0"},
    "objective": "Count how many widgets in a list match a target value.",
    "required_operation": "Walk the list and count each element equal to the "
                          "target value, then return that count.",
    "input_semantics": "Two parameters: nums, a list of integers, and target, "
                       "the integer being matched against.",
    "output_semantics": "A single integer, the number of matching elements.",
    "constraints": "The list holds between 1 and 100 integers, each between "
                   "1 and 1000 inclusive.",
    "edge_cases": "A target absent from the list gives zero. Repeated values "
                  "are each counted.",
    "load_bearing": "Every element is examined and equality is exact.",
    "method_behaviour": "The method accepts the list and the target and "
                        "returns one integer. It does not print.",
    "provenance": "OPERATOR_SUPPLIED",
    "author": "test fixture",
    "written_at": "2026-08-24T00:00:00+00:00",
}

GOOD_STATEMENT = (
    "<p>Count how many widgets in <code>nums</code> are equal to the "
    "<code>target</code> value, and return that count.</p>"
    "<p>Walk the list and count each element equal to the target. Every "
    "element is examined and equality is exact. A target absent from the "
    "list gives zero; repeated values are each counted. The list holds "
    "between 1 and 100 integers, each between 1 and 1000 inclusive. The "
    "method takes two parameters, returns one integer and does "
    "not print.</p>"
    "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
    "<h3>Example</h3><p>Input: nums = [1,2], target = 2. Output: 1.</p>")
GOOD_STARTER = ("class Solution:\n"
                "    def widgetCount(self, nums: list[int], target: int):\n"
                "        pass\n")


@pytest.fixture
def operator(db, django_user_model):
    return django_user_model.objects.create_user(
        username="generator-op", password="pw", email="g@example.com",
        is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Generation Portal")
    row, _ = Topic.objects.get_or_create(
        name="Array", defaults={"structure_type": "flat", "portal": portal})
    return row


def make_candidate(topic, question_id=9880, **overrides):
    fields = dict(
        id=question_id, title="Widget Count", topic=topic,
        base_difficulty=1300.0,
        content=(f"<p>{Question.PLACEHOLDER_MARKER} "
                 f"<strong>Widget Count</strong> challenge.</p>"),
        boilerplate_code={"python": VARIADIC},
        hidden_test_cases=[], hidden_wrapper_code={},
        execution_contract_version="v1")
    fields.update(overrides)
    return Question.objects.create(**fields)


def spec_dir_for(tmp_path, *questions):
    """Write an operator specification per question, as the command expects."""
    import json
    # Beside the output directory, never inside it: tests assert on
    # exactly which files generation produced.
    directory = tmp_path.parent / (tmp_path.name + "-specs")
    directory.mkdir(exist_ok=True)
    for question in questions:
        (directory / f"{question.pk}.spec.json").write_text(
            json.dumps(_for(question)), encoding="utf-8")
    return str(directory)


def _for(question):
    """The fixture specification, retargeted at another question."""
    specification = dict(SPECIFICATION)
    specification["question_id"] = question.pk
    return specification


@pytest.fixture
def candidate(topic):
    return make_candidate(topic)


@pytest.fixture
def spec(candidate):
    return gen.build_spec(candidate, specification=SPECIFICATION)


def frozen_batch(question, operator, key="gen-batch"):
    batch = RemediationBatch.objects.create(
        batch_key=key, purpose="generation tests", created_by=operator)
    pre_image.capture(batch, question, operator)
    RemediationBatch.objects.filter(pk=batch.pk).update(
        state=RemediationBatch.STATE_CAPTURED, frozen_at=timezone.now(),
        frozen_by=operator)
    batch.refresh_from_db()
    return batch


class FixedProvider:
    """Returns exactly what a test hands it."""

    name = "fixed"
    version = "0"

    def __init__(self, statement=GOOD_STATEMENT, starter=GOOD_STARTER):
        self.statement, self.starter = statement, starter
        self.calls = 0

    def produce(self, spec):
        self.calls += 1
        return self.statement, self.starter


# ═════════════════════════════════════════════════════════════
# 1-2. Input, and the eligibility check that precedes it
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_spec_carries_only_what_generation_needs(spec, candidate):
    assert spec.question_id == candidate.pk
    assert spec.title == "Widget Count"
    assert spec.topic == "Array"
    assert spec.difficulty_band == "medium"
    assert spec.class_name == "Solution"
    assert spec.method_name == "widgetCount"
    assert spec.input_digest == pre_image.live_digest(candidate)
    assert set(spec.as_dict()) == set(gen.GenerationSpec.__slots__)


@pytest.mark.django_db
def test_the_spec_is_frozen(spec):
    with pytest.raises(AttributeError):
        spec.title = "something else"


@pytest.mark.django_db
@pytest.mark.parametrize("overrides,fragment", [
    (dict(content="<p>A real statement.</p>"), "placeholder marker"),
    (dict(status=Question.STATUS_PUBLISHED), "not DRAFT"),
    (dict(hidden_test_cases=[{"stdin": "1"}]), "hidden test case"),
])
def test_an_ineligible_question_is_refused_before_generation(
        topic, overrides, fragment):
    question = make_candidate(topic, 9881, **overrides)
    with pytest.raises(gen.GenerationRefused, match=fragment):
        gen.build_spec(question, specification=_for(question))


@pytest.mark.django_db
def test_an_unknown_difficulty_is_refused(topic):
    question = make_candidate(topic, 9882, base_difficulty=1450.0)
    with pytest.raises(gen.GenerationRefused, match="outside the three bands"):
        gen.build_spec(question, specification=_for(question))


@pytest.mark.django_db
def test_generation_requires_membership_of_the_frozen_batch(topic, operator):
    """7: the question must belong to the frozen candidate batch."""
    inside = make_candidate(topic, 9883)
    outside = make_candidate(topic, 9884)
    batch = frozen_batch(inside, operator)

    assert gen.build_spec(inside, specification=_for(inside), batch=batch).frozen is True
    with pytest.raises(pre_image.CaptureIncomplete):
        gen.build_spec(outside, specification=_for(outside), batch=batch)


@pytest.mark.django_db
def test_without_a_batch_the_artifact_is_marked_inapplicable(spec):
    provider = FixedProvider()
    tries = gen.generate(spec, provider)
    manifest = gen.build_manifest(spec, provider, tries,
                                  filenames={"statement": "a", "starter": "b"})

    assert manifest["status"] == gen.STATUS_READY
    assert manifest["frozen_batch"] is False
    assert manifest["applicable"] is False


@pytest.mark.django_db
def test_inside_a_frozen_batch_the_artifact_is_applicable(topic, operator):
    question = make_candidate(topic, 9885)
    batch = frozen_batch(question, operator, key="applicable-batch")
    spec = gen.build_spec(question, specification=_for(question),
                          batch=batch)

    provider = FixedProvider()
    manifest = gen.build_manifest(
        spec, provider, gen.generate(spec, provider),
        filenames={"statement": "a", "starter": "b"})

    assert manifest["batch_key"] == "applicable-batch"
    assert manifest["applicable"] is True


# ═════════════════════════════════════════════════════════════
# 3-5. Outputs: exactly two files, and what is never generated
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_exactly_two_artifacts_plus_a_manifest(tmp_path, spec):
    provider = FixedProvider()
    gen.write_artifacts(tmp_path, spec, gen.generate(spec, provider), provider)

    written = sorted(path.name for path in tmp_path.iterdir())
    assert written == [f"{spec.question_id}.manifest.json",
                       f"{spec.question_id}.starter.py",
                       f"{spec.question_id}.statement.html"]


@pytest.mark.django_db
def test_nothing_resembling_grading_truth_is_produced(tmp_path, spec):
    provider = FixedProvider()
    manifest = gen.write_artifacts(tmp_path, spec,
                                   gen.generate(spec, provider), provider)

    forbidden = {"hidden_test_cases", "expected_output", "oracle", "approval",
                 "trust_state", "status_transition", "published",
                 "adaptive_eligible"}
    assert not (forbidden & set(manifest))
    for path in tmp_path.iterdir():
        if path.suffix == ".json":
            continue
        body = path.read_text(encoding="utf-8")
        assert "expected_output" not in body
        assert "hidden_test_cases" not in body


@pytest.mark.django_db
def test_generation_writes_nothing_to_the_database(tmp_path, spec, candidate):
    before = pre_image.live_digest(candidate)
    provider = FixedProvider()
    gen.write_artifacts(tmp_path, spec, gen.generate(spec, provider), provider)

    candidate.refresh_from_db()
    assert pre_image.live_digest(candidate) == before
    assert RemediationAction.objects.count() == 0
    assert ReseedLedger.objects.count() == 0


def test_the_generator_module_contains_no_write_path():
    """
    1/8: zero database write authority, asserted against the source rather
    than inferred from behaviour.
    """
    import inspect

    source = inspect.getsource(gen)
    for forbidden in (".save(", "objects.create(", "objects.update(",
                      "objects.bulk_create(", ".delete(", "record_action",
                      "select_for_update", "cursor.execute"):
        assert forbidden not in source, forbidden


# ═════════════════════════════════════════════════════════════
# 4/10. Both halves together, and made to agree
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_both_halves_come_from_one_call(spec):
    """
    Two calls would let the parameter list drift from the prose describing
    it, and the parameters are what hidden cases are later bound against.
    """
    provider = FixedProvider()
    gen.generate(spec, provider)
    assert provider.calls == 1


@pytest.mark.django_db
def test_the_prompt_demands_both_and_names_the_api(spec):
    prompt = gen.build_prompt(spec)
    assert "statement_html" in prompt and "starter_python" in prompt
    assert spec.method_name in prompt and spec.class_name in prompt
    assert "NO *args" in prompt and "**kwargs" in prompt
    assert "must be exactly `pass`" in prompt
    assert Question.PLACEHOLDER_MARKER in prompt      # as a prohibition


@pytest.mark.django_db
@pytest.mark.parametrize("statement,fragment", [
    # `nums` appears ONLY inside the signature line. A self-satisfying check
    # would pass this; the prose never says what nums is.
    ("<p>Given a <code>target</code>, count widgets.</p>"
     "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
     "<h3>Example</h3><p>Enough words here to pass the length floor easily.</p>",
     "never mentions the parameter 'nums' outside the signature line"),
    ("<p>Given <code>nums</code> and <code>target</code>, do the thing.</p>"
     "<h3>Example</h3><p>Enough words here to pass the length floor easily.</p>",
     "never names the method"),
])
def test_a_statement_that_disagrees_with_the_starter_is_refused(
        spec, statement, fragment):
    refusals = gen.semantic_refusals(spec, statement, GOOD_STARTER)
    assert any(fragment in refusal for refusal in refusals), refusals


@pytest.mark.django_db
def test_the_title_no_longer_arbitrates_once_a_specification_exists(topic):
    """
    The title-overlap heuristic belonged to the title-only era, when the title
    was the only source of truth. With a specification attached the
    specification IS the truth, and conformance checks against it directly.

    Keeping both would refuse a faithful statement for spelling the title
    differently — which it did: a specification written in British English
    ("coloured", "neighbours") against a title reading "Colored ...
    Neighbors" lost 4 of 7 title words and was rejected despite matching its
    specification exactly.
    """
    question = make_candidate(topic, 9886, title="Median of Two Sorted Arrays")
    built = gen.build_spec(question, specification=_for(question))

    assert gen.semantic_refusals(built, CONFORMING, GOOD_STARTER) == []


@pytest.mark.django_db
def test_a_well_formed_pair_is_accepted(spec):
    assert gen.validate_artifact(spec, GOOD_STATEMENT, GOOD_STARTER) == []


@pytest.mark.django_db
def test_validate_artifact_actually_runs_the_semantic_check(spec):
    """
    A mutation sweep dropped `semantic_refusals` from `validate_artifact` and
    every test still passed, because the semantic tests called it directly.
    The composition needs its own test, or the check can be unwired without
    anything noticing.
    """
    disagreeing = (
        "<p>Given a <code>target</code>, count widgets in the grid.</p>"
        "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
        "<h3>Example</h3><p>Plenty of visible text to clear the length floor "
        "so that only the semantic check can fail here.</p>")

    assert gen._statement_refusals(spec, disagreeing) == []
    assert gen._starter_refusals(spec, GOOD_STARTER) == []

    refusals = gen.validate_artifact(spec, disagreeing, GOOD_STARTER)
    assert any("outside the signature line" in refusal
               for refusal in refusals), refusals


# ═════════════════════════════════════════════════════════════
# 9. Deterministic validation
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("statement,fragment", [
    ("<p>Given <code>nums</code> and <code>target</code>, "
     "<code>widgetCount</code> counts things properly and at length.",
     "unclosed"),
    ("<p>nums target widgetCount</p></div>", "never opened"),
    ("<p><b>nums target widgetCount and plenty more words here</p></b>",
     "still open"),
    ("<p>short</p>", "not a problem description"),
    (f"<p>{Question.PLACEHOLDER_MARKER} nums target widgetCount and more "
     f"words to clear the floor comfortably here.</p>", "placeholder marker"),
    ("<p>nums target widgetCount with plenty of additional words.</p>"
     "<script>alert(1)</script>", "script"),
    ("<p>nums target widgetCount with plenty of additional words here.</p>"
     "<p><code>class Solution: return 4</code></p>", "worked solution"),
    ("<p>nums target widgetCount plenty of words</p><marquee>x</marquee>",
     "disallowed tag"),
])
def test_a_bad_statement_is_refused(spec, statement, fragment):
    refusals = gen._statement_refusals(spec, statement)
    assert any(fragment in refusal for refusal in refusals), refusals


@pytest.mark.django_db
@pytest.mark.parametrize("starter,fragment", [
    ("class Solution:\n    def widgetCount(self, nums: list[int])\n"
     "        pass\n", "not valid Python"),
    ("class Solution:\n    def widgetCount(self, *args):\n        pass\n",
     "*args"),
    ("class Solution:\n    def widgetCount(self, **kwargs):\n        pass\n",
     "*args"),
    ("class Solution:\n    def widgetCount(self, nums: list[int], *, k: int):\n"
     "        pass\n", "keyword-only"),
    ("class Solution:\n    def widgetCount(self, nums):\n        pass\n",
     "no type annotation"),
    ("class Solution:\n    def widgetCount(self, nums: list[int]):\n"
     "        return sum(nums)\n", "not a stub"),
    ("class Solution:\n    def widgetCount(self, nums: list[int]):\n"
     "        pass\n    def helper(self, x: int):\n        pass\n",
     "exactly one public"),
    ("class Solution:\n    def renamed(self, nums: list[int]):\n        pass\n",
     "renamed"),
    ("class Other:\n    def widgetCount(self, nums: list[int]):\n"
     "        pass\n", "class was renamed"),
    ("class Solution:\n    def widgetCount(self, nums: list[int]):\n"
     "        pass\nprint('hi')\n", "module-level"),
    ("class Solution:\n    def widgetCount(self, nums: list[int]):\n"
     "        pass\nCASES = [{'stdin': '1', 'expected_output': '2'}]\n",
     "test-case data"),
])
def test_a_bad_starter_is_refused(spec, starter, fragment):
    refusals = gen._starter_refusals(spec, starter)
    assert any(fragment in refusal for refusal in refusals), refusals


# ═════════════════════════════════════════════════════════════
# 6/11. Manifest, rejection, regeneration
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_manifest_records_everything_required(tmp_path, spec):
    provider = FixedProvider()
    manifest = gen.write_artifacts(tmp_path, spec,
                                   gen.generate(spec, provider), provider)

    for key in ("question_id", "input_digest", "generator_version",
                "prompt_template_version", "generated_at", "outputs",
                "status", "artifact_digest", "attempts",
                "regeneration_count"):
        assert key in manifest, key
    assert manifest["question_id"] == spec.question_id
    assert manifest["input_digest"] == spec.input_digest
    assert manifest["generator_version"] == gen.GENERATOR_VERSION
    assert manifest["prompt_template_version"] == gen.PROMPT_TEMPLATE_VERSION
    assert manifest["outputs"] == {
        "statement": f"{spec.question_id}.statement.html",
        "starter": f"{spec.question_id}.starter.py"}
    assert manifest["status"] == gen.STATUS_READY

    on_disk = json.loads(
        (tmp_path / f"{spec.question_id}.manifest.json").read_text("utf-8"))
    assert on_disk == manifest


@pytest.mark.django_db
def test_a_rejected_artifact_emits_no_production_file(tmp_path, spec):
    provider = FixedProvider(starter="class Solution:\n"
                                     "    def widgetCount(self, *args):\n"
                                     "        pass\n")
    manifest = gen.write_artifacts(tmp_path, spec,
                                   gen.generate(spec, provider), provider)

    assert manifest["status"] == gen.STATUS_REJECTED
    assert manifest["outputs"] == {}
    assert manifest["artifact_digest"] is None
    assert manifest["refusals"]

    names = sorted(path.name for path in tmp_path.iterdir())
    assert f"{spec.question_id}.starter.py" not in names
    assert f"{spec.question_id}.statement.html" not in names
    # kept beside the manifest so a human can see what was produced
    assert f"{spec.question_id}.starter.py.rejected" in names


@pytest.mark.django_db
def test_a_failure_is_never_silently_repaired(tmp_path, spec):
    """11: regenerate from the spec, never patch the provider's output."""
    bad = "class Solution:\n    def widgetCount(self, *args):\n        pass\n"
    provider = FixedProvider(starter=bad)
    tries = gen.generate(spec, provider, attempts=3)

    assert len(tries) == 3
    assert all(attempt["refusals"] for attempt in tries)
    assert all(attempt["starter"] == bad for attempt in tries)


class FlakyProvider:
    """Bad once, then good — the regeneration path."""

    name, version = "flaky", "0"

    def __init__(self):
        self.calls = 0

    def produce(self, spec):
        self.calls += 1
        if self.calls == 1:
            return GOOD_STATEMENT, ("class Solution:\n"
                                    "    def widgetCount(self, *args):\n"
                                    "        pass\n")
        return GOOD_STATEMENT, GOOD_STARTER


@pytest.mark.django_db
def test_the_manifest_records_why_each_attempt_failed(tmp_path, spec):
    """Why, not just how many — a provider failing the same check twice is a
    prompt problem, and the manifest is where that becomes visible."""
    provider = FlakyProvider()
    manifest = gen.write_artifacts(
        tmp_path, spec, gen.generate(spec, provider, attempts=3), provider)

    history = manifest["attempt_history"]
    assert [entry["attempt"] for entry in history] == [1, 2]
    assert any("*args" in refusal for refusal in history[0]["refusals"])
    assert history[1]["refusals"] == []


@pytest.mark.django_db
def test_regeneration_is_counted(tmp_path, spec):
    provider = FlakyProvider()
    manifest = gen.write_artifacts(
        tmp_path, spec, gen.generate(spec, provider, attempts=3), provider)

    assert manifest["status"] == gen.STATUS_READY
    assert manifest["attempts"] == 2
    assert manifest["regeneration_count"] == 1


@pytest.mark.django_db
def test_a_stale_manifest_is_detected(tmp_path, spec):
    provider = FixedProvider()
    manifest = gen.write_artifacts(tmp_path, spec,
                                   gen.generate(spec, provider), provider)
    assert gen.verify_manifest(tmp_path, manifest) == []

    (tmp_path / manifest["outputs"]["starter"]).write_text(
        GOOD_STARTER.replace("target: int", "target: str"), encoding="utf-8")

    problems = gen.verify_manifest(tmp_path, manifest)
    assert any("stale" in problem for problem in problems), problems


@pytest.mark.django_db
def test_the_artifact_digest_covers_both_halves(spec):
    base = gen.artifact_digest(GOOD_STATEMENT, GOOD_STARTER)
    assert base != gen.artifact_digest(GOOD_STATEMENT + " ", GOOD_STARTER)
    assert base != gen.artifact_digest(GOOD_STATEMENT, GOOD_STARTER + "\n")


@pytest.mark.django_db
def test_a_provider_returning_junk_is_refused(spec):
    class Junk:
        name, version = "junk", "0"

        def produce(self, spec):
            return gen._parse_json_response("not json at all")

    tries = gen.generate(spec, Junk())
    assert tries[-1]["refusals"]
    assert "did not return JSON" in tries[-1]["refusals"][0]


# ═════════════════════════════════════════════════════════════
# 12. The command, and its dry run
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_dry_run_writes_no_file_and_no_row(tmp_path, candidate, operator,
                                               capsys):
    call_command("reseed_generate", "--operator", operator.username,
                 "--out-dir", str(tmp_path), "--topic", "Array",
                 "--spec-dir", spec_dir_for(tmp_path, candidate),
                 "--limit", "5", "--local")

    assert list(tmp_path.iterdir()) == []
    assert RemediationAction.objects.count() == 0
    report = capsys.readouterr().out
    assert "DRY RUN" in report
    assert str(candidate.pk) in report
    assert pre_image.live_digest(candidate) in report


@pytest.mark.django_db
def test_emitting_produces_artifacts(tmp_path, candidate, operator):
    call_command("reseed_generate", "--operator", operator.username,
                 "--out-dir", str(tmp_path), "--topic", "Array",
                 "--spec-dir", spec_dir_for(tmp_path, candidate),
                 "--provider", "stub", "--emit", "--local")

    names = sorted(path.name for path in tmp_path.iterdir())
    assert f"{candidate.pk}.statement.html" in names
    assert f"{candidate.pk}.starter.py" in names
    assert f"{candidate.pk}.manifest.json" in names

    candidate.refresh_from_db()
    assert Question.PLACEHOLDER_MARKER in candidate.content
    assert RemediationAction.objects.count() == 0


@pytest.mark.django_db
def test_excluded_questions_are_never_generated_for(tmp_path, topic, operator):
    keep = make_candidate(topic, 9890)
    skip = make_candidate(topic, 9891)

    call_command("reseed_generate", "--operator", operator.username,
                 "--out-dir", str(tmp_path), "--topic", "Array",
                 "--spec-dir", spec_dir_for(tmp_path, keep, skip),
                 "--exclude", str(skip.pk), "--provider", "stub", "--emit",
                 "--local")

    names = {path.name for path in tmp_path.iterdir()}
    assert f"{keep.pk}.manifest.json" in names
    assert not any(str(skip.pk) in name for name in names)


@pytest.mark.django_db
def test_the_command_never_calls_the_reseed_writers():
    """14: artifacts are reviewed by a human, then applied by other commands."""
    import inspect
    from groups.management.commands import reseed_generate

    source = inspect.getsource(reseed_generate)
    for writer in ("reseed_statement", "declare_signature",
                   "reseed_orchestrate"):
        assert f'call_command("{writer}"' not in source
        assert f"call_command('{writer}'" not in source


def test_the_command_forbids_every_write_on_production():
    from groups.management.commands import reseed_generate

    forbidden = set(reseed_generate.GENERATOR_FORBIDDEN)
    for table in ("groups_question", "groups_reseedledger",
                  "groups_remediationaction", "groups_questionpreimage"):
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert (table, None, privilege) in forbidden


# ═════════════════════════════════════════════════════════════
# P2.7h-21 — conformance is COMPOSED into validate_artifact
#
# Phase 5 lost a mutation here: dropping `semantic_refusals` from
# `validate_artifact` changed nothing, because every semantic test called it
# directly. These tests go through the composition on purpose. If the
# conformance call is removed from `validate_artifact`, they fail.
# ═════════════════════════════════════════════════════════════

def _statement(body):
    """A structurally valid statement carrying `body` as its prose."""
    return ("<p>" + body + "</p>"
            "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
            "<h3>Example</h3><p>Input: nums = [1,2], target = 2. Output: 1.</p>")


CONFORMING = _statement(
    "Count how many widgets in <code>nums</code> are equal to the "
    "<code>target</code> value, and return that count. Walk the list and "
    "count each element equal to the target. Every element is examined and "
    "equality is exact. A target absent from the list gives zero; repeated "
    "values are each counted. The list holds between 1 and 100 integers, "
    "each between 1 and 1000 inclusive. The method takes two parameters, "
    "returns one integer and does not print.")


@pytest.mark.django_db
def test_the_conforming_pair_passes_the_whole_composition(spec):
    assert gen.validate_artifact(spec, CONFORMING, GOOD_STARTER) == []


@pytest.mark.django_db
def test_validate_artifact_calls_conformance(spec):
    """
    The composition test the Phase 5 survivor demanded.

    The statement below is structurally perfect — valid HTML, correct
    signature, method and parameters named — and drops a requirement. Only
    the conformance call can see that, so this test fails the moment it is
    removed from `validate_artifact`.
    """
    lossy = CONFORMING.replace("Every element is examined and equality is "
                               "exact. ", "")

    assert gen._statement_refusals(spec, lossy) == []
    assert gen._starter_refusals(spec, GOOD_STARTER) == []
    assert gen.semantic_refusals(spec, lossy, GOOD_STARTER) == []

    refusals = gen.validate_artifact(spec, lossy, GOOD_STARTER)
    assert refusals, "conformance is not wired into validate_artifact"
    assert any("omits load-bearing terms" in refusal for refusal in refusals)


@pytest.mark.django_db
@pytest.mark.parametrize("mutation,replacement,fragment", [
    # missing requirement
    ("Every element is examined and equality is exact. ", "", "every"),
    # changed quantifier — "every" is lost and "any" appears in its place
    ("Every element is examined and equality is exact. ",
     "Any element may be examined. ", "every"),
    # dropped requirement — the sentence carrying "every" is gone entirely
    ("Every element is examined and equality is exact. ", "", "every"),
])
def test_a_changed_requirement_is_refused_through_the_composition(
        spec, mutation, replacement, fragment):
    statement = CONFORMING.replace(mutation, replacement)
    refusals = gen.validate_artifact(spec, statement, GOOD_STARTER)

    assert refusals, (mutation, replacement)
    assert any(fragment in refusal.lower() for refusal in refusals), refusals


@pytest.mark.django_db
def test_a_changed_adjacency_requirement_is_refused(topic):
    """
    The q1970 defect in miniature: a specification demanding BOTH adjacent
    neighbours, and a statement that quietly widened it to any cell.
    """
    question = make_candidate(topic, 9895)
    specification = dict(SPECIFICATION)
    specification["question_id"] = question.pk
    specification["required_operation"] = (
        "Remove a piece only when both of the cells adjacent to it hold the "
        "same value, walking the row from left to right and counting each "
        "removal that is legal under that rule.")
    built = gen.build_spec(question, specification=specification)

    widened = _statement(
        "Remove a piece when any cell near it holds the same value, walking "
        "the row from left to right and counting each removal. Every element "
        "is examined and equality is exact. A target absent from the list "
        "gives zero; repeated values are each counted. The list holds "
        "between 1 and 100 integers, each between 1 and 1000 inclusive. The "
        "method takes two parameters, returns one integer and does not print.")

    refusals = gen.validate_artifact(built, widened, GOOD_STARTER)
    assert any("adjacent" in refusal for refusal in refusals), refusals


@pytest.mark.django_db
def test_a_legitimate_paraphrase_is_accepted(spec):
    """Rewording that keeps every load-bearing term must not be refused."""
    reworded = _statement(
        "For each widget in <code>nums</code>, check whether it is equal to "
        "<code>target</code>; return how many are. Walk the list and count "
        "each element equal to the target. Every element is examined and "
        "equality is exact. A target absent from the list gives zero; "
        "repeated values are each counted. The list holds between 1 and 100 "
        "integers, each between 1 and 1000 inclusive. The method takes two "
        "parameters, returns one integer and does not print.")

    assert gen.validate_artifact(spec, reworded, GOOD_STARTER) == []


# ── the specification itself ──────────────────────────────────────────

@pytest.mark.django_db
def test_generation_without_a_specification_is_refused(candidate):
    with pytest.raises(gen.GenerationRefused, match="no operator specification"):
        gen.build_spec(candidate, specification=None)


@pytest.mark.django_db
def test_an_empty_specification_is_refused(candidate):
    with pytest.raises(gen.GenerationRefused,
                       match="no operator specification"):
        gen.build_spec(candidate, specification={})

    incomplete = {"question_id": candidate.pk, "provenance": "OPERATOR_SUPPLIED"}
    with pytest.raises(gen.GenerationRefused, match="missing required fields"):
        gen.build_spec(candidate, specification=incomplete)


@pytest.mark.django_db
def test_a_specification_for_another_question_is_refused(candidate):
    wrong = dict(SPECIFICATION)
    wrong["question_id"] = candidate.pk + 1
    with pytest.raises(gen.GenerationRefused, match="not "):
        gen.build_spec(candidate, specification=wrong)


@pytest.mark.django_db
def test_a_reconstructed_specification_is_refused(candidate):
    """
    Provenance is checked, not decorative. A specification rebuilt from a
    title is precisely what Phase 5 proved unsafe.
    """
    reconstructed = dict(SPECIFICATION)
    reconstructed["provenance"] = "TITLE_RECONSTRUCTED"
    with pytest.raises(gen.GenerationRefused, match="OPERATOR_SUPPLIED"):
        gen.build_spec(candidate, specification=reconstructed)


@pytest.mark.django_db
def test_a_thin_specification_is_refused(candidate):
    thin = dict(SPECIFICATION)
    for field in spec_mod.REQUIRED_PROSE:
        thin[field] = "do the thing"
    with pytest.raises(gen.GenerationRefused, match="title with extra steps"):
        gen.build_spec(candidate, specification=thin)


@pytest.mark.django_db
def test_the_specification_digest_is_recorded_and_stable(candidate, spec):
    assert spec.specification_digest == spec_mod.specification_digest(
        SPECIFICATION)

    reformatted = dict(SPECIFICATION)
    reformatted["objective"] = "  " + SPECIFICATION["objective"] + "  "
    assert spec_mod.specification_digest(reformatted) == \
        spec.specification_digest, "whitespace must not move the digest"

    changed = dict(SPECIFICATION)
    changed["load_bearing"] = "Only the first element is examined."
    assert spec_mod.specification_digest(changed) != spec.specification_digest


@pytest.mark.django_db
def test_a_stale_specification_is_detected_by_its_digest(tmp_path, candidate):
    """
    A specification edited after an artifact was built no longer digests to
    what the manifest recorded — which is how a stale one is caught.
    """
    import json

    path = tmp_path / f"{candidate.pk}.spec.json"
    path.write_text(json.dumps(_for(candidate)), encoding="utf-8")
    loaded = spec_mod.load_specification(path, question_id=candidate.pk)
    built = gen.build_spec(candidate, specification=loaded)

    edited = dict(_for(candidate))
    edited["required_operation"] = "Return the first element and stop."
    path.write_text(json.dumps(edited), encoding="utf-8")
    reloaded = spec_mod.load_specification(path, question_id=candidate.pk)

    assert reloaded["specification_digest"] != built.specification_digest


@pytest.mark.django_db
def test_the_manifest_records_the_specification(tmp_path, spec):
    provider = FixedProvider(statement=CONFORMING)
    manifest = gen.write_artifacts(tmp_path, spec,
                                   gen.generate(spec, provider), provider)

    assert manifest["status"] == gen.STATUS_READY
    assert manifest["specification_digest"] == spec.specification_digest
    assert manifest["specification_provenance"] == "OPERATOR_SUPPLIED"
    assert manifest["prompt_template_version"] == gen.PROMPT_TEMPLATE_VERSION


@pytest.mark.django_db
def test_the_prompt_carries_the_specification_and_forbids_extending_it(spec):
    prompt = gen.build_prompt(spec)

    assert "THE SPECIFICATION IS THE SOURCE OF TRUTH" in prompt
    assert SPECIFICATION["load_bearing"] in prompt
    assert "Do NOT add, remove or alter any requirement" in prompt


def test_the_conformance_limitation_is_stated_in_the_module():
    """
    The boundary of the claim is documented where a reader will meet it. A
    requirement-loss detector described as a correctness proof is worse than
    no detector, because it invites trust it cannot support.
    """
    import inspect

    from groups import reseed_conformance

    text = inspect.getdoc(reseed_conformance) or ""
    assert "not equivalence" in text.lower()
    assert inspect.getdoc(gen.conformance_refusals)
    assert "not a proof of semantic equivalence" in \
        inspect.getdoc(gen.conformance_refusals)


@pytest.mark.django_db
def test_KNOWN_LIMITATION_a_substitution_survives_if_the_term_does(spec):
    """
    The boundary of the claim, as a test rather than a caveat.

    Conformance detects requirement LOSS. If a statement changes its operative
    sentence but the original term still appears somewhere else — in a
    restated constraint, an example, a heading — the term is not "omitted" and
    nothing fires.

    Here the operative verb becomes `sum` while the word `count` survives in
    the surrounding prose. The requirement has changed and the check passes.

    This is why human semantic review remains mandatory and why the module
    calls itself a requirement-loss detector rather than a correctness proof.
    A validator whose limits are written down is one people can rely on
    correctly; one whose limits are implied is one they will over-trust.
    """
    substituted = CONFORMING.replace(
        "Walk the list and count each element equal to the target.",
        "Walk the list and sum each element equal to the target.")

    assert "count" in substituted            # the term survives elsewhere
    assert gen.validate_artifact(spec, substituted, GOOD_STARTER) == []


@pytest.mark.django_db
def test_a_changed_extremal_condition_is_refused(topic):
    """
    The q1974 defect through the composition: a specification naming the
    smallest and largest values, and a statement that swapped them for all
    elements. This is the artifact Phase 5 shipped as READY.
    """
    question = make_candidate(topic, 9896)
    specification = dict(SPECIFICATION)
    specification["question_id"] = question.pk
    specification["required_operation"] = (
        "Find the smallest value and the largest value in the list, then "
        "return the greatest common divisor of those two numbers only, "
        "examining no other element of the list.")
    built = gen.build_spec(question, specification=specification)

    swapped = _statement(
        "Compute the greatest common divisor of all elements in "
        "<code>nums</code> and return it, given also a <code>target</code>. "
        "Every element is examined and equality is exact. A target absent "
        "from the list gives zero; repeated values are each counted. The "
        "list holds between 1 and 100 integers, each between 1 and 1000 "
        "inclusive. The method takes two parameters, returns one integer "
        "and does not print.")

    refusals = gen.validate_artifact(built, swapped, GOOD_STARTER)
    assert refusals
    assert any("smallest" in refusal and "largest" in refusal
               for refusal in refusals), refusals


# ═════════════════════════════════════════════════════════════
# P2.7h-22 — presentation is COMPOSED into validate_artifact
#
# Same adversarial standard as the conformance composition: these go through
# `validate_artifact`, so removing the presentation call from it makes them
# fail. Each fixture is deliberately CONFORMANT — it keeps every load-bearing
# term — so only the presentation check can refuse it. That is what proves the
# two gates are independent.
# ═════════════════════════════════════════════════════════════

_CONFORMANT_BODY = (
    "Count how many widgets in <code>nums</code> are equal to the "
    "<code>target</code> value, and return that count. Walk the list and "
    "count each element equal to the target. Every element is examined and "
    "equality is exact. A target absent from the list gives zero; repeated "
    "values are each counted. The list holds between 1 and 100 integers, "
    "each between 1 and 1000 inclusive. The method takes two parameters, "
    "returns one integer and does not print.")


@pytest.mark.django_db
def test_validate_artifact_calls_the_presentation_gate(spec):
    """
    A statement that is structurally valid AND fully conformant, and reads
    like the specification's form rather than a problem. Only the
    presentation check can see it, so this fails the moment that call is
    removed from `validate_artifact`.
    """
    transcribed = (
        "<h3>Objective</h3><p>" + _CONFORMANT_BODY + "</p>"
        "<h3>Required operation</h3><p>Walk the list and count matches.</p>"
        "<h3>Input semantics</h3><p>Two parameters, nums and target.</p>"
        "<h3>Output semantics</h3><p>A single integer.</p>"
        "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
        "<h3>Example</h3><p>nums = [1, 2], target = 2 → Result: 1</p>")

    assert gen._statement_refusals(spec, transcribed) == []
    assert gen.conformance_refusals(spec, transcribed) == []

    found = gen.validate_artifact(spec, transcribed, GOOD_STARTER)
    assert found, "the presentation gate is not wired into validate_artifact"
    assert any("internal specification labels" in refusal
               for refusal in found), found


@pytest.mark.django_db
def test_the_composition_catches_author_directed_commentary(spec):
    commented = _statement(
        "The greatest common divisor of the whole list is NOT what is "
        "wanted. " + _CONFORMANT_BODY)

    found = gen.validate_artifact(spec, commented, GOOD_STARTER)
    assert any("not wanted" in refusal for refusal in found), found


@pytest.mark.django_db
def test_the_composition_catches_a_missing_example(spec):
    without = ("<h3>Problem</h3><p>" + _CONFORMANT_BODY + "</p>"
               "<p><code>widgetCount(nums: list[int], target: int)</code></p>")

    found = gen.validate_artifact(spec, without, GOOD_STARTER)
    assert any("no worked example" in refusal for refusal in found), found


@pytest.mark.django_db
def test_the_composition_catches_internal_metadata(spec):
    leaky = _statement(
        "Provenance: OPERATOR_SUPPLIED. " + _CONFORMANT_BODY)

    found = gen.validate_artifact(spec, leaky, GOOD_STARTER)
    assert any("provenance" in refusal.lower() for refusal in found), found


@pytest.mark.django_db
def test_the_composition_catches_transcription(spec):
    """Three schema labels as headings, every requirement kept."""
    formish = (
        "<h3>Objective</h3><p>" + _CONFORMANT_BODY + "</p>"
        "<h3>Constraints</h3><p>Between 1 and 100 integers.</p>"
        "<h3>Edge cases</h3><p>Zero when absent.</p>"
        "<h3>Method behaviour</h3><p>Returns one integer.</p>"
        "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
        "<h3>Example</h3><p>nums = [1, 2], target = 2 → 1</p>")

    found = gen.validate_artifact(spec, formish, GOOD_STARTER)
    assert any("reproduces the specification's structure" in refusal
               for refusal in found), found


@pytest.mark.django_db
def test_a_well_presented_conformant_artifact_passes_everything(spec):
    """Both gates satisfied at once — the shape the pipeline is aiming for."""
    good = ("<h3>Counting matches</h3><p>" + _CONFORMANT_BODY + "</p>"
            "<p><code>widgetCount(nums: list[int], target: int)</code></p>"
            "<h3>Example</h3><p>nums = [1, 2, 2, 3], target = 2 → Result: 2"
            "</p>")

    assert gen.validate_artifact(spec, good, GOOD_STARTER) == []


@pytest.mark.django_db
def test_the_two_gates_are_independent(spec):
    """
    Neither subsumes the other, which is the whole architectural point:
    conformance rewards keeping the specification's words, so transcription
    scores perfectly on it; presentation rewards writing a problem, which a
    paraphrase can do while dropping a requirement.
    """
    transcribed = (
        "<h3>Objective</h3><p>" + _CONFORMANT_BODY + "</p>"
        "<h3>Required operation</h3><p>Count matches.</p>"
        "<h3>Input semantics</h3><p>nums and target.</p>"
        "<h3>Example</h3><p>nums = [1, 2], target = 2 → 1</p>")
    lossy = _statement(
        "Count how many widgets in <code>nums</code> match the "
        "<code>target</code> and return that count. Some elements may be "
        "skipped. The list holds between 1 and 100 integers.")

    # conformant, badly presented
    assert gen.conformance_refusals(spec, transcribed) == []
    assert gen.presentation_refusals(spec, transcribed, GOOD_STARTER)

    # well presented, not conformant
    assert gen.presentation_refusals(spec, lossy, GOOD_STARTER) == []
    assert gen.conformance_refusals(spec, lossy)
