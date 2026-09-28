"""
Representation faithfulness in hidden-test admission (Phase 1 M17.1).

The v3 adapter, and the v2 line check, admitted inputs whose stored
REPRESENTATION survives into the argument: `'hit'` reached the method as the
five characters `'hit'`, and `['hot','dot']` as junk. A correct solution then
failed on 81 questions (285 cases) that every gate had passed.

The central test here does not trust the validator's own account. For each
row it runs the REAL harness — `_build_executable` + `prepare_stdin`, executed
locally — with a probe that returns exactly what it was given, and holds one
property:

    admitted  <=>  the learner receives the intended value

Admitted rows must deliver the intended value; refused rows must be ones that
would not. So a refusal can never be a guess, and an admission can never be
a mis-binding.

No Judge0, no production. Local subprocesses and the local test database.
"""

import ast
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import execution_adapter as adapter
from groups import pre_image
from groups import suite_admission as sa
from groups.management.commands import remediate_contract
from groups.models import CodingPortal, Question, RemediationBatch, Topic
from groups.services import ExecutionContractError, GradingService

TEXT = ("class Solution:\n"
        "    def first(self, s: str) -> str:\n"
        "        pass\n")
WORDS = ("class Solution:\n"
         "    def first(self, words: list[str]) -> str:\n"
         "        pass\n")
GRID = ("class Solution:\n"
        "    def first(self, grid: list[list[int]]) -> str:\n"
        "        pass\n")
PAIR = ("class Solution:\n"
        "    def first(self, a: str, b: str) -> str:\n"
        "        pass\n")

#: What a correct learner writes for each starter — same signature — except
#: that it returns a faithful record of what it was handed.
PROBES = {
    TEXT: ("class Solution:\n"
           "    def first(self, s: str) -> str:\n"
           "        return repr(s)\n"),
    WORDS: ("class Solution:\n"
            "    def first(self, words: list[str]) -> str:\n"
            "        return repr(words)\n"),
    GRID: ("class Solution:\n"
           "    def first(self, grid: list[list[int]]) -> str:\n"
           "        return repr(grid)\n"),
    PAIR: ("class Solution:\n"
           "    def first(self, a: str, b: str) -> str:\n"
           "        return repr((a, b))\n"),
}

HOT_DOT = ["hot", "dot"]

#: (contract, starter, stored stdin, the value the case MEANS, admitted?)
ROWS = [
    # quoted str vs JSON str vs bare text
    ("v1", TEXT, '"hello"', "hello", True),
    ("v1", TEXT, "'hello'", "hello", False),
    ("v1", TEXT, "hello", "hello", True),
    ("v2", TEXT, "hello", "hello", True),
    ("v2", TEXT, '"hello"', "hello", False),
    ("v2", TEXT, "'hello'", "hello", False),
    ("v3", TEXT, "hello", "hello", True),
    ("v3", TEXT, '"hello"', "hello", False),
    ("v3", TEXT, "'hello'", "hello", False),
    # numeric strings
    ("v1", TEXT, '"123"', "123", True),
    ("v1", TEXT, "123", "123", False),
    ("v2", TEXT, "123", "123", False),
    ("v3", TEXT, "123", "123", True),
    # strings containing spaces
    ("v1", TEXT, '"a b"', "a b", True),
    ("v2", TEXT, "a b", "a b", False),
    ("v3", TEXT, "a b", "a b", True),
    # surrounding whitespace is content, not padding
    ("v1", TEXT, '"  hi  "', "  hi  ", True),
    ("v2", TEXT, "  hi  ", "  hi  ", False),
    ("v3", TEXT, "  hi  ", "  hi  ", True),
    # empty strings
    ("v1", TEXT, '""', "", True),
    ("v2", TEXT, "", "", False),
    ("v3", TEXT, "", "", True),
    # escaped quotes
    ("v1", TEXT, '"a\\"b"', 'a"b', True),
    ("v2", TEXT, 'a"b', 'a"b', True),
    ("v3", TEXT, 'a"b', 'a"b', True),
    ("v3", TEXT, '"a\\"b"', 'a"b', False),
    # quoted list[str] vs JSON list[str]
    ("v1", WORDS, '[["hot","dot"]]', HOT_DOT, True),
    ("v1", WORDS, '["hot","dot"]', HOT_DOT, False),
    ("v1", WORDS, "['hot','dot']", HOT_DOT, False),
    ("v2", WORDS, "hot dot", HOT_DOT, True),
    ("v2", WORDS, '["hot","dot"]', HOT_DOT, False),
    ("v2", WORDS, "['hot','dot']", HOT_DOT, False),
    ("v3", WORDS, '["hot","dot"]', HOT_DOT, True),
    ("v3", WORDS, "hot dot", HOT_DOT, True),
    ("v3", WORDS, "['hot','dot']", HOT_DOT, False),
    ("v3", WORDS, '"hot" "dot"', HOT_DOT, False),
    # empty strings inside a list survive
    ("v1", WORDS, '[["","a"]]', ["", "a"], True),
    ("v3", WORDS, '["","a"]', ["", "a"], True),
    # the declared element type
    ("v1", WORDS, "[[1,2]]", ["1", "2"], False),
    ("v3", WORDS, "[1,2]", ["1", "2"], False),
    # nested lists, where the contract can express them
    ("v1", GRID, "[[[1,2],[3]]]", [[1, 2], [3]], True),
    ("v3", GRID, "[[1,2],[3]]", [[1, 2], [3]], True),
    ("v2", GRID, "[[1,2],[3]]", [[1, 2], [3]], False),
    # malformed JSON
    ("v1", WORDS, '["a","b"', ["a", "b"], False),
    ("v2", WORDS, '["a","b"', ["a", "b"], False),
    ("v3", WORDS, '["a","b"', ["a", "b"], False),
    # a stray trailing comma: Python reads a one-element tuple (q957)
    ("v1", TEXT, '"0110101",', "0110101", False),
    ("v2", TEXT, '"0110101",', "0110101", False),
    ("v3", TEXT, '"0110101",', "0110101", False),
    # one pair of quotes wrapped around every line (q97)
    ("v1", PAIR, '"abc"\n"def"', ("abc", "def"), True),
    ("v1", PAIR, '"abc\ndef"', ("abc", "def"), False),
    ("v2", PAIR, "abc\ndef", ("abc", "def"), True),
    ("v2", PAIR, '"abc\ndef"', ("abc", "def"), False),
    ("v3", PAIR, "abc\ndef", ("abc", "def"), True),
    ("v3", PAIR, '"abc\ndef"', ("abc", "def"), False),
]


def standin(starter, contract):
    """Only what admission and the grading seam read. Never saved."""
    return SimpleNamespace(pk=940_127, execution_contract_version=contract,
                           boilerplate_code={"python": starter},
                           hidden_wrapper_code={})


#: The probe's result when the grader itself refuses to run the case.
GRADER_REFUSED = object()
#: The probe's result when the harness crashed before returning anything.
CRASHED = object()


def received(question, starter, stdin):
    """What a correct learner's method is actually handed, by running it."""
    executable = GradingService._build_executable(question, "python",
                                                  PROBES[starter])[0]
    try:
        fed = GradingService.prepare_stdin(question, "python", stdin)
    except ExecutionContractError:
        return GRADER_REFUSED
    run = subprocess.run([sys.executable, "-c", executable],
                         input=fed.encode("utf-8"), capture_output=True,
                         timeout=60)
    if run.returncode != 0:
        return CRASHED
    return ast.literal_eval(run.stdout.decode("utf-8").strip())


def python_check(question, stdin):
    return next(check for check in
                sa.check_case(question, {"stdin": stdin, "expected_output": "x"})
                if check.language == "python")


@pytest.mark.parametrize("contract,starter,stdin,intended,admitted", ROWS)
def test_admission_matches_what_the_learner_receives(contract, starter, stdin,
                                                     intended, admitted):
    question = standin(starter, contract)
    check = python_check(question, stdin)
    got = received(question, starter, stdin)

    assert (not check.refused) is admitted, check.detail
    if admitted:
        assert got == intended
    else:
        assert got != intended


def test_a_v3_refusal_is_also_a_grading_refusal():
    """The grader shares the adapter, so v3 never SILENTLY mis-binds either."""
    for stdin in ('"hello"', "'hello'"):
        with pytest.raises(ExecutionContractError, match="quoted literal"):
            GradingService.prepare_stdin(standin(TEXT, "v3"), "python", stdin)


@pytest.mark.parametrize("stdin,expected", [
    ("  hi  ", ["  hi  "]),                 # never stripped
    ("", [""]),                             # an empty string is a value
    ('it\'s "fine"', ['it\'s "fine"']),     # quotes inside text are content
])
def test_v3_keeps_text_byte_for_byte(stdin, expected):
    invocation = adapter.build_invocation(stdin, TEXT)
    assert invocation.ok, invocation.detail
    assert invocation.arguments == expected


def test_v3_keeps_empty_strings_inside_a_list():
    invocation = adapter.build_invocation('["", "a", ""]', WORDS)
    assert invocation.ok and invocation.arguments == [["", "a", ""]]


@pytest.mark.parametrize("annotation,name", [
    ("Optional[ListNode]", "ListNode"),
    ("TreeNode", "TreeNode"),
    ("'Node'", "Node"),
])
def test_v3_refuses_a_declared_structure_instead_of_misreading_it(annotation,
                                                                  name):
    """
    The adapter's substring hints read `ListNode` AS a list, so `[1,2,3]`
    used to bind to a `ListNode` parameter as a plain list. It builds no
    structure, and now says so.
    """
    starter = (f"class Solution:\n    def f(self, head: {annotation}) -> int:\n"
               f"        pass\n")
    invocation = adapter.build_invocation("[1,2,3]", starter)
    assert invocation.outcome == adapter.CONTRACT_MISMATCH
    assert f"declares {name}" in invocation.detail


def test_v3_json_envelope_decodes_quoted_strings():
    """The JSON array envelope is how v3 carries a string that needs quoting."""
    two = "class Solution:\n    def f(self, a: str, b: int) -> str:\n        pass\n"
    invocation = adapter.build_invocation('["\\"quoted\\"", 3]', two)
    assert invocation.ok and invocation.arguments == ['"quoted"', 3]


# ═════════════════════════════════════════════════════════════
# q127 — the regression the census measured
# ═════════════════════════════════════════════════════════════

LADDER = ("class Solution:\n"
          "    def ladderLength(self, beginWord: str, endWord: str, "
          "wordList: list[str]) -> int:\n"
          "        pass\n")
LADDER_SOLUTION = '''from collections import deque

class Solution:
    def ladderLength(self, beginWord: str, endWord: str, wordList: list[str]) -> int:
        words = set(wordList)
        if endWord not in words:
            return 0
        queue, seen = deque([(beginWord, 1)]), {beginWord}
        while queue:
            word, steps = queue.popleft()
            if word == endWord:
                return steps
            for i in range(len(word)):
                for c in "abcdefghijklmnopqrstuvwxyz":
                    nxt = word[:i] + c + word[i + 1:]
                    if nxt in words and nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, steps + 1))
        return 0
'''
#: Public Word Ladder examples plus two small ones; (begin, end, list, answer).
LADDER_CASES = [
    ("hit", "cog", ["hot", "dot", "dog", "lot", "log", "cog"], "5"),
    ("hit", "cog", ["hot", "dot", "dog", "lot", "log"], "0"),
    ("a", "c", ["a", "b", "c"], "2"),
    ("hit", "hot", ["hot"], "2"),
]


def quoted_suite():
    """q127's stored form: Python-quoted strings and a Python-repr list."""
    return [{"stdin": f"'{b}'\n'{e}'\n{w!r}", "expected_output": a}
            for b, e, w, a in LADDER_CASES]


def json_suite():
    """The repair: one JSON value per line — v1's own representation."""
    return [{"stdin": f"{json.dumps(b)}\n{json.dumps(e)}\n{json.dumps(w)}",
             "expected_output": a}
            for b, e, w, a in LADDER_CASES]


def local_runner(source, language, stdin):
    run = subprocess.run([sys.executable, "-c", source],
                         input=stdin.encode("utf-8"), capture_output=True,
                         timeout=60)
    return {"status_id": 3 if run.returncode == 0 else 11,
            "status": "Accepted" if run.returncode == 0 else "Runtime Error",
            "stdout": run.stdout.decode("utf-8"),
            "stderr": run.stderr.decode("utf-8"), "compile_output": ""}


def ladder(cases):
    return SimpleNamespace(pk=127, execution_contract_version="v1",
                           boilerplate_code={"python": LADDER},
                           hidden_wrapper_code={}, hidden_test_cases=cases)


def test_q127_stored_quoted_inputs_fail_a_correct_solution_and_are_refused():
    question = ladder(quoted_suite())
    result = GradingService(local_runner).grade(question, "python",
                                                LADDER_SOLUTION)

    assert result.passed < result.total
    assert all(python_check(question, case["stdin"]).refused
               for case in question.hidden_test_cases)


def test_q127_json_per_line_inputs_pass_4_of_4_and_are_admitted_on_v1():
    question = ladder(json_suite())
    result = GradingService(local_runner).grade(question, "python",
                                                LADDER_SOLUTION)

    assert (result.final_status, result.passed, result.total) == (
        "accepted", 4, 4)
    for case in question.hidden_test_cases:
        checks = sa.check_case(question, case)
        assert all(not c.refused for c in checks), [
            (c.language, c.detail) for c in checks if c.refused]


def test_q127_quoted_form_is_refused_under_every_contract():
    """Not a v1 quirk: no contract receives `'hit'` as hit."""
    case = quoted_suite()[0]
    for contract in ("v1", "v2", "v3"):
        question = SimpleNamespace(
            pk=127, execution_contract_version=contract,
            boilerplate_code={"python": LADDER}, hidden_wrapper_code={})
        assert python_check(question, case["stdin"]).refused, contract


def test_remediate_contract_no_longer_passes_q127_into_v3():
    """
    The gate the census caught: `_assess` returned NO refusals for q127, and
    v3 as stored would score 2/4. It now refuses every quoted case.
    """
    refusals = remediate_contract.Command()._assess(
        {"boilerplate_code": {"python": LADDER}, "hidden_wrapper_code": {},
         "hidden_test_cases": quoted_suite()}, "v3")[1]
    assert len(refusals) == len(LADDER_CASES)
    assert all("quoted literal" in refusal or "Python literal" in refusal
               for refusal in refusals)


User = get_user_model()


@pytest.mark.django_db
def test_expanding_q127_neither_admits_quoted_cases_nor_moves_it_to_v3(tmp_path):
    """
    The command refuses the quoted additions and accepts the JSON ones — and
    whichever happens, the question's contract stays v1. Admission validates;
    it never migrates.
    """
    operator = User.objects.create_user(username="m171-op", password="pw",
                                        email="m171@example.com", is_staff=True)
    portal = CodingPortal.objects.create(name="M171 Portal")
    topic, _ = Topic.objects.get_or_create(
        name="M171Topic", defaults={"structure_type": "flat", "portal": portal})
    row = Question.objects.create(
        id=127, title="Word Ladder", content="Statement.", topic=topic,
        base_difficulty=1300.0, boilerplate_code={"python": LADDER},
        hidden_test_cases=json_suite()[:2], hidden_wrapper_code={},
        execution_contract_version="v1")
    batch = RemediationBatch.objects.create(batch_key="m171-q127",
                                            purpose="test", created_by=operator)
    pre_image.capture(batch, row, operator)
    pre_image.freeze(batch, operator)

    def expand(additions):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"question": 127, "labels": {},
                                    "additions": additions}), encoding="utf-8")
        call_command("expand_hidden_tests", "--batch", "m171-q127",
                     "--question", "127", "--plan", str(plan), "--reason", "t",
                     "--operator", operator.username, "--local", "--apply",
                     "--confirm")

    # Refused for v1's real reason: nothing decodes, so the whole text becomes
    # ONE argument for a three-parameter method.
    with pytest.raises(CommandError, match="not executable under v1.*undecoded"):
        expand([dict(quoted_suite()[2], category="typical")])
    row.refresh_from_db()
    assert (len(row.hidden_test_cases), row.execution_contract_version) == (2, "v1")

    expand([dict(json_suite()[2], category="typical")])
    row.refresh_from_db()
    assert (len(row.hidden_test_cases), row.execution_contract_version) == (3, "v1")
