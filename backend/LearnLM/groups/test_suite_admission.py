"""
Contract-specific admission of new hidden test cases (Phase 1 M17).

`expand_hidden_tests` promised every addition "must bind under the question's
declared contract" and then checked only v3. These tests hold the validator
that replaced that early return:

  * each contract is judged by ITS OWN binding rule — the same stdin is
    accepted by one contract and refused by another, and the tests prove the
    validator tells them apart;
  * the v1 mirror is pinned against the REAL frozen templates, executed
    locally (Python always; JavaScript and Java where a runtime exists);
  * v2 structural cases are held to construction, arity, canonical output
    and a wired adapter per language;
  * whatever nothing models is refused, never waved through;
  * the command refuses before writing.

No Judge0, no production. Local subprocesses and the local test database.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import execution_contract, language_readiness, pre_image
from groups import suite_admission as sa
from groups.models import CodingPortal, Question, RemediationBatch, Topic
from groups.services import (
    GENERIC_JAVA_WRAPPER, GENERIC_JS_WRAPPER, GENERIC_PYTHON_WRAPPER,
)

NODE = shutil.which("node")
JAVAC, JAVA = shutil.which("javac"), shutil.which("java")

LIST = ("class Solution:\n"
        "    def total(self, nums: list[int]) -> int:\n"
        "        pass\n")
PAIR = ("class Solution:\n"
        "    def add(self, a: int, b: int) -> int:\n"
        "        pass\n")
TREE = ("class Solution:\n"
        "    def isSameTree(self, p: TreeNode | None, "
        "q: TreeNode | None) -> bool:\n"
        "        pass\n")
MIRROR = ("class Solution:\n"
          "    def mirror(self, root: Optional[TreeNode]) -> Optional[TreeNode]:\n"
          "        pass\n")
TEXT = ("class Solution:\n"
        "    def first(self, s: str) -> str:\n"
        "        pass\n")


def question(starter, version="v1", wrappers=None, **starters):
    """A stand-in carrying only what admission reads. Never saved."""
    return SimpleNamespace(
        pk=1, execution_contract_version=version,
        boilerplate_code={"python": starter, **starters},
        hidden_wrapper_code=wrappers or {})


def verdicts(q, stdin, expected="1"):
    return {check.language: check for check in
            sa.check_case(q, {"stdin": stdin, "expected_output": expected})}


def refused(q, stdin, expected="1"):
    return {lang for lang, check in verdicts(q, stdin, expected).items()
            if check.refused}


REFLECTION = {"python", "javascript", "java"}


# ═════════════════════════════════════════════════════════════
# The three contracts are NOT the same check
# ═════════════════════════════════════════════════════════════

def test_one_stdin_three_contracts_three_answers():
    """
    `[1,2,3]` for a single `list[int]`: v3's envelope reads one list; v1
    splats a top-level array into THREE arguments; v2 reads one
    whitespace-token, the string "[1,2,3]". Reusing any one contract's check
    for another gets at least one of these wrong.
    """
    assert refused(question(LIST, "v3"), "[1,2,3]", "6") == set()
    assert {"python", "javascript"} <= refused(question(LIST, "v1"),
                                               "[1,2,3]", "6")
    assert refused(question(LIST, "v2"), "[1,2,3]", "6") == REFLECTION


def test_each_contract_accepts_its_own_spelling():
    assert refused(question(LIST, "v1"), "[[1,2,3]]", "6") == set()
    assert refused(question(LIST, "v2"), "1 2 3", "6") == set()
    assert refused(question(LIST, "v3"), "[1,2,3]", "6") == set()


# ═════════════════════════════════════════════════════════════
# v1 — the mirror, pinned against the real templates
# ═════════════════════════════════════════════════════════════

CORPUS = ["5", "[1,2,3]", "[[1,2,3]]", "[1,2]\n3", "1\n2", "hello",
          "hello\nworld", "", "  [1]  \n", '{"a": 1, "b": 2}', '"text"',
          "true", "null", "[1]\n\n[2]", "1 2 3", "NaN", '["a","b"]',
          "[1,2]\n[3", "[]", "[]\n[]", "3.5", "-0"]

PY_PROBE = ("class Solution:\n"
            "    def probe(self, *args, **kwargs):\n"
            "        return repr((args, kwargs))\n")
JS_PROBE = ("class Solution {\n"
            "    probe(...args) { return JSON.stringify(args); }\n"
            "}\n")
JAVA_PROBE = ("class Solution {\n"
              "    public String probe(String a, String b) {\n"
              "        return (a == null ? \"NULL\" : a) + \"|\" + "
              "(b == null ? \"NULL\" : b);\n"
              "    }\n"
              "}\n")


def execute(command, stdin):
    """
    Run one probe with BYTE-exact stdin. Text mode rewrites every newline as
    CRLF on Windows — invisible to a Python child reading in text mode, fatal
    to a Node child reading raw bytes.
    """
    run = subprocess.run(command, input=stdin.encode("utf-8"),
                         capture_output=True, timeout=120)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    return SimpleNamespace(stdout=run.stdout.decode("utf-8"))


@pytest.mark.parametrize("stdin", CORPUS)
def test_the_v1_python_mirror_matches_the_real_template(stdin):
    source = GENERIC_PYTHON_WRAPPER.replace("{user_code}", PY_PROBE)
    run = execute([sys.executable, "-c", source], stdin)

    mode, arguments = sa.v1_binding(stdin)
    expected = (((), arguments) if mode == sa.KEYWORD
                else (tuple(arguments), {}))
    assert run.stdout.strip() == repr(expected)


@pytest.mark.skipif(NODE is None, reason="no local node; the JS mirror is "
                                         "unpinned on this machine, not passed")
@pytest.mark.parametrize("stdin", CORPUS)
def test_the_v1_javascript_mirror_matches_the_real_template(stdin, tmp_path):
    script = tmp_path / "probe.js"
    script.write_text(GENERIC_JS_WRAPPER.replace("{user_code}", JS_PROBE),
                      encoding="utf-8")
    run = execute([NODE, str(script)], stdin)

    mode, arguments = sa.v1_binding(stdin, strict=True)
    # An object is ONE argument in JavaScript — the divergence admission
    # refuses on. Everything else is the same positional list as Python's.
    expected = [arguments] if mode == sa.KEYWORD else arguments
    assert json.loads(run.stdout) == expected


@pytest.fixture(scope="module")
def java_probe():
    if not (JAVAC and JAVA):
        pytest.skip("no local JDK; the Java line rule is unpinned on this "
                    "machine, not passed")
    workdir = Path(tempfile.mkdtemp())
    (workdir / "Main.java").write_text(
        GENERIC_JAVA_WRAPPER.replace("{user_code}", JAVA_PROBE),
        encoding="utf-8")
    subprocess.run([JAVAC, "Main.java"], cwd=workdir, check=True,
                   capture_output=True, timeout=120)
    yield workdir
    shutil.rmtree(workdir, ignore_errors=True)


@pytest.mark.parametrize("stdin", ["x", "x\ny", "x\ny\nz", "\nx\ny",
                                   "x\n\ny", "  x  \n  y  ", "x\ny\n"])
def test_the_v1_java_line_rule_matches_the_real_template(stdin, java_probe):
    run = execute([JAVA, "-cp", str(java_probe), "Main"], stdin)

    lines = sa._java_lines(stdin)
    bound = [lines[i].strip() if i < len(lines) else "NULL" for i in range(2)]
    assert run.stdout.strip() == "|".join(bound)


# ═════════════════════════════════════════════════════════════
# v1 — admission
# ═════════════════════════════════════════════════════════════

def test_v1_refuses_an_arity_mismatch_in_every_reflection_language():
    """One line for two parameters: Python raises, Java passes null, and
    JavaScript binds `undefined` without a word."""
    checks = verdicts(question(PAIR), "7", "7")
    assert {lang for lang, c in checks.items() if c.refused} == REFLECTION
    assert "silently" in checks["javascript"].detail
    assert "null" in checks["java"].detail


def test_v1_accepts_a_case_every_reflection_harness_binds():
    assert refused(question(PAIR), "3\n4", "7") == set()


def test_v1_java_refuses_a_splat_it_cannot_read():
    """`[3,4]` splats to (3, 4) in Python and JS but is ONE line to Java."""
    assert refused(question(PAIR), "[3,4]", "7") == {"java"}


def test_v1_refuses_an_object_the_harnesses_bind_differently():
    assert refused(question(PAIR), '{"a": 3, "b": 4}', "7") == {
        "javascript", "java"}


def test_v1_refuses_a_value_of_the_wrong_declared_type():
    assert refused(question(PAIR), '"3"\n4', "7") == {"python", "javascript"}


def test_v1_honours_a_default():
    starter = ("class Solution:\n"
               "    def f(self, a: int, b: int = 1) -> int:\n        pass\n")
    assert {"python", "javascript"}.isdisjoint(
        refused(question(starter), "5", "6"))


def test_v1_refuses_every_case_of_a_structural_question():
    """No v1 harness builds a TreeNode, so no case can be executable."""
    checks = verdicts(question(TREE), "[1]\n[1]", "true")
    assert {lang for lang, c in checks.items() if c.refused} == REFLECTION
    assert "migrate it to v2" in checks["python"].detail


def test_v1_refuses_an_answer_the_method_cannot_print():
    starter = ("class Solution:\n"
               "    def ok(self, n: int) -> bool:\n        pass\n")
    assert refused(question(starter), "1", "True") == REFLECTION
    assert refused(question(starter), "1", "true") == set()
    assert refused(question(PAIR), "3\n4", "seven") == REFLECTION


# ═════════════════════════════════════════════════════════════
# v2 — structural and not
# ═════════════════════════════════════════════════════════════

def test_v2_accepts_a_buildable_canonical_structural_case():
    checks = verdicts(question(TREE, "v2"), "[1,2,3]\n[1,2,3]", "true")
    assert {lang for lang, c in checks.items()
            if c.outcome == sa.VALIDATED} == REFLECTION


def test_v2_refuses_a_line_that_does_not_build_its_structure():
    assert refused(question(TREE, "v2"), "[1,{]\n[1]", "true") == REFLECTION


def test_v2_refuses_a_non_canonical_structural_input():
    """Builds the same tree, but a second spelling defeats duplicate
    detection — new data is stored canonically."""
    assert refused(question(TREE, "v2"), "[1,null,2,null,null]\n[1]",
                   "false") == REFLECTION


@pytest.mark.parametrize("stdin", ["[1]", "[1]\n[1]\n[2]"])
def test_v2_refuses_an_arity_mismatch(stdin):
    assert refused(question(TREE, "v2"), stdin, "true") == REFLECTION


def test_v2_tolerates_the_trailing_newline_storage_artifact():
    assert refused(question(TREE, "v2"), "[1]\n[1]\n", "true") == set()


def test_v2_holds_a_structural_answer_to_its_canonical_form():
    assert refused(question(MIRROR, "v2"), "[1,2]", "[1,null,2]") == set()
    assert refused(question(MIRROR, "v2"), "[1,2]",
                   "[1,null,2,null,null]") == REFLECTION
    assert refused(question(MIRROR, "v2"), "[1,2]", "true") == REFLECTION


def test_v2_holds_a_scalar_answer_to_its_printed_form():
    assert refused(question(TREE, "v2"), "[1]\n[1]", "True") == REFLECTION


def test_v2_refuses_a_missing_structural_adapter(monkeypatch):
    """A language with a v2 harness but no adapter would receive the raw
    array: refused, by name, for that language only."""
    monkeypatch.setattr(language_readiness, "STRUCTURAL_ADAPTERS",
                        frozenset({"python", "java"}))
    checks = verdicts(question(TREE, "v2"), "[1]\n[1]", "true")
    assert {lang for lang, c in checks.items() if c.refused} == {"javascript"}
    assert "adapter" in checks["javascript"].detail


def test_v2_refuses_a_language_with_no_v2_harness(monkeypatch):
    wrappers = dict(execution_contract.V2_WRAPPERS)
    del wrappers["java"]
    monkeypatch.setattr(execution_contract, "V2_WRAPPERS", wrappers)
    assert refused(question(PAIR, "v2"), "3\n4", "7") == {"java"}


def test_v2_refuses_a_structure_no_contract_builds():
    starter = ("class Solution:\n"
               "    def f(self, root: 'Node') -> int:\n        pass\n")
    assert refused(question(starter, "v2"), "[1]", "1") == REFLECTION


def test_v2_token_rule_for_plain_parameters():
    assert refused(question(PAIR, "v2"), "3\n4", "7") == set()
    assert refused(question(PAIR, "v2"), "3", "7") == REFLECTION       # arity
    assert refused(question(PAIR, "v2"), "3 5\n4", "7") == REFLECTION  # scalar
    assert refused(question(TEXT, "v2"), "123", "1") == REFLECTION     # int, not str
    assert refused(question(TEXT, "v2"), "abc", "a") == set()


# ═════════════════════════════════════════════════════════════
# v3 preserved, C/C++ raw, and nothing unmodelled waved through
# ═════════════════════════════════════════════════════════════

def test_v3_is_python_only_and_unchanged():
    checks = verdicts(question(LIST, "v3"), "[1,2,3]", "6")
    assert checks["python"].outcome == sa.VALIDATED
    assert {lang for lang, c in checks.items()
            if c.outcome == sa.NOT_DEFINED} == {"javascript", "java", "cpp",
                                                 "c"}
    guessy = question("class Solution:\n    def f(self, xs):\n        pass\n",
                      "v3")
    assert "binds only by guessing" in verdicts(guessy, "[1,2]")["python"].detail


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_self_contained_languages_get_their_own_contract(version):
    """No reflection wrapper is forced on a program that reads stdin itself —
    even for a case every reflection harness refuses."""
    checks = verdicts(question(PAIR, version), "anything at all", "7")
    assert checks["cpp"].outcome == sa.RAW_STDIN
    assert checks["c"].outcome == sa.RAW_STDIN


def test_a_question_s_own_wrapper_is_refused_not_trusted():
    checks = verdicts(question(PAIR, wrappers={"python": "{user_code}\n"}),
                      "3\n4", "7")
    assert checks["python"].refused and "own wrapper" in checks["python"].detail
    assert not checks["javascript"].refused


@pytest.mark.parametrize("case", [
    {"stdin": "3\n4", "expected_output": 7},
    {"stdin": ["3", "4"], "expected_output": "7"},
    {"expected_output": "7"},
])
def test_a_non_text_field_is_refused_in_every_language(case):
    """93 stored production cases hold a JSON number as the answer key."""
    checks = sa.check_case(question(PAIR), case)
    assert all(check.refused for check in checks)


def test_an_unknown_contract_refuses_every_language():
    assert refused(question(PAIR, "v9"), "3\n4", "7") == {
        "python", "javascript", "java", "cpp", "c"}


@pytest.mark.parametrize("starter", [
    "class Solution:\n    def f(self, n: int -> int: pass\n",
    "def f(n: int) -> int:\n    pass\n",
    "class Solution:\n    def _private(self, n: int) -> int: pass\n",
    "",
])
def test_no_declared_signature_is_a_refusal(starter):
    assert refused(question(starter), "1", "1") == REFLECTION


# ═════════════════════════════════════════════════════════════
# The command refuses before it writes
# ═════════════════════════════════════════════════════════════

User = get_user_model()


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="adm-op", password="pw",
                                    email="adm@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="Admission Portal")
    made, _ = Topic.objects.get_or_create(
        name="AdmissionTopic",
        defaults={"structure_type": "flat", "portal": portal})
    return made


def frozen(operator, row, key):
    batch = RemediationBatch.objects.create(batch_key=key, purpose="test",
                                            created_by=operator)
    pre_image.capture(batch, row, operator)
    pre_image.freeze(batch, operator)
    return batch


def expand(tmp_path, operator, row, batch, additions):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"question": row.pk, "labels": {},
                                "additions": additions}), encoding="utf-8")
    call_command("expand_hidden_tests", "--batch", batch.batch_key,
                 "--question", str(row.pk), "--plan", str(plan),
                 "--reason", "admission test", "--operator",
                 operator.username, "--local", "--apply", "--confirm")


def make(topic, pk, starter, version, cases):
    return Question.objects.create(
        id=pk, title=f"Q{pk}", content="Statement.", topic=topic,
        base_difficulty=1200.0, boilerplate_code={"python": starter},
        hidden_test_cases=cases, hidden_wrapper_code={},
        execution_contract_version=version)


@pytest.mark.django_db
def test_the_command_refuses_a_v1_addition_and_writes_nothing(
        topic, operator, tmp_path):
    row = make(topic, 9800, PAIR, "v1",
               [{"stdin": "1\n2", "expected_output": "3"}])
    batch = frozen(operator, row, "adm-v1")

    with pytest.raises(CommandError, match="not executable under v1"):
        expand(tmp_path, operator, row, batch,
               [{"stdin": "7", "expected_output": "7", "category": "one"}])

    row.refresh_from_db()
    assert row.hidden_test_cases == [{"stdin": "1\n2", "expected_output": "3"}]


@pytest.mark.django_db
def test_the_command_accepts_a_v1_addition_every_harness_binds(
        topic, operator, tmp_path):
    row = make(topic, 9801, PAIR, "v1",
               [{"stdin": "1\n2", "expected_output": "3"}])
    batch = frozen(operator, row, "adm-v1-ok")

    expand(tmp_path, operator, row, batch,
           [{"stdin": "3\n4", "expected_output": "7", "category": "typical"}])

    row.refresh_from_db()
    assert len(row.hidden_test_cases) == 2


@pytest.mark.django_db
def test_the_command_refuses_a_v2_structural_addition_that_does_not_build(
        topic, operator, tmp_path):
    row = make(topic, 9802, TREE, "v2",
               [{"stdin": "[1]\n[1]", "expected_output": "true"}])
    batch = frozen(operator, row, "adm-v2")

    with pytest.raises(CommandError, match="does not build"):
        expand(tmp_path, operator, row, batch,
               [{"stdin": "[1,{]\n[1]", "expected_output": "false",
                 "category": "broken"}])

    row.refresh_from_db()
    assert len(row.hidden_test_cases) == 1


@pytest.mark.django_db
def test_the_command_refuses_to_grow_a_v1_structural_suite(topic, operator,
                                                           tmp_path):
    """q100's situation: its cases are only executable once it is v2."""
    row = make(topic, 9803, TREE, "v1",
               [{"stdin": "[1]\n[1]", "expected_output": "true"}])
    batch = frozen(operator, row, "adm-v1-tree")

    with pytest.raises(CommandError, match="migrate it to v2 first"):
        expand(tmp_path, operator, row, batch,
               [{"stdin": "[]\n[]", "expected_output": "true",
                 "category": "empty_input"}])
