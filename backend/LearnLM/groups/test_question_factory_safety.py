"""
Question Factory safety (M2 P2.7b).

reseed_questions is a PROPOSAL mechanism, not a grading-truth publisher. These
tests pin the boundary, because every defect they cover was silent: nothing
errored, nothing logged, the question simply became worse.

The four the audit proved:

  * `{"python": starter}` replaced the whole boilerplate dict, so a
    plain-string response deleted java/cpp/js/c — and since the editor derives
    its language picker from those keys, a five-language question became a
    python-only one with no error;
  * the floor was two cases;
  * C and C++ starters were `class Solution` templates, but both languages are
    self-contained and run with no wrapper, so they have no entry point and
    cannot link;
  * python starters carried no parameter annotations, so the v2 grader falls
    back to a heuristic that cannot tell a one-element list from a scalar.

Plus the boundary itself: reseed must never overwrite an existing hidden-test
suite, and must never move a question between execution contracts.
"""

import pytest

from groups.management.commands.reseed_questions import (
    Command, MIN_TEST_CASES, SOURCE_LLM_UNVERIFIED, tag_unverified,
)
from groups.models import CodingPortal, Question, Topic

PY_OK = "class Solution:\n    def solve(self, x: int) -> int:\n        pass"
JAVA_OK = "class Solution { public int solve(int x) { return 0; } }"
JS_OK = "class Solution { solve(x) {} }"
C_OK = "#include <stdio.h>\nint main(void) { return 0; }"
CPP_OK = "#include <bits/stdc++.h>\nint main() { return 0; }"

FIVE_LANGUAGES = {"python": PY_OK, "java": JAVA_OK, "javascript": JS_OK,
                  "c": C_OK, "cpp": CPP_OK}


@pytest.fixture
def cmd():
    return Command()


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Factory Portal")
    t, _ = Topic.objects.get_or_create(
        name="FactoryTopic", defaults={"structure_type": "flat", "portal": portal}
    )
    return t


def make(topic, boilerplate=None, cases=None, version="v1", wrapper=None):
    return Question.objects.create(
        title="Factory Q", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code=boilerplate if boilerplate is not None else {},
        hidden_test_cases=cases if cases is not None else [],
        hidden_wrapper_code=wrapper or {},
        execution_contract_version=version,
    )


def payload(starter, n_cases=12):
    return {
        "content": "<p>A statement long enough to satisfy the length check.</p>",
        "starter_code": starter,
        "hidden_test_cases": [
            {"stdin": str(i), "expected_output": str(i)} for i in range(n_cases)
        ],
    }


# ─────────────────────────────────────────────────────────────
# FIX 1 — boilerplate merge
# ─────────────────────────────────────────────────────────────

def test_regenerating_python_keeps_the_other_four_languages(cmd, topic):
    """
    The defect. A python-only response used to become the ENTIRE boilerplate
    dict, and the editor derives its language picker from these keys — so four
    languages vanished from the product with no error.
    """
    question = make(topic, boilerplate=dict(FIVE_LANGUAGES))

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert sorted(question.boilerplate_code) == ["c", "cpp", "java", "javascript", "python"]
    assert question.boilerplate_code["java"] == JAVA_OK
    assert question.boilerplate_code["cpp"] == CPP_OK


def test_a_plain_string_response_does_not_wipe_the_dictionary(cmd, topic):
    """
    The legacy shape specifically: `starter_code` as a bare string. It is
    coerced to a python entry and MERGED, never substituted for the whole map.
    """
    question = make(topic, boilerplate=dict(FIVE_LANGUAGES))

    cmd._save_question(question, payload(PY_OK), "new content")

    question.refresh_from_db()
    assert len(question.boilerplate_code) == 5
    assert question.boilerplate_code["python"] == PY_OK


def test_regenerating_java_keeps_python(cmd, topic):
    question = make(topic, boilerplate={"python": PY_OK})

    cmd._save_question(question, payload({"java": JAVA_OK}), "new content")

    question.refresh_from_db()
    assert question.boilerplate_code == {"python": PY_OK, "java": JAVA_OK}


def test_a_missing_language_is_added(cmd, topic):
    question = make(topic, boilerplate={"python": PY_OK})

    cmd._save_question(question, payload({"cpp": CPP_OK}), "new content")

    question.refresh_from_db()
    assert set(question.boilerplate_code) == {"python", "cpp"}


def test_a_malformed_existing_entry_is_not_a_reason_to_drop_the_rest(cmd, topic):
    question = make(topic, boilerplate={"python": PY_OK, "java": ""})

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert set(question.boilerplate_code) == {"python", "java"}


def test_a_question_with_no_boilerplate_gains_the_generated_one(cmd, topic):
    question = make(topic, boilerplate={})

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert question.boilerplate_code == {"python": PY_OK}


def test_repeated_reseeding_is_idempotent_for_boilerplate(cmd, topic):
    question = make(topic, boilerplate=dict(FIVE_LANGUAGES))

    for _ in range(3):
        cmd._save_question(question, payload({"python": PY_OK}), "new content")
        question.refresh_from_db()

    assert sorted(question.boilerplate_code) == ["c", "cpp", "java", "javascript", "python"]


def test_a_custom_wrapper_is_never_touched(cmd, topic):
    """
    A per-question wrapper defines the question's whole execution contract and
    outranks the version in `_build_executable`. reseed has no business in it.
    """
    question = make(topic, boilerplate={"python": PY_OK},
                    wrapper={"python": "CUSTOM {user_code}"})

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert question.hidden_wrapper_code == {"python": "CUSTOM {user_code}"}


def test_no_language_ever_disappears(cmd, topic):
    """The invariant, stated directly rather than per-scenario."""
    question = make(topic, boilerplate=dict(FIVE_LANGUAGES))
    before = set(question.boilerplate_code)

    for starter in ({"python": PY_OK}, PY_OK, {"java": JAVA_OK}, {}):
        cmd._save_question(question, payload(starter), "new content")
        question.refresh_from_db()
        assert before <= set(question.boilerplate_code)


# ─────────────────────────────────────────────────────────────
# FIX 2 — the hidden-test floor
# ─────────────────────────────────────────────────────────────

def test_the_floor_is_twelve():
    assert MIN_TEST_CASES == 12


@pytest.mark.parametrize("count,accepted", [
    (0, False), (1, False), (11, False), (12, True), (13, True), (20, True),
])
def test_the_floor_is_enforced_at_the_boundary(cmd, count, accepted):
    error = cmd._validate_ai_payload(payload({"python": PY_OK}, n_cases=count))

    assert (error is None) is accepted, error


def test_generated_cases_are_tagged_as_unverified(cmd, topic):
    """
    reseed proposes; it does not decide what is correct. Every case it writes
    records that an LLM produced the expected output and NOTHING executed a
    trusted reference against it, so a downstream consumer can tell without
    guessing. `source` is already an optional field in the P2.5 contract, so
    this needs no schema change.
    """
    question = make(topic, cases=[])

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert all(c["source"] == SOURCE_LLM_UNVERIFIED for c in question.hidden_test_cases)


def test_tagging_does_not_alter_the_values_it_tags():
    original = [{"stdin": "a", "expected_output": "b"}]

    tagged = tag_unverified(original)

    assert tagged[0]["stdin"] == "a"
    assert tagged[0]["expected_output"] == "b"
    assert original[0] == {"stdin": "a", "expected_output": "b"}  # not mutated


# ─────────────────────────────────────────────────────────────
# The grading-truth boundary
# ─────────────────────────────────────────────────────────────

def test_an_existing_hidden_test_suite_is_never_overwritten(cmd, topic):
    """
    The central safety property. Those expected outputs may since have been
    verified against an oracle, and this generator has no way to know.
    Regeneration belongs to P2.7c/P2.7d, behind the approval gate.
    """
    existing = [{"stdin": "keep", "expected_output": "me"}]
    question = make(topic, cases=list(existing))

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert question.hidden_test_cases == existing


def test_an_empty_suite_is_armed(cmd, topic):
    """The other half — reseed's actual job is arming unarmed questions."""
    question = make(topic, cases=[])

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 12


def test_running_the_same_reseed_three_times_is_idempotent(cmd, topic):
    """
    Stated as the end-to-end property rather than inferred from the branch
    structure: the second and third runs must not append, duplicate, or
    re-tag. The first arms the question; the rest are no-ops on grading data.
    """
    question = make(topic, cases=[])

    cmd._save_question(question, payload({"python": PY_OK}), "content 1")
    question.refresh_from_db()
    after_first = question.hidden_test_cases

    for content in ("content 2", "content 3"):
        cmd._save_question(question, payload({"python": PY_OK}), content)
        question.refresh_from_db()

    assert question.hidden_test_cases == after_first
    assert len(question.hidden_test_cases) == 12
    stdins = [c["stdin"] for c in question.hidden_test_cases]
    assert len(set(stdins)) == 12, "repeated reseeding duplicated hidden tests"


def test_the_hidden_test_count_never_decreases(cmd, topic):
    question = make(topic, cases=[{"stdin": str(i), "expected_output": str(i)}
                                  for i in range(20)])

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert len(question.hidden_test_cases) == 20


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_reseeding_never_changes_the_execution_contract(cmd, topic, version):
    """
    Migrating a question between contracts changes how it grades. That is an
    explicit operation gated on boilerplate, an approved oracle and hidden
    tests — never a side effect of regenerating content.
    """
    question = make(topic, version=version)

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert question.execution_contract_version == version


def test_reseeding_writes_only_the_three_intended_fields(cmd, topic):
    question = make(topic, boilerplate={"python": PY_OK}, version="v1")
    question.base_difficulty = 1234.0
    question.save(update_fields=["base_difficulty"])

    cmd._save_question(question, payload({"python": PY_OK}), "new content")

    question.refresh_from_db()
    assert question.base_difficulty == 1234.0
    assert question.execution_contract_version == "v1"


# ─────────────────────────────────────────────────────────────
# FIX 3 — C/C++ execution model
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["c", "cpp"])
def test_a_self_contained_template_without_main_is_rejected(cmd, language):
    """
    The proven defect: the generator emitted `class Solution` for C++ and a
    bare function for C. Both run raw with no wrapper, so neither has an entry
    point and neither can link.
    """
    starter = {"python": PY_OK,
               language: "class Solution { public: int solve(int x){return 0;} };"}

    error = cmd._validate_ai_payload(payload(starter))

    assert error is not None
    assert "main()" in error and language in error


@pytest.mark.parametrize("language,template", [("c", C_OK), ("cpp", CPP_OK)])
def test_a_self_contained_template_with_main_is_accepted(cmd, language, template):
    """Positive control — the rule must not reject every C/C++ template."""
    assert cmd._validate_ai_payload(
        payload({"python": PY_OK, language: template})
    ) is None


@pytest.mark.parametrize("language", ["java", "javascript"])
def test_a_reflection_template_without_a_solution_class_is_rejected(cmd, language):
    starter = {"python": PY_OK, language: "int solve(int x){ return 0; }"}

    error = cmd._validate_ai_payload(payload(starter))

    assert error is not None and "Solution" in error


# ─────────────────────────────────────────────────────────────
# FIX 4 — python annotations
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("signature,accepted", [
    ("def solve(self, nums: list[int], target: int) -> list[int]:", True),
    ("def solve(self, words: list[str]) -> str:", True),
    ("def solve(self, x: int) -> int:", True),
    ("def solve(self, x: float) -> float:", True),
    ("def solve(self, s: str) -> bool:", True),
    ("def solve(self) -> int:", True),                 # no parameters to annotate
    ("def solve(self, nums, target):", False),         # the defect
    ("def solve(self, nums: list[int], target):", False),  # partially annotated
])
def test_python_parameters_must_be_annotated(cmd, signature, accepted):
    """
    The v2 grader types arguments from the signature. Without annotations it
    falls back to a heuristic where a single-token line is a scalar — wrong
    for a valid one-element array. The type belongs in the template, not in a
    guess made at grading time, and NOT in an inference from the parameter's
    name.
    """
    starter = {"python": f"class Solution:\n    {signature}\n        pass"}

    error = cmd._validate_ai_payload(payload(starter))

    assert (error is None) is accepted, error


def test_annotation_checking_does_not_guess_from_parameter_names(cmd):
    """
    A parameter called `nums` with no annotation is still unannotated. Name-
    based inference is the fragile heuristic this check exists to remove.
    """
    starter = {"python": "class Solution:\n    def solve(self, nums):\n        pass"}

    assert cmd._validate_ai_payload(payload(starter)) is not None
