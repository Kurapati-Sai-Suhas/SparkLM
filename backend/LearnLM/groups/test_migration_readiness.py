"""
The v2 migration classifier (Phase 1 M14).

WHY THIS FILE IS LONG
    `migration_readiness.classify` decides which questions are eligible to
    have their input representation changed. A false SAFE does not fail
    loudly -- it silently corrupts an answer key that passes today. Every
    assertion below corresponds to a defect that was actually present in the
    first working version of this classifier, found by inspecting the
    questions it claimed were safe.

    The sequence was 32 -> 19 -> 29 -> 25 -> 28 SAFE_TO_MIGRATE. Each step was
    a real defect, not a tuning knob, and each has a test here named after the
    question that exposed it.

NO DATABASE
    Every case is a literal starter and literal stored cases. The classifier
    is pure by construction, so the tests do not need fixtures, and a test
    that passes here is testing the rule rather than the state of the bank.
"""

import pytest

from groups import migration_readiness as mr

# ── starters, spelled the way the bank spells them ───────────────────────

TREE_SCALAR = """
class Solution:
    def isBalanced(self, root: TreeNode | None) -> bool:
        pass
"""

TREE_RETURNS_TREE = """
class Solution:
    def insertIntoBST(self, root: 'TreeNode', val: int) -> 'TreeNode':
        pass
"""

TWO_TREES = """
class Solution:
    def isSameTree(self, tree1: TreeNode | None, tree2: TreeNode | None) -> bool:
        pass
"""

LIST_NODE_PAIR = """
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
class Solution:
    def mergeTwoLists(self, l1: 'ListNode', l2: 'ListNode') -> 'ListNode':
        pass
"""

#: q382 -- the structure reaches the CONSTRUCTOR, never the graded method.
STATEFUL = """
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
class Solution:
    def __init__(self, head: ListNode):
        pass
    def getRandom(self) -> int:
        pass
"""

#: q95 -- N trees, not one.
COLLECTION_RETURN = """
class Solution:
    def generateTrees(self, n: int) -> list[TreeNode]:
        pass
"""

#: q105 -- typing generics undefined at runtime.
UNCANONICAL_ANNOTATIONS = """
class Solution:
    def buildTree(self, preorder: List[int], inorder: List[int]) -> Optional[TreeNode]:
        pass
"""

NODE_UNSUPPORTED = """
class Solution:
    def cloneGraph(self, node: 'Node') -> 'Node':
        pass
"""

IMMUTABLE_UNSUPPORTED = """
class Solution:
    def printLinkedListInReverse(self, head: 'ImmutableListNode') -> None:
        pass
"""


def case(stdin, expected):
    return {"stdin": stdin, "expected_output": expected}


def bucket(source, cases, question_id=9999):
    verdict = mr.classify(question_id, source, cases)
    return None if verdict is None else verdict.bucket


# ── positive controls: these must stay SAFE ──────────────────────────────

class TestStaysSafe:
    def test_q110_one_tree_in_scalar_out(self):
        assert bucket(TREE_SCALAR, [
            case("[3,9,20,null,null,15,7]", "true"),
            case("[1,2,2,3,3,null,null,4,4]", "false"),
        ]) == mr.SAFE_TO_MIGRATE

    def test_q100_two_trees_in_scalar_out(self):
        assert bucket(TWO_TREES, [
            case("[1,2,3]\n[1,2,3]", "true"),
            case("[1,2]\n[1,null,2]", "false"),
        ]) == mr.SAFE_TO_MIGRATE

    def test_q21_leading_empty_argument_is_the_empty_list(self):
        # THE M16 CASE. `"\\n[0]"` is two arguments, the first an empty list.
        # Collapsing it to one would read as an arity mismatch and wrongly
        # block the question -- the same mistake, one layer up, that M16 fixed
        # in the Java harness where trim() deleted the leading empty line.
        assert bucket(LIST_NODE_PAIR, [
            case("[1,2,4]\n[1,3,4]", "[1,1,2,3,4,4]"),
            case("\n[0]", "[0]"),
        ]) == mr.SAFE_TO_MIGRATE

    def test_empty_second_argument(self):
        assert bucket(LIST_NODE_PAIR,
                      [case("[1]\n", "[1]")]) == mr.SAFE_TO_MIGRATE

    def test_both_arguments_empty(self):
        assert bucket(LIST_NODE_PAIR, [case("\n", "")]) == mr.SAFE_TO_MIGRATE

    def test_trailing_newline_is_an_artifact_not_an_argument(self):
        # q112/q199/q337 and seven others store `"[...]\\n"` against a
        # one-parameter signature. Benign -- the harness binds by position --
        # but it must be REPORTED, because it is a storage inconsistency that
        # should be normalised before the wave.
        verdict = mr.classify(1, TREE_SCALAR, [case("[3,9,20]\n", "true")])

        assert verdict.bucket == mr.SAFE_TO_MIGRATE
        assert any("trailing-newline" in note for note in verdict.notes)

    def test_structural_then_scalar(self):
        assert bucket(TREE_RETURNS_TREE,
                      [case("[4,2,7,1,3]\n5", "[4,2,7,1,3,5]")]) \
            == mr.SAFE_TO_MIGRATE

    def test_canonical_structural_output(self):
        assert bucket(TREE_RETURNS_TREE,
                      [case("[4,2,7]\n5", "[4,2,7,null,null,5]")]) \
            == mr.SAFE_TO_MIGRATE


# ── A. the structure must reach the GRADED method ────────────────────────

class TestGradedSignature:
    def test_q382_constructor_injected_structure_is_stateful(self):
        assert bucket(STATEFUL, [case("[1]\n0", "1")]) == mr.STATEFUL_STRUCTURE

    def test_a_stateful_problem_is_never_safe(self):
        # The one that matters: whatever else is true of it, it must not enter
        # the wave through the ordinary function-signature adapter.
        assert bucket(STATEFUL, [case("[1,2]\n0", "1")]) != mr.SAFE_TO_MIGRATE

    def test_helper_class_alone_is_not_a_migration(self):
        source = """
class TreeNode:
    def __init__(self, val: int = 0, left: 'TreeNode' = None):
        self.val = val
class Solution:
    def countNodes(self, n: int) -> int:
        pass
"""
        assert bucket(source, [case("3", "3")]) == mr.NOT_IN_GRADED_SIGNATURE


# ── B. collections of structures ─────────────────────────────────────────

class TestCollections:
    def test_q95_list_of_treenode_return(self):
        assert bucket(COLLECTION_RETURN, [case("3", "[[1,null,2]]")]) \
            == mr.UNSUPPORTED_COLLECTION

    def test_list_of_treenode_parameter(self):
        source = """
class Solution:
    def mergeAll(self, roots: list[TreeNode]) -> int:
        pass
"""
        assert bucket(source, [case("[[1]]", "1")]) == mr.UNSUPPORTED_COLLECTION

    def test_a_collection_is_never_read_as_a_plain_tree(self):
        # An array-of-arrays IS a JSON array, so a naive output check accepts
        # it and the question looks migratable.
        assert bucket(COLLECTION_RETURN,
                      [case("3", "[[1,null,2],[2,1,3]]")]) != mr.SAFE_TO_MIGRATE


# ── C. arity ─────────────────────────────────────────────────────────────

class TestArity:
    def test_q543_genuine_extra_value(self):
        verdict = mr.classify(1, TREE_SCALAR, [case("[1,2,3,4,5]\n6", "3")])

        assert verdict.bucket == mr.BLOCKED_BY_TEST_CONTRACT_ARITY
        assert "6" in verdict.reason

    def test_too_few_arguments(self):
        assert bucket(TWO_TREES, [case("[1,2,3]", "true")]) \
            == mr.BLOCKED_BY_TEST_CONTRACT_ARITY

    def test_an_empty_surplus_is_not_a_dropped_value(self):
        # The distinction the arity rule exists to make: `"[1]\\n"` against
        # arity 1 is a trailing newline; `"[1]\\n6"` is a dropped 6.
        assert bucket(TREE_SCALAR, [case("[1]\n", "true")]) \
            == mr.SAFE_TO_MIGRATE
        assert bucket(TREE_SCALAR, [case("[1]\n6", "true")]) \
            == mr.BLOCKED_BY_TEST_CONTRACT_ARITY


# ── D/E. input and output canonicality are NOT symmetric ─────────────────

class TestCanonicality:
    def test_q298_trailing_nulls_on_INPUT_are_cosmetic(self):
        # `[4,2,6,1,3,null,null]` and `[4,2,6,1,3]` build an identical tree,
        # because an input goes through build_tree, which normalises it.
        verdict = mr.classify(1, TREE_SCALAR,
                              [case("[4,2,6,1,3,null,null]", "2")])

        assert verdict.bucket == mr.SAFE_TO_MIGRATE
        assert any("non-canonical spelling" in n for n in verdict.notes)

    def test_q783_deep_trailing_nulls_on_INPUT_are_cosmetic(self):
        verdict = mr.classify(
            1, TREE_SCALAR,
            [case("[90,69,null,49,89,52,null,null,null,null]", "1")])

        assert verdict.bucket == mr.SAFE_TO_MIGRATE

    def test_q450_trailing_nulls_on_OUTPUT_are_fatal(self):
        # THE ASYMMETRY. An output is compared as text and gets no parser
        # normalisation, so a non-canonical spelling means the stored answer
        # key stops matching the moment the harness serialises canonically.
        verdict = mr.classify(1, TREE_RETURNS_TREE,
                              [case("[5,3,6,2,4,null,7]\n7", "[5,3,6,2,4,null]")])

        assert verdict.bucket == mr.BLOCKED_BY_TEST_CONTRACT_OUTPUT
        assert "[5,3,6,2,4]" in verdict.reason

    def test_a_structural_output_that_is_not_an_array_at_all(self):
        # q105's stored `{3,9,20,15,7}` and `-1` against a tree return.
        assert bucket(TREE_RETURNS_TREE, [case("[3]\n1", "{3,9,20}")]) \
            == mr.BLOCKED_BY_TEST_CONTRACT_OUTPUT
        assert bucket(TREE_RETURNS_TREE, [case("[3]\n1", "-1")]) \
            == mr.BLOCKED_BY_TEST_CONTRACT_OUTPUT

    def test_a_scalar_return_is_never_output_checked(self):
        # `true` is not a JSON array and must not be read as a broken tree.
        assert bucket(TREE_SCALAR, [case("[1]", "true")]) == mr.SAFE_TO_MIGRATE

    def test_an_input_that_does_not_build_a_structure_is_fatal(self):
        assert bucket(TREE_SCALAR, [case("{1,2,3}", "true")]) \
            == mr.BLOCKED_BY_TEST_CONTRACT_INPUT


# ── starter-level blockers ───────────────────────────────────────────────

class TestStarter:
    def test_q105_undefined_typing_generics(self):
        verdict = mr.classify(1, UNCANONICAL_ANNOTATIONS,
                              [case("[3]\n[3]", "[3]")])

        assert verdict.bucket == mr.BLOCKED_BY_STARTER
        assert "List" in verdict.reason or "Optional" in verdict.reason

    def test_q108_after_m13_is_no_longer_starter_blocked(self):
        # M13 canonicalised q108 to `list[int]` / `TreeNode | None`. Its
        # blocker moved rather than disappearing -- it is the answer key now,
        # not the starter.
        source = """
class Solution:
    def sortedArrayToBST(self, nums: list[int]) -> TreeNode | None:
        pass
"""
        assert bucket(source, [case("[-10,-3,0,5,9]", "[0,-3,9,-10,null,5]")]) \
            != mr.BLOCKED_BY_STARTER


# ── unsupported structures and quarantine ────────────────────────────────

class TestUnsupported:
    def test_node_has_no_adapter(self):
        assert bucket(NODE_UNSUPPORTED, [case("[[2,4]]", "[[2,4]]")]) \
            == mr.UNSUPPORTED_NODE

    def test_immutable_list_node_has_no_adapter(self):
        assert bucket(IMMUTABLE_UNSUPPORTED, [case("[1,2]", "2 1")]) \
            == mr.UNSUPPORTED_IMMUTABLE


class TestQuarantine:
    def test_q98_is_quarantined_whatever_else_is_true_of_it(self):
        # q98's starter and stored cases are otherwise perfectly ordinary, so
        # nothing about its SHAPE would hold it back. The hold is about
        # whether the stored answer is true, which representational tidiness
        # cannot settle.
        verdict = mr.classify(98, TREE_SCALAR, [case("[1,null,2,null,3]",
                                                     "false")])

        assert verdict.bucket == mr.QUARANTINED

    def test_q98_can_never_be_reported_safe(self):
        assert bucket(TREE_SCALAR, [case("[1]", "true")], question_id=98) \
            != mr.SAFE_TO_MIGRATE


# ── non-structural questions are simply not in scope ─────────────────────

class TestOutOfScope:
    def test_a_plain_array_question_is_not_classified(self):
        source = """
class Solution:
    def twoSum(self, nums: list[int], target: int) -> list[int]:
        pass
"""
        assert mr.classify(1, source, [case("[2,7]\n9", "[0,1]")]) is None

    def test_an_empty_starter_is_not_classified(self):
        assert mr.classify(1, "", []) is None

    def test_an_unparseable_starter_is_not_classified(self):
        assert mr.classify(1, "class Solution(:::", []) is None


# ── determinism ──────────────────────────────────────────────────────────

def test_classification_is_deterministic():
    cases = [case("[1,2,3]\n[1,2,3]", "true"), case("[1,2]\n[1,null,2]", "false")]
    verdicts = {mr.classify(7, TWO_TREES, cases).bucket for _ in range(25)}

    assert len(verdicts) == 1


@pytest.mark.parametrize("stdin,expected_args", [
    ("\n[0]", 2),
    ("[1]\n", 2),
    ("\n", 2),
    ("[1]", 1),
    ("[1]\n[2]\n[3]", 3),
])
def test_arguments_are_split_without_stripping(stdin, expected_args):
    assert len(mr.parse_arguments(stdin)) == expected_args
