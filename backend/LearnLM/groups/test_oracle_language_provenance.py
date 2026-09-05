"""
Oracle provenance records the language actually executed (Phase 1 M2).

The defect: `oracle_execute` stamped every provenance row with
`judge0_id("python")`. Execution itself was always correct — the runner
dispatches on `reference.language` — so a Java or C++ reference would have
run as Java or C++ and then recorded evidence claiming a Python interpreter
produced it.

Nothing downstream could have caught that. The artifact digest does not read
`executor`, and the rows are immutable after insert, so a wrong id would have
been permanent and unfalsifiable. It was latent only because all eight
production references happen to be Python.

These tests pin the id to the reference, per language, rather than to a
constant that happened to be right.
"""

import pytest

from common import languages
from groups.management.commands.oracle_execute import Command


class FakeReference:
    """Only what `_executor_for` reads."""

    def __init__(self, language):
        self.language = language


@pytest.fixture
def command(monkeypatch):
    command = Command()
    # The runtime name is a Judge0 HTTP lookup. Stubbed so these tests assert
    # on the id — the thing this milestone fixes — without a network call.
    monkeypatch.setattr(Command, "_runtime_description",
                        lambda self, language_id: f"runtime-{language_id}")
    return command


def executor_for(command, monkeypatch, language, base=None):
    monkeypatch.setattr(
        "groups.oracle.canonical_reference",
        lambda question: FakeReference(language))
    return command._executor_for(object(), base or {})


# ═════════════════════════════════════════════════════════════
# One test per language the platform registers
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("language", ["python", "java", "cpp", "javascript",
                                      "c"])
def test_provenance_records_the_reference_language(command, monkeypatch,
                                                   language):
    executor = executor_for(command, monkeypatch, language)

    assert executor["reference_language"] == language
    assert executor["judge0_language_id"] == languages.judge0_id(language)


def test_each_language_gets_a_DISTINCT_judge0_id(command, monkeypatch):
    """
    The regression in one assertion. Under the old code every one of these
    was Python's id, so a test checking only "an id is present" would have
    passed against the bug.
    """
    ids = {lang: executor_for(command, monkeypatch, lang)["judge0_language_id"]
           for lang in ("python", "java", "cpp", "javascript", "c")}

    assert len(set(ids.values())) == len(ids), ids


def test_a_java_reference_is_not_recorded_as_python(command, monkeypatch):
    executor = executor_for(command, monkeypatch, "java")

    assert executor["judge0_language_id"] != languages.judge0_id("python")
    assert executor["judge0_language_id"] == 62


def test_a_cpp_reference_is_not_recorded_as_python(command, monkeypatch):
    executor = executor_for(command, monkeypatch, "cpp")

    assert executor["judge0_language_id"] != languages.judge0_id("python")
    assert executor["judge0_language_id"] == 54


def test_javascript_resolves_through_its_alias(command, monkeypatch):
    """The SPA spells it "js"; the registry canonicalises. Both must land on 63."""
    for spelling in ("javascript", "js"):
        executor = executor_for(command, monkeypatch, spelling)
        assert executor["judge0_language_id"] == 63


def test_python_still_records_pythons_id(command, monkeypatch):
    """The fix must not move the case that was already correct."""
    executor = executor_for(command, monkeypatch, "python")

    assert executor["judge0_language_id"] == languages.judge0_id("python")


# ═════════════════════════════════════════════════════════════
# Absent and invalid
# ═════════════════════════════════════════════════════════════

def test_an_unregistered_language_records_a_null_id_not_pythons(
        command, monkeypatch):
    """
    `judge0_id` returns None for an unknown spelling. Recording None is
    correct — it says "unmapped". Falling back to Python's id would restore
    exactly the defect this milestone removes, in the one situation where the
    claim is least defensible.

    The run still refuses: `oracle_pipeline` blocks a reference whose language
    has no executor mapping before anything executes.
    """
    executor = executor_for(command, monkeypatch, "rust")

    assert executor["judge0_language_id"] is None
    assert executor["reference_language"] == "rust"


def test_no_canonical_reference_omits_the_language_keys(command, monkeypatch):
    """
    An absent key cannot be mistaken for a claim; a wrong key can. When no
    single canonical reference exists, `run_question` reports the blocker and
    records nothing, so there is nothing to describe.
    """
    monkeypatch.setattr("groups.oracle.canonical_reference",
                        lambda question: None)

    executor = command._executor_for(object(), {"limits": {"cpu": 5}})

    assert "judge0_language_id" not in executor
    assert "reference_language" not in executor
    assert executor["limits"] == {"cpu": 5}      # base survives


def test_the_language_independent_base_is_preserved(command, monkeypatch):
    base = {"limits": {"cpu": 5}, "operator": "Suhas"}

    executor = executor_for(command, monkeypatch, "java", base)

    assert executor["limits"] == {"cpu": 5}
    assert executor["operator"] == "Suhas"


def test_the_base_dict_is_not_mutated(command, monkeypatch):
    """It is shared across every question in the batch."""
    base = {"limits": {"cpu": 5}}

    executor_for(command, monkeypatch, "java", base)

    assert base == {"limits": {"cpu": 5}}


# ═════════════════════════════════════════════════════════════
# The runtime lookup
# ═════════════════════════════════════════════════════════════

def test_the_runtime_name_is_looked_up_for_the_actual_language(
        command, monkeypatch):
    executor = executor_for(command, monkeypatch, "cpp")

    assert executor["runtime"] == f"runtime-{languages.judge0_id('cpp')}"


def test_the_runtime_lookup_is_memoised_per_language():
    """
    Judge0 is the service most likely to be rate-limiting when the oracle
    runs. Asking it once per question for a batch sharing one language is the
    behaviour that made a 429 worse.
    """
    command = Command()
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        raise RuntimeError("judge0 unavailable")

    import requests
    original = requests.get
    requests.get = fake_get
    try:
        import os
        os.environ.setdefault("JUDGE0_API_HOST", "example.invalid")
        os.environ.setdefault("JUDGE0_API_KEY", "k")
        for _ in range(4):
            command._runtime_description(62)
    finally:
        requests.get = original

    assert len(calls) <= 1, calls


def test_a_failed_lookup_records_nothing_rather_than_guessing(command,
                                                              monkeypatch):
    monkeypatch.setattr(Command, "_runtime_description",
                        lambda self, language_id: None)

    executor = executor_for(command, monkeypatch, "java")

    assert "runtime" not in executor
    assert executor["judge0_language_id"] == 62   # the id is still recorded


# ═════════════════════════════════════════════════════════════
# The hard-code is gone
# ═════════════════════════════════════════════════════════════

def test_the_command_no_longer_hard_codes_python():
    """
    Checked on the AST, not the text. The prose in this module and in the
    command both quote the old expression while explaining why it was wrong,
    and a substring search cannot tell an explanation from a call — the first
    version of this test failed on its own docstring.
    """
    import ast
    import inspect

    from groups.management.commands import oracle_execute

    tree = ast.parse(inspect.getsource(oracle_execute))

    hard_coded = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "judge0_id"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]

    assert hard_coded == [], [n.args[0].value for n in hard_coded]


def test_execution_dispatch_still_uses_the_reference_language():
    """
    M2 fixes the RECORD, not the run. Execution was already language-correct
    and must stay that way — `oracle.execute_reference` passes
    `reference.language` to the runner.
    """
    import inspect

    from groups import oracle

    source = inspect.getsource(oracle)

    assert "reference.language" in source
