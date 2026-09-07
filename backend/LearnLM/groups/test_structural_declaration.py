"""
The declared structure reaches every adapter (Phase 1 M12).

M11 found 20 Java structural candidates and reported that 9 were blocked
because "the Python starter does not declare the structural type". Inspecting
all nine individually found that was the symptom, not the cause, and that it
had three different causes:

  1. AN ADAPTER DEFECT, five of nine. `chosen_function` read the FIRST class
     in the file, but every harness instantiates `Solution`. The idiomatic
     structural starter defines its node class first:

         class TreeNode:              <- read this one
             def __init__(...)        <- filtered out as private
         class Solution:              <- never reached
             def goodNodes(self, root: TreeNode) -> int:

     so the signature came back empty and the kind vector with it. All 68
     starters in the bank that define a helper class before `Solution` were
     affected — this was never about these nine questions.

  2. A GENUINELY WRONG OR MISSING ANNOTATION, three of nine. q100 declared
     `list[int]` for a parameter its statement, its Java starter and its
     stored `null`-bearing test data all call a binary tree; q112 and q199
     declared nothing at all.

  3. A DEFECT IN M11's OWN CLASSIFIER, two of nine. It asked only whether a
     PARAMETER was structural. q105, q106 and q108 build a tree FROM two
     arrays — the structure is in the return, and they needed no repair.

── The rule these tests exist to hold ──────────────────────────────────────

The question declares the structure; no adapter may disagree with another
about what it is. That is checked here by executing all three reflection
languages against one canonical fixture table, not by asserting that an
annotation is spelled a certain way.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from groups import execution_adapter, execution_contract as ec
from groups import language_readiness
from groups.models import Question

JAVAC, JAVA, NODE = (shutil.which("javac"), shutil.which("java"),
                     shutil.which("node"))

needs_java = pytest.mark.skipif(
    not (JAVAC and JAVA),
    reason="no local JDK on this shell's PATH; Judge0 is NOT_SUBSCRIBED")


# ═════════════════════════════════════════════════════════════
# 1. The adapter defect: `Solution`, not the first class
# ═════════════════════════════════════════════════════════════

NODE_CLASS_FIRST = (
    "class TreeNode:\n"
    "    def __init__(self, val=0, left=None, right=None):\n"
    "        self.val = val\n"
    "        self.left = left\n"
    "        self.right = right\n"
    "\n"
    "class Solution:\n"
    "    def goodNodes(self, root: TreeNode | None) -> int:\n"
    "        pass\n"
)


def test_the_signature_is_read_from_Solution_not_the_first_class():
    """
    The root cause, in one assertion. Before M12 this returned None, because
    `TreeNode`'s only method is `__init__` and private methods are filtered.
    """
    signature = execution_adapter.declared_signature(NODE_CLASS_FIRST)

    assert signature is not None
    name, parameters = signature
    assert name == "goodNodes"
    assert [p for p, _a in parameters] == ["root"]


def test_the_kind_vector_survives_a_node_class_defined_first():
    assert ec.v2_parameter_kinds(NODE_CLASS_FIRST) == [ec.TREE_KIND]


def test_public_method_names_also_reads_Solution():
    """
    Same fix, same reason: the ambiguity that matters is the one the harness
    will hit, and the harness only ever looks at `Solution`.
    """
    assert execution_adapter.public_method_names(NODE_CLASS_FIRST) == \
        ["goodNodes"]


def test_a_starter_whose_class_is_not_named_Solution_is_unchanged():
    """
    The fallback the old behaviour was right about. Preserved deliberately —
    narrowing to `Solution` alone would have broken every starter that names
    its class something else.
    """
    other = ("class Helper:\n    def compute(self, x: int) -> int:\n"
             "        pass\n")

    signature = execution_adapter.declared_signature(other)

    assert signature is not None and signature[0] == "compute"


def test_Solution_wins_even_when_it_is_not_last():
    ordered = ("class Solution:\n    def first(self, x: int) -> int:\n"
               "        pass\n"
               "\nclass TreeNode:\n    def __init__(self):\n        pass\n")

    assert execution_adapter.declared_signature(ordered)[0] == "first"


# ═════════════════════════════════════════════════════════════
# 2. The repaired questions, as they are actually stored
# ═════════════════════════════════════════════════════════════
#
# (question, the kinds its stored starter must now yield, the return kind)
#
#: The EXACT starter each question stores after M12, captured from
#: production and held as fixtures rather than read from the database:
#: the test database contains no production rows, so a suite that
#: queried them would SKIP everywhere and prove nothing. That failure
#: mode — a green suite that ran almost nothing — is the one this
#: project keeps writing tests to avoid, and the first draft of this
#: module walked straight into it (24 of 32 skipped).
#:
#: `m12_verify.py` checks production still stores exactly these.
STARTERS = {
    21: "class ListNode:\n    def __init__(self, val=0, next=None):\n        self.val = val\n        self.next = next\nclass Solution:\n    def mergeTwoLists(self, l1: 'ListNode', l2: 'ListNode') -> 'ListNode':\n        pass",
    100: 'class Solution:\n    def isSameTree(self, tree1: TreeNode | None, tree2: TreeNode | None) -> bool:\n        pass',
    105: 'class Solution:\n    def buildTree(self, preorder: List[int], inorder: List[int]) -> Optional[TreeNode]:\n        pass',
    106: 'class Solution:\n    def buildTree(self, inorder: list[int], postorder: list[int]) -> TreeNode | None:\n        pass',
    108: 'class TreeNode(object):\n    def __init__(self, val=0, left=None, right=None):\n        self.val = val\n        self.left = left\n        self.right = right\n\nclass Solution(object):\n    def sortedArrayToBST(self, nums: List[int]) -> Optional[TreeNode]:\n        pass',
    112: 'class TreeNode(object): \n    def __init__(self, x): \n        self.val = x \n        self.left = None \n        self.right = None\n\nclass Solution(object):\n    def isBalanced(self, root: TreeNode | None):\n        pass',
    199: 'class TreeNode(object): \n    def __init__(self, val=0, left=None, right=None): \n        self.val = val \n        self.left = left \n        self.right = right \n\nclass Solution(object): \n    def rightSideView(self, root: TreeNode | None): \n        pass',
    298: 'class TreeNode: \n    def __init__(self, val=0, left=None, right=None): \n        self.val = val \n        self.left = left \n        self.right = right\n\nclass Solution:\n    def longestConsecutive(self, root: TreeNode) -> int:\n        pass',
    606: 'class TreeNode(object):\n    def __init__(self, val=0, left=None, right=None):\n        self.val = val\n        self.left = left\n        self.right = right\nclass Solution:\n    def tree2str(self, root: TreeNode) -> str:\n        pass',
}

DECLARED = (
    (21,  ["linked_list", "linked_list"], "linked_list"),
    (100, ["tree", "tree"], ""),
    (105, ["sequence", "sequence"], "tree"),
    (106, ["sequence", "sequence"], "tree"),
    (108, ["sequence"], "tree"),
    (112, ["tree"], ""),
    (199, ["tree"], ""),
    (298, ["tree"], ""),
    (606, ["tree"], ""),
)


@pytest.mark.parametrize("qid,kinds,returns", DECLARED,
                         ids=[f"q{row[0]}" for row in DECLARED])
def test_each_candidate_declares_the_structure_it_should(qid, kinds, returns):
    """The kind vector each stored starter must now yield."""
    starter = STARTERS[qid]

    assert ec.v2_parameter_kinds(starter) == kinds
    assert ec.v2_return_kind(starter) == returns


@pytest.mark.parametrize("qid", [100, 112, 199])
def test_the_repaired_questions_kept_their_arity_and_method_name(qid):
    """
    The repair was annotation-only. A changed method name or arity would be a
    different question, and the audited command refuses it — this asserts the
    outcome rather than trusting the gate.
    """
    name, parameters = execution_adapter.declared_signature(STARTERS[qid])

    expected = {100: ("isSameTree", 2), 112: ("isBalanced", 1),
                199: ("rightSideView", 1)}[qid]
    assert (name, len(parameters)) == expected


@pytest.mark.parametrize("qid", [21, 100, 106, 112, 199, 298, 606])
def test_the_usable_candidates_are_python_ready_under_v2(qid):
    assert language_readiness.assess_source(
        STARTERS[qid], "python", ec.CONTRACT_V2).verdict == \
        language_readiness.READY


@pytest.mark.parametrize("qid", [105, 108])
def test_the_two_remaining_blockers_are_the_undefined_typing_aliases(qid):
    """
    NOT repaired here. `Optional[TreeNode]` -> `TreeNode | None` is outside
    what `remediate_boilerplate` permits: its canonical-form rule maps typing
    generics that have builtin counterparts, and `Optional` has none. Widening
    a trust-adjacent command is a decision, not a side effect — the same call
    M6.1 escalated rather than took.
    """
    result = language_readiness.assess_source(STARTERS[qid], "python",
                                              ec.CONTRACT_V2)

    assert result.cause == language_readiness.UNDEFINED_ANNOTATION
    assert "Optional" in result.reason or "List" in result.reason


@pytest.mark.parametrize("qid,structural", [
    (21, True), (100, True), (105, False), (106, False), (108, False),
    (112, True), (199, True), (298, True), (606, True)])
def test_which_candidates_have_a_structural_INPUT(qid, structural):
    """
    M11's classifier asked only this question and reported the answer as
    "blocked". Three of the nine have no structural input at all — they build
    a tree FROM arrays — so there was nothing to repair.
    """
    kinds = ec.v2_parameter_kinds(STARTERS[qid])

    assert bool([k for k in kinds if k in ec.STRUCTURAL_KINDS]) is structural


@pytest.mark.django_db
def test_a_repaired_starter_is_still_a_v1_CONTRACT_gap():
    """
    Readiness must not improve merely because the annotation is now right.
    All nine still declare v1, whose harness passes a string — a correct
    annotation makes a question MIGRATABLE, not ready.
    """
    from groups.models import CodingPortal, Topic

    portal = CodingPortal.objects.create(name="M12 Portal")
    topic, _ = Topic.objects.get_or_create(
        name="M12Topic", defaults={"structure_type": "flat", "portal": portal})
    question = Question.objects.create(
        title="M12", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": STARTERS[112]},
        hidden_test_cases=[{"stdin": "[1]", "expected_output": "true"}],
        hidden_wrapper_code={}, execution_contract_version=ec.CONTRACT_V1)

    result = language_readiness.assess(question, "python")

    assert result.verdict == language_readiness.NOT_READY
    assert result.cause == language_readiness.STRUCTURAL_TYPE
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.is_adaptive_eligible is False


# ═════════════════════════════════════════════════════════════
# 3. The declaration reaches every adapter, executed
# ═════════════════════════════════════════════════════════════

TREE_FIXTURES = ("[]", "[1]", "[1,2,3]", "[1,null,2,null,3]",
                 "[5,1,4,null,null,3,6]", "[-10,9,20,null,null,15,7]")
LIST_FIXTURES = ("[]", "[1]", "[1,2,3]", "[-1,-1,2]")


def echo_sources(kind, arity):
    names = ", ".join(f"a{i}" for i in range(arity))
    python = (f"class Solution:\n    def probe(self, {names}):\n"
              f"        return a0\n")
    javascript = f"class Solution {{\n  probe({names}) {{ return a0; }}\n}}\n"
    java_type = {"tree": "TreeNode", "linked_list": "ListNode"}[kind]
    params = ", ".join(f"{java_type} a{i}" if i == 0 else f"Object a{i}"
                       for i in range(arity))
    java = (f"class Solution {{\n  public {java_type} probe({params}) "
            f"{{ return a0; }}\n}}\n")
    return {"python": python, "javascript": javascript, "java": java}


def execute(language, source, kinds, returns, stdin):
    executable = ec.render_v2(ec.V2_WRAPPERS[language], source, kinds, returns)
    if language == "java":
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Main.java").write_text(executable,
                                                    encoding="utf-8")
            build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                                   capture_output=True, text=True, timeout=300)
            assert build.returncode == 0, build.stderr[:400]
            proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                                  input=stdin, capture_output=True, text=True,
                                  timeout=90)
    else:
        runner = ([sys.executable, "-c"] if language == "python"
                  else [NODE, "-e"])
        proc = subprocess.run(runner + [executable], input=stdin,
                              capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:400]
    return proc.stdout.strip()


STRUCTURAL_INPUT = ((21, "linked_list", 2), (100, "tree", 2), (112, "tree", 1),
                    (199, "tree", 1), (298, "tree", 1), (606, "tree", 1))


@pytest.mark.parametrize("qid,kind,arity", STRUCTURAL_INPUT,
                         ids=[f"q{row[0]}" for row in STRUCTURAL_INPUT])
@needs_java
def test_the_declaration_produces_one_structure_in_all_three_languages(
        qid, kind, arity):
    """
    The whole chain, executed: stored Python declaration -> kind vector ->
    each adapter -> canonical output. Semantic comparison, one fixture table.
    """
    kinds = ec.v2_parameter_kinds(STARTERS[qid])
    sources = echo_sources(kind, arity)
    fixtures = TREE_FIXTURES if kind == "tree" else LIST_FIXTURES

    for canonical in fixtures:
        stdin = "\n".join([canonical] + ["[]"] * (arity - 1))
        outputs = {language: execute(language, source, kinds, kind, stdin)
                   for language, source in sources.items()}
        assert len(set(outputs.values())) == 1, f"{canonical}: {outputs}"
        assert json.loads(outputs["python"]) == json.loads(canonical)


@pytest.mark.parametrize("qid", [105, 106, 108])
def test_a_return_only_structure_declares_no_structural_input(qid):
    """
    M11's classifier asked only about parameters, which is why these three
    were called blocked. They build a tree FROM arrays: the structure is in
    the RETURN, the parameters really are sequences, and there was nothing to
    repair.
    """
    assert ec.v2_return_kind(STARTERS[qid]) == ec.TREE_KIND
    assert not [k for k in ec.v2_parameter_kinds(STARTERS[qid])
                if k in ec.STRUCTURAL_KINDS]


# ═════════════════════════════════════════════════════════════
# 4. Removing the declaration must break the binding
# ═════════════════════════════════════════════════════════════

@needs_java
def test_without_the_declaration_java_does_not_receive_a_structure():
    """
    The mutation, as a test: strip the structural annotation and the kind
    vector empties, so the Java harness falls back to binding by the
    submitted parameter type and hands over the raw text.

    This is what all nine questions were doing before M12, and it is why an
    empty kind vector is a blocker rather than a detail.
    """
    undeclared = ("class Solution:\n    def probe(self, root):\n"
                  "        pass\n")

    kinds = ec.v2_parameter_kinds(undeclared)
    assert kinds == [ec.SCALAR_KIND]

    java = ("class Solution {\n  public String probe(Object root) {\n"
            "    return root == null ? \"null\" : root.getClass().getName();\n"
            "  }\n}\n")
    executable = ec.render_v2(ec.V2_WRAPPERS["java"], java, kinds, "")
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "Main.java").write_text(executable, encoding="utf-8")
        build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[:400]
        proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                              input="[1,2,3]", capture_output=True, text=True,
                              timeout=90)

    # A String, not a TreeNode — the defect, reproduced deliberately.
    assert proc.stdout.strip() == "java.lang.String"


@needs_java
def test_with_the_declaration_java_receives_the_structure():
    """The control. Same submission, same input, declared kind — a TreeNode."""
    java = ("class Solution {\n  public String probe(Object root) {\n"
            "    return root == null ? \"null\" : root.getClass().getName();\n"
            "  }\n}\n")
    executable = ec.render_v2(ec.V2_WRAPPERS["java"], java, [ec.TREE_KIND], "")
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "Main.java").write_text(executable, encoding="utf-8")
        build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                               capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr[:400]
        proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                              input="[1,2,3]", capture_output=True, text=True,
                              timeout=90)

    assert proc.stdout.strip() == "TreeNode"
