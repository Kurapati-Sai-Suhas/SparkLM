"""
Which Python interpreter Judge0 runs, and that selecting it changes nothing
else (M2 P2.7h-12).

Judge0 CE 71 is Python 3.8.1, which rejects PEP 585 subscripted generics:
`list[list[str]]` raises `TypeError: 'type' object is not subscriptable` at
class-definition time. 772 of 2,926 Python starters use that syntax, so their
reference solutions could not run at all — and a mutation quality gate scored a
PASS out of thirteen crashes, because a crash counts as a kill.

The fix is a SELECTION, not an upgrade: the same Judge0 deployment already
serves 3.11.2 as id 92. These tests hold the selection, its configurability,
and the boundaries it must not cross.

Hermetic — no network. The live equivalence evidence (q3309 12/12 identical
under both interpreters, q1436 13/13 correct under 3.11) lives in the phase
document; what is asserted here is everything checkable without Judge0.
"""

import importlib
import os

import pytest

from common import languages


def reload_registry(monkeypatch, value):
    """Re-import the registry with a given env value, then restore it."""
    if value is None:
        monkeypatch.delenv("JUDGE0_PYTHON_LANGUAGE_ID", raising=False)
    else:
        monkeypatch.setenv("JUDGE0_PYTHON_LANGUAGE_ID", value)
    return importlib.reload(languages)


#: Every module-level name another module may have captured with
#: `from common.languages import X`. Identity matters for all of them.
_EXPORTS = ("REGISTRY", "ACCEPTED_SPELLINGS", "CANONICAL_KEYS", "LANGUAGE_IDS",
            "SELF_CONTAINED", "_BY_SPELLING")


@pytest.fixture(autouse=True)
def restore_registry():
    """
    Whatever a test does to the module, put the real one back.

    The variable is cleared here rather than relying on `monkeypatch`:
    fixture teardown order is not guaranteed to run monkeypatch first, and a
    reload while a malformed value is still set would leave every later test
    importing a module that raises.

    Restoring the VALUES is not enough. `importlib.reload` rebinds every
    module-level name to a brand-new object, while a module that did
    `from common.languages import LANGUAGE_IDS` still holds the old one — so
    a reload leaves two equal-but-distinct registries in the same process.
    That is the exact duplicate-mapping split
    `test_judge0_language_ids_come_from_the_one_registry` exists to forbid,
    and it made that test fail for every test running after this file.
    The original objects are therefore put back by identity, with the dicts
    refilled in place rather than replaced.
    """
    originals = {name: getattr(languages, name) for name in _EXPORTS}
    yield
    os.environ.pop("JUDGE0_PYTHON_LANGUAGE_ID", None)
    importlib.reload(languages)
    for name, original in originals.items():
        if isinstance(original, dict):
            original.clear()
            original.update(getattr(languages, name))
        setattr(languages, name, original)


# ═════════════════════════════════════════════════════════════
# 1. Python resolves to 92
# ═════════════════════════════════════════════════════════════

def test_python_resolves_to_the_pep585_capable_runtime():
    assert languages.judge0_id("python") == 92
    assert languages.LANGUAGE_IDS["python"] == 92


def test_the_default_is_used_when_the_environment_says_nothing(monkeypatch):
    module = reload_registry(monkeypatch, None)
    assert module.judge0_id("python") == 92


def test_the_runtime_is_configurable(monkeypatch):
    """A rollback must be an environment variable, not a deployment."""
    module = reload_registry(monkeypatch, "71")
    assert module.judge0_id("python") == 71
    assert module.LANGUAGE_IDS["python"] == 71


def test_a_blank_override_falls_back_to_the_default(monkeypatch):
    module = reload_registry(monkeypatch, "   ")
    assert module.judge0_id("python") == 92


def test_a_malformed_override_refuses_loudly(monkeypatch):
    """
    Never silently run on a different interpreter than the operator asked
    for: the whole defect this fixes was an interpreter nobody had checked.
    """
    with pytest.raises(ValueError, match="JUDGE0_PYTHON_LANGUAGE_ID"):
        reload_registry(monkeypatch, "3.11")


# ═════════════════════════════════════════════════════════════
# 7/9. Fail closed; other languages untouched
# ═════════════════════════════════════════════════════════════

def test_an_unknown_language_still_fails_closed():
    assert languages.judge0_id("cobol") is None
    assert languages.get("cobol") is None
    assert "cobol" not in languages.LANGUAGE_IDS


@pytest.mark.parametrize("name,expected", [
    ("java", 62), ("cpp", 54), ("c++", 54), ("c", 50),
    ("javascript", 63), ("js", 63),
])
def test_no_other_language_moved(name, expected):
    assert languages.judge0_id(name) == expected


def test_only_python_is_configurable(monkeypatch):
    """The override must not be a lever on every language at once."""
    module = reload_registry(monkeypatch, "100")
    assert module.judge0_id("python") == 100
    assert module.judge0_id("java") == 62
    assert module.judge0_id("javascript") == 63


def test_the_registry_still_has_one_entry_per_spelling():
    """The duplicate-spelling guard must survive a dynamic id."""
    spellings = [s for lang in languages.REGISTRY for s in lang.spellings]
    assert len(spellings) == len(set(spellings))


# ═════════════════════════════════════════════════════════════
# 8. The execution contract is not coupled to the runtime
# ═════════════════════════════════════════════════════════════

def test_the_execution_contract_does_not_read_the_language_id():
    """
    Contract versions govern how arguments are BOUND, not which interpreter
    runs. If the contract ever started reading the id, changing the runtime
    would silently change grading semantics.
    """
    import inspect

    from groups import execution_contract

    source = inspect.getsource(execution_contract)
    assert "judge0_id" not in source
    assert "language_id" not in source
    assert "LANGUAGE_IDS" not in source


def test_known_contract_versions_are_unchanged():
    from groups import execution_contract

    assert set(execution_contract.KNOWN_CONTRACTS) == {"v1", "v2", "v3"}


# ═════════════════════════════════════════════════════════════
# Every execution path resolves through the one registry
# ═════════════════════════════════════════════════════════════

def test_no_module_hardcodes_a_python_language_id():
    """
    The reason one line fixes learner submissions, the oracle, the quality
    gate and hidden-test reconciliation at once is that they all resolve
    through LANGUAGE_IDS. A hardcoded id anywhere would leave a path behind.
    """
    import pathlib
    import re

    root = pathlib.Path(languages.__file__).resolve().parents[1]
    pattern = re.compile(r"language_id\s*[=:]\s*\d+")
    offenders = []
    for path in list(root.glob("groups/**/*.py")) + list(root.glob("common/**/*.py")):
        if "test" in path.name or path.name == "languages.py":
            continue
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"hardcoded Judge0 language id: {offenders}"


def test_the_judge0_runner_resolves_the_id_from_the_registry():
    import inspect

    from groups import coding_views

    source = inspect.getsource(coding_views._run_on_judge0)
    assert "LANGUAGE_IDS.get(language.lower())" in source


# ═════════════════════════════════════════════════════════════
# Provenance records WHICH interpreter produced the evidence
# ═════════════════════════════════════════════════════════════

def test_oracle_execution_records_the_runtime(monkeypatch):
    """
    `language` says "python" and nothing more, so 3.8 evidence was
    indistinguishable from 3.11 evidence in the audit trail.
    """
    from groups.management.commands import oracle_execute

    command = oracle_execute.Command()
    monkeypatch.setattr(command, "_runtime_description",
                        lambda: "Python (3.11.2)")

    import inspect
    source = inspect.getsource(oracle_execute.Command.handle)
    assert '"judge0_language_id": languages.judge0_id("python")' in source
    assert '"runtime": self._runtime_description()' in source


def test_an_unreachable_judge0_records_no_runtime_rather_than_a_guess(
        monkeypatch):
    """An unknown runtime is a fact; an invented one is not."""
    from groups.management.commands import oracle_execute

    monkeypatch.delenv("JUDGE0_API_HOST", raising=False)
    monkeypatch.delenv("JUDGE0_API_KEY", raising=False)
    assert oracle_execute.Command()._runtime_description() is None


def test_the_artifact_digest_does_not_read_the_executor():
    """
    Enriching provenance must not invalidate q3309's approval. The digest
    frames are the definition of what an approval covers.
    """
    import inspect

    from groups import question_artifact

    frames = inspect.getsource(question_artifact.QuestionArtifact._frames)
    assert "executor" not in frames
    assert "runtime" not in frames
    assert "language_id" not in frames
