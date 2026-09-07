"""
The C and C++ execution contract (M2 P2.37 / Phase 1 M4).

THE CONTRACT
    C and C++ are SELF-CONTAINED. The learner's source is compiled and run
    exactly as written, with no wrapper, so it must be a complete program:
    includes, a `main()`, reading stdin and printing the answer.

This is not inferred from the registry flag. It is stated in
`ai_services.generate_full_question`'s own prompt ("A 'Solution' class for
c/cpp has no entry point, cannot link, and will be rejected"), matched by its
fallback template, and enforced by `reseed_questions._validate_starter_code`.

WHAT WENT WRONG
    A second generator, `generate_starter_stubs`, described the opposite
    contract — it asked for "a Solution class for the object-oriented
    languages (java/cpp/javascript)" and its validator REJECTED any stub
    without the word "Solution". So it requested the wrong shape and then
    discarded the right one. That is where all 638 C++ starters came from.

── What these tests can and cannot prove ───────────────────────────────────

There is no `g++` in this environment and Judge0 is returning 429/403, so
NOTHING here compiles or runs C++. These tests pin the contract BOUNDARY —
that the platform hands Judge0 the learner's source unaltered, under the
right language id, and that both generators now agree — which is exactly the
part that was broken. Compilation, runtime failure and timeout behaviour are
Judge0's, and are listed as unverified in the milestone report rather than
asserted here.
"""

import inspect
import re

import pytest

from common import languages
from groups import ai_services, execution_contract, language_readiness
from groups.models import CodingPortal, Question, Topic
from groups.services import GradingService

CPP_COMPLETE = (
    "#include <bits/stdc++.h>\n"
    "using namespace std;\n\n"
    "int main() {\n"
    "    int x;\n"
    "    cin >> x;\n"
    "    cout << x;\n"
    "    return 0;\n"
    "}\n"
)
CPP_SOLUTION_CLASS = (
    "class Solution {\npublic:\n    int reverse(int x) {\n"
    "        return 0;\n    }\n};\n"
)
C_COMPLETE = (
    "#include <stdio.h>\n\n"
    "int main(void) {\n"
    "    int x;\n"
    "    scanf(\"%d\", &x);\n"
    "    printf(\"%d\", x);\n"
    "    return 0;\n"
    "}\n"
)


@pytest.fixture
def question(db):
    portal = CodingPortal.objects.create(name="Cpp Portal")
    topic, _ = Topic.objects.get_or_create(
        name="CppTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return Question.objects.create(
        id=9700, title="Q9700", content="Statement.", topic=topic,
        base_difficulty=1300.0,
        boilerplate_code={"cpp": CPP_COMPLETE, "c": C_COMPLETE},
        hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
        hidden_wrapper_code={}, execution_contract_version="v1")


# ═════════════════════════════════════════════════════════════
# The contract boundary: source reaches Judge0 unaltered
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("language,source", [
    ("cpp", CPP_COMPLETE), ("c", C_COMPLETE)])
def test_self_contained_source_is_passed_through_unwrapped(
        question, language, source):
    """
    THE contract. Anything else — a wrapper, an injected main, a rewritten
    include block — would change the program the learner wrote.
    """
    executable, stored = GradingService._build_executable(
        question, language, source)

    assert executable == source
    assert stored == source


@pytest.mark.django_db
def test_a_reflection_language_is_still_wrapped(question):
    """The control: pass-through is specific to the self-contained model."""
    python_source = "class Solution:\n    def f(self, x: int) -> int:\n        return x\n"

    executable, _ = GradingService._build_executable(
        question, "python", python_source)

    assert executable != python_source
    assert python_source in executable        # the learner's code is inside


@pytest.mark.django_db
def test_includes_and_main_survive_verbatim(question):
    """
    Named explicitly because these are the two things a wrapper would most
    plausibly move or duplicate, and either would break compilation.
    """
    executable, _ = GradingService._build_executable(
        question, "cpp", CPP_COMPLETE)

    assert executable.count("#include <bits/stdc++.h>") == 1
    assert executable.count("int main()") == 1
    assert executable.startswith("#include")


@pytest.mark.django_db
def test_stdin_is_not_transformed_for_a_self_contained_language(question):
    """
    The learner's program reads raw stdin. `prepare_stdin` must not reshape
    it into the JSON envelope the reflection harness uses.
    """
    raw = "5\n7"

    assert GradingService.prepare_stdin(question, "cpp", raw) == raw
    assert GradingService.prepare_stdin(question, "c", raw) == raw


@pytest.mark.django_db
def test_no_v2_wrapper_exists_for_the_self_contained_languages(question):
    """
    Not an oversight — the absence IS the contract. A wrapper appearing here
    would silently switch C/C++ to the reflection model.
    """
    for key in ("cpp", "c"):
        assert execution_contract.V2_WRAPPERS.get(key) is None


@pytest.mark.django_db
def test_a_v2_question_also_passes_cpp_through(db, question):
    """The model is per LANGUAGE, not per contract version."""
    Question.objects.filter(pk=question.pk).update(
        execution_contract_version="v2")
    question.refresh_from_db()

    executable, _ = GradingService._build_executable(
        question, "cpp", CPP_COMPLETE)

    assert executable == CPP_COMPLETE


# ═════════════════════════════════════════════════════════════
# Judge0 mapping
# ═════════════════════════════════════════════════════════════

def test_the_judge0_ids_are_the_registered_ones():
    assert languages.judge0_id("cpp") == 54
    assert languages.judge0_id("c") == 50
    assert languages.judge0_id("c++") == 54      # alias


def test_the_self_contained_set_is_exactly_c_and_cpp():
    self_contained = {lang.key for lang in languages.REGISTRY
                      if lang.self_contained}

    assert self_contained == {"c", "cpp"}


def test_every_restatement_of_the_self_contained_set_agrees():
    """
    Three modules kept their own copy of "which languages are self-contained".
    They agree today; a test is what stops them drifting, since the one that
    drifts is the one deciding whether a learner's program gets a main().
    """
    from groups import contract_reconciliation
    from groups.management.commands import reseed_questions

    registry = {lang.key for lang in languages.REGISTRY if lang.self_contained}

    for restatement in (contract_reconciliation.SELF_CONTAINED_LANGUAGES,
                        reseed_questions.Command.SELF_CONTAINED):
        # Restatements include the "c++" alias; canonicalise before comparing.
        assert {languages.canonical(key) for key in restatement} == registry


# ═════════════════════════════════════════════════════════════
# Readiness follows the contract
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language,source", [
    ("cpp", CPP_COMPLETE), ("c", C_COMPLETE)])
def test_a_complete_program_is_ready(language, source):
    assert language_readiness.assess_source(source, language).verdict == \
        language_readiness.READY


@pytest.mark.parametrize("language", ["cpp", "c"])
def test_a_solution_class_is_not_ready_and_says_why(language):
    result = language_readiness.assess_source(CPP_SOLUTION_CLASS, language)

    assert result.verdict == language_readiness.NOT_READY
    assert result.cause == language_readiness.NO_ENTRY_POINT
    assert "main()" in result.reason


@pytest.mark.parametrize("language", ["cpp", "c"])
def test_a_missing_starter_is_a_DIFFERENT_cause(language):
    """
    The distinction the repair worklist rests on: 1,150 C++ questions need
    content authored, 638 need an existing starter replaced. Reporting both
    as one number hid that they are different jobs.
    """
    result = language_readiness.assess_source("", language)

    assert result.cause == language_readiness.NO_STARTER


# ═════════════════════════════════════════════════════════════
# The generator that produced the broken bank
# ═════════════════════════════════════════════════════════════

def test_the_stub_generator_no_longer_asks_for_a_solution_class_in_cpp():
    """
    `generate_starter_stubs` said "using a Solution class for the
    object-oriented languages (java/cpp/javascript)". That sentence is where
    638 unlinkable starters came from.
    """
    source = inspect.getsource(ai_services.generate_starter_stubs)
    # Comments stripped: the function documents the old defect by quoting it,
    # and a raw substring search cannot tell an explanation from a directive.
    # The M2 provenance test hit exactly this and was fixed the same way.
    instructions = "\n".join(line for line in source.splitlines()
                             if not line.lstrip().startswith("#"))

    assert "Solution class for" not in instructions
    assert "SELF-CONTAINED" in instructions
    assert "is_self_contained" in instructions


def test_the_stub_generator_accepts_a_correct_self_contained_stub():
    """
    The other half of the defect: the old validator required "Solution" for
    everything but C, so a correct C++ program with main() was DISCARDED.
    """
    source = inspect.getsource(ai_services.generate_starter_stubs)

    # The marker each model requires is chosen by execution model, not by a
    # hard-coded language name.
    assert 'lang == "c"' not in source
    assert "main" in source


def test_both_generators_describe_the_same_contract():
    """
    One contract, two prompts. They disagreed, and the bank is the evidence.
    """
    full = inspect.getsource(ai_services.generate_full_question)
    stubs = inspect.getsource(ai_services.generate_starter_stubs)

    for phrase in ("SELF-CONTAINED", "main()", "no entry point"):
        assert phrase in full, phrase
    for phrase in ("SELF-CONTAINED", "main()", "no entry point"):
        assert phrase in stubs, phrase


def test_the_reseed_validator_still_rejects_a_cpp_stub_without_main():
    """Unchanged by M4, and load-bearing: the newer path was already correct."""
    from groups.management.commands import reseed_questions

    command = reseed_questions.Command()
    problem = command._validate_starter_code({"cpp": CPP_SOLUTION_CLASS})

    assert problem and "main()" in problem


def test_the_reseed_validator_accepts_a_complete_program():
    from groups.management.commands import reseed_questions

    command = reseed_questions.Command()

    assert command._validate_starter_code({"cpp": CPP_COMPLETE}) is None


# ═════════════════════════════════════════════════════════════
# Malformed input
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_an_empty_cpp_submission_is_passed_through_not_repaired(question):
    """
    The platform does not invent a program. An empty submission reaches
    Judge0 as empty and fails to compile there, which is the honest outcome —
    silently supplying a main() would grade code the learner never wrote.
    """
    executable, _ = GradingService._build_executable(question, "cpp", "")

    assert executable == ""


@pytest.mark.django_db
def test_a_syntactically_invalid_cpp_submission_is_not_rejected_locally(
        question):
    """
    Compilation is Judge0's job. The platform must not pre-judge C++ syntax:
    it has no compiler, and a wrong local verdict would be worse than none.
    """
    broken = "int main( {"

    executable, _ = GradingService._build_executable(question, "cpp", broken)

    assert executable == broken
