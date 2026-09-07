"""
The Python reflection-starter contract (M2 P2.38 / Phase 1 M6).

293 of 1,788 servable questions shipped a Python starter that cannot execute.
The generation validator was the reason: it checked that a starter CONTAINED
the word `Solution` and that its parameters CARRIED annotations, but it never
parsed the template and never asked whether the annotation names resolve.

So it accepted both of the largest failure classes:

    nums: List[int]      NameError before the learner's first line — the
                         harness emits no imports, and `List` is not a
                         builtin. 84 questions.

    class Solution: def f(self, x: int) -> int: pass
                         newlines lost somewhere in generation; does not
                         parse at all. 21 questions.

The repair is at the validator, not in 293 rows: it now delegates to
`language_readiness`, which is the one definition of "can this execute".

`structural_type` is deliberately still allowed through — a signature naming
TreeNode cannot execute either, but that is the M5 gap, and blocking it here
would make every tree question ungeneratable while M5 is undecided.
"""

import ast
import inspect
import re

import pytest

from groups import language_readiness
from groups.management.commands import reseed_questions

GOOD = ("class Solution:\n"
        "    def f(self, nums: list[int]) -> int:\n        pass\n")
CAPITAL_LIST = ("class Solution:\n"
                "    def f(self, nums: List[int]) -> int:\n        pass\n")
UNPARSEABLE = "class Solution: def f(self, x: int) -> int: pass"
NO_SOLUTION = ("class MinStack:\n"
               "    def push(self, x: int) -> None:\n        pass\n")
STRUCTURAL = ("class Solution:\n"
              "    def f(self, root: TreeNode) -> bool:\n        pass\n")
UNANNOTATED = "class Solution:\n    def f(self, nums):\n        pass\n"


@pytest.fixture
def validate():
    return reseed_questions.Command()._validate_starter_code


# ═════════════════════════════════════════════════════════════
# The two classes the validator used to accept
# ═════════════════════════════════════════════════════════════

def test_a_capitalised_typing_name_is_rejected(validate):
    """
    84 questions in the bank carry exactly this. `List` is not a builtin and
    the harness emits no imports, so it raises at class-definition time.
    """
    problem = validate({"python": CAPITAL_LIST})

    assert problem is not None
    assert "List" in problem and "NameError" in problem


def test_an_unparseable_starter_is_rejected(validate):
    """21 questions, newlines lost somewhere between the model and storage."""
    problem = validate({"python": UNPARSEABLE})

    assert problem is not None
    assert "does not parse" in problem


def test_the_lowercase_builtin_generic_is_accepted(validate):
    """
    `list[int]` is the convention the generator's own prompt documents, and
    the reason Judge0 language id 92 (Python 3.11) was selected over 71.
    """
    assert validate({"python": GOOD}) is None


# ═════════════════════════════════════════════════════════════
# The checks that already worked must keep working
# ═════════════════════════════════════════════════════════════

def test_a_missing_solution_class_is_still_rejected(validate):
    problem = validate({"python": NO_SOLUTION})

    assert problem is not None and "Solution" in problem


def test_unannotated_parameters_are_still_rejected(validate):
    problem = validate({"python": UNANNOTATED})

    assert problem is not None and "unannotated" in problem


def test_a_self_contained_starter_without_main_is_still_rejected(validate):
    problem = validate({"cpp": "class Solution {};\n"})

    assert problem is not None and "main()" in problem


# ═════════════════════════════════════════════════════════════
# The deliberate M5 exception
# ═════════════════════════════════════════════════════════════

def test_a_structural_type_is_allowed_through(validate):
    """
    NOT an oversight. TreeNode cannot execute either, but blocking it at
    generation would make every tree and linked-list question ungeneratable
    while M5 is still undecided. It stays visible in the readiness report.
    """
    assert validate({"python": STRUCTURAL}) is None
    assert language_readiness.assess_source(
        STRUCTURAL, "python").cause == language_readiness.STRUCTURAL_TYPE


def test_the_exception_is_narrow_and_named():
    """
    Only `structural_type` is exempt. A future cause must be considered
    explicitly rather than inheriting the exemption.
    """
    source = inspect.getsource(reseed_questions.Command._readiness_blocker)

    assert "STRUCTURAL_TYPE" in source
    for other in ("UNDEFINED_ANNOTATION", "UNPARSEABLE", "NO_ENTRY_POINT"):
        assert other not in source, other


# ═════════════════════════════════════════════════════════════
# One definition, not a second partial copy
# ═════════════════════════════════════════════════════════════

def test_the_validator_delegates_rather_than_reimplementing():
    source = inspect.getsource(reseed_questions.Command._readiness_blocker)

    assert "language_readiness" in source
    assert "ast.parse" not in source


def test_the_generator_prompt_documents_the_lowercase_convention():
    """
    The prompt and the validator must agree, or generation produces starters
    its own validator then rejects.
    """
    from groups import ai_services

    source = inspect.getsource(ai_services.generate_full_question)

    assert "list[int]" in source
    assert "List[int]" not in source


# ═════════════════════════════════════════════════════════════
# The repair itself is annotation-only and semantics-preserving
# ═════════════════════════════════════════════════════════════

PEP585 = {"List": "list", "Dict": "dict", "Set": "set",
          "Tuple": "tuple", "FrozenSet": "frozenset"}


def lower_generics(source):
    out = source
    for old, new in PEP585.items():
        out = re.sub(r"\b" + old + r"\[", new + "[", out)
    return out


def test_the_repair_changes_only_annotations():
    """
    Structural equality apart from annotations. A repair that renamed a
    parameter or touched a body would be a different action with a different
    review — which is exactly why `remediate_boilerplate` compares syntax
    trees rather than text.
    """
    before = ast.parse(CAPITAL_LIST)
    after = ast.parse(lower_generics(CAPITAL_LIST))

    before_fn = [n for n in ast.walk(before) if isinstance(n, ast.FunctionDef)][0]
    after_fn = [n for n in ast.walk(after) if isinstance(n, ast.FunctionDef)][0]

    assert before_fn.name == after_fn.name
    assert [a.arg for a in before_fn.args.args] == \
           [a.arg for a in after_fn.args.args]
    assert ast.dump(ast.Module(body=before_fn.body, type_ignores=[])) == \
           ast.dump(ast.Module(body=after_fn.body, type_ignores=[]))


def test_the_repair_makes_the_starter_ready():
    assert language_readiness.assess_source(
        lower_generics(CAPITAL_LIST), "python").verdict == \
        language_readiness.READY


def test_the_repair_does_not_touch_an_already_correct_starter():
    assert lower_generics(GOOD) == GOOD


def test_the_repair_does_not_rescue_a_structural_type():
    """
    Lowercasing generics says nothing about TreeNode. The M5 gap must not be
    hidden by a repair aimed at a different cause.
    """
    result = language_readiness.assess_source(
        lower_generics(STRUCTURAL), "python")

    assert result.cause == language_readiness.STRUCTURAL_TYPE
