"""
The canonical structural input architecture (Phase 1 M5).

WHAT M5 IS
    Not "a TreeNode parser". One canonical, language-independent
    representation, and deterministic adapters that turn it into a learner-
    facing object in each REFLECTION language — while C and C++ stay
    self-contained and receive the canonical text on stdin, unaltered.

THE REPRESENTATION WAS FOUND, NOT CHOSEN
    The stored content already carries structural inputs in five different
    forms. Level-order JSON is both the most common and the only one that is
    already deterministic, language-independent and unambiguous, so it is the
    canonical one. The others are NOT rewritten — changing a question's stored
    representation changes what its expected outputs mean.

WHAT THESE TESTS DO
    The parity and round-trip tests EXECUTE both harnesses as real
    subprocesses through the same seam the grader uses. Java's structural
    contract is asserted structurally only: there is no JVM in this
    environment, so nothing here compiles it and no runtime claim is made.
    Judge0 is not used anywhere in this module.
"""

import json
import subprocess
import sys
import textwrap

import pytest

from groups import execution_contract as ec
from groups import language_readiness, structural_types
from groups.models import CodingPortal, Question, Topic
from groups.services import GradingService

# ═════════════════════════════════════════════════════════════
# THE canonical fixtures. One table, every language.
# ═════════════════════════════════════════════════════════════
#
# Each entry is the exact stored text. A structure has ONE spelling, so the
# same string is both the input and — for an identity method — the expected
# output. That round-trip property is what lets a learner run, a reference run
# and an oracle run share a fixture.

CANONICAL_TREES = (
    ("empty",            "[]"),
    ("single node",      "[1]"),
    ("balanced",         "[1,2,3]"),
    ("full",             "[1,2,3,4,5,6,7]"),
    ("absent children",  "[5,1,4,null,null,3,6]"),
    ("left-skewed",      "[1,2,null,3]"),
    ("right-skewed",     "[1,null,2,null,3]"),
    ("duplicate values", "[1,1,1]"),
    ("negative values",  "[-10,9,20,null,null,15,7]"),
    ("zero value",       "[0]"),
)

CANONICAL_LISTS = (
    ("empty",            "[]"),
    ("single node",      "[1]"),
    ("multiple nodes",   "[1,2,3]"),
    ("duplicate values", "[7,7,7,7]"),
    ("negative values",  "[-1,-1,2]"),
    ("zero value",       "[0]"),
)

TREE_IDS = [name for name, _ in CANONICAL_TREES]
LIST_IDS = [name for name, _ in CANONICAL_LISTS]


# ═════════════════════════════════════════════════════════════
# Execution helpers — the real seam, not a helper in isolation
# ═════════════════════════════════════════════════════════════

RUNNERS = {"python": [sys.executable, "-c"], "javascript": ["node", "-e"]}

PY_ECHO_TREE = textwrap.dedent("""
    class Solution:
        def echo(self, root: TreeNode | None) -> TreeNode | None:
            return root
""")
JS_ECHO = "class Solution {\n  echo(node) { return node; }\n}\n"
PY_ECHO_LIST = textwrap.dedent("""
    class Solution:
        def echo(self, head: ListNode | None) -> ListNode | None:
            return head
""")

STARTER_TREE = ("class Solution:\n"
                "    def echo(self, root: TreeNode | None) -> TreeNode | None:\n"
                "        pass\n")
STARTER_LIST = ("class Solution:\n"
                "    def echo(self, head: ListNode | None) -> ListNode | None:\n"
                "        pass\n")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M5 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M5Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, starter, version=ec.CONTRACT_V2, language="python"):
    return Question.objects.create(
        title="M5", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code={language: starter, "python": starter},
        hidden_test_cases=[{"stdin": "[]", "expected_output": "[]"}],
        hidden_wrapper_code={}, execution_contract_version=version)


def execute(question, language, learner_source, stdin):
    """Build and feed exactly as the grader does."""
    executable, _stored = GradingService._build_executable(
        question, language, learner_source)
    prepared = GradingService.prepare_stdin(question, language, stdin)
    proc = subprocess.run(RUNNERS[language] + [executable], input=prepared,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def echoed(question, language, stdin, kind="tree"):
    source = JS_ECHO if language == "javascript" else (
        PY_ECHO_TREE if kind == "tree" else PY_ECHO_LIST)
    stdout, stderr, code = execute(question, language, source, stdin)
    assert code == 0, f"{language} exited {code}: {stderr}"
    return stdout


# ═════════════════════════════════════════════════════════════
# Round-trip: the property the whole architecture rests on
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,canonical", CANONICAL_TREES, ids=TREE_IDS)
def test_a_tree_round_trips_through_the_server_side_adapter(name, canonical):
    """
    `serialize(build(x)) == x` for every canonical x. Without it the same tree
    would have several spellings, and a correct submission would fail on
    formatting rather than on being wrong.
    """
    values = json.loads(canonical)

    rebuilt = structural_types.serialize_tree(
        structural_types.build_tree(values))

    assert rebuilt == values


@pytest.mark.parametrize("name,canonical", CANONICAL_LISTS, ids=LIST_IDS)
def test_a_list_round_trips_through_the_server_side_adapter(name, canonical):
    values = json.loads(canonical)

    rebuilt = structural_types.serialize_linked_list(
        structural_types.build_linked_list(values))

    assert rebuilt == values


def test_trailing_nulls_are_trimmed_to_one_spelling():
    """
    `[1,2,3]` and `[1,2,3,null,null]` are the same tree. Only the trimmed form
    is canonical, so serialisation must produce it from either.
    """
    padded = structural_types.build_tree([1, 2, 3, None, None, None, None])

    assert structural_types.serialize_tree(padded) == [1, 2, 3]


def test_a_null_has_no_children_in_the_representation():
    """
    The compactness rule. In `[1,null,2,null,3]` the second null belongs to
    node 2, not to the null before it — a null contributes no queue entry, so
    a missing subtree costs one token rather than a whole level.
    """
    root = structural_types.build_tree([1, None, 2, None, 3])

    assert root.left is None
    assert root.right.val == 2
    assert root.right.left is None
    assert root.right.right.val == 3


def test_child_order_is_left_then_right():
    """
    Reading the pair backwards would mirror every tree, and every symmetric
    fixture would still pass. Asserted on an asymmetric one.
    """
    root = structural_types.build_tree([1, 2, 3])

    assert (root.left.val, root.right.val) == (2, 3)


def test_list_order_is_preserved_not_reversed():
    head = structural_types.build_linked_list([1, 2, 3])

    assert (head.val, head.next.val, head.next.next.val) == (1, 2, 3)
    assert head.next.next.next is None


def test_a_cycle_is_refused_rather_than_hung():
    """
    A cycle has no spelling in the canonical form. Hanging would be reported
    as a timeout, which reads to a learner as "too slow" rather than "this
    structure cannot be represented".
    """
    head = structural_types.build_linked_list([1, 2, 3])
    head.next.next.next = head

    with pytest.raises(ValueError, match="cycle"):
        structural_types.serialize_linked_list(head)


# ═════════════════════════════════════════════════════════════
# Cross-language parity, executed
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("name,canonical", CANONICAL_TREES, ids=TREE_IDS)
def test_both_reflection_harnesses_build_the_same_tree(topic, name, canonical):
    """
    THE contract test for trees. Same canonical text, same structure, same
    canonical text back — in two languages that share no code.
    """
    question = make_question(topic, STARTER_TREE)

    python = echoed(question, "python", canonical)
    javascript = echoed(question, "javascript", canonical)

    assert python == javascript
    assert python == canonical


@pytest.mark.django_db
@pytest.mark.parametrize("name,canonical", CANONICAL_LISTS, ids=LIST_IDS)
def test_both_reflection_harnesses_build_the_same_list(topic, name, canonical):
    question = make_question(topic, STARTER_LIST)

    python = echoed(question, "python", canonical, kind="linked_list")
    javascript = echoed(question, "javascript", canonical, kind="linked_list")

    assert python == javascript
    assert python == canonical


@pytest.mark.django_db
def test_the_learner_receives_a_real_object_not_a_string(topic):
    """
    The defect M5 exists to close: without an adapter the harness passed the
    raw text through, so `root.val` raised AttributeError on a str.
    """
    question = make_question(topic, STARTER_TREE)
    source = textwrap.dedent("""
        class Solution:
            def depth(self, root: TreeNode | None) -> int:
                if root is None:
                    return 0
                return 1 + max(self.depth(root.left), self.depth(root.right))
    """)

    stdout, stderr, code = execute(question, "python", source,
                                   "[1,2,3,4,null,null,5]")

    assert code == 0, stderr
    assert stdout == "3"


@pytest.mark.django_db
def test_an_UNANNOTATED_submission_still_receives_a_real_object(topic):
    """
    Regression, found by the M9 gate.

    The structural branch first read the SUBMITTED method's annotation. A
    learner who deletes `: TreeNode | None` from the starter they were handed
    — perfectly ordinary in a dynamically typed language — was then given the
    raw text, and their correct code raised
    `AttributeError: 'str' object has no attribute 'left'`. A wrong verdict on
    right code, which is the worst outcome available.

    The question declares the structure; the submission does not get a vote.
    Python now reads the same server-side kind vector JavaScript already did.
    """
    question = make_question(topic, STARTER_TREE)
    unannotated = textwrap.dedent("""
        class Solution:
            def echo(self, root):
                if root is None:
                    return 0
                return 1 + max(self.echo(root.left), self.echo(root.right))
    """)

    stdout, stderr, code = execute(question, "python", unannotated,
                                   "[1,2,3,4,null,null,5]")

    assert code == 0, stderr
    assert stdout == "3"


@pytest.mark.django_db
def test_javascript_receives_a_real_object_too(topic):
    question = make_question(topic, STARTER_TREE)
    source = ("class Solution {\n"
              "  depth(root) {\n"
              "    if (root === null) return 0;\n"
              "    return 1 + Math.max(this.depth(root.left),"
              " this.depth(root.right));\n"
              "  }\n}\n")

    stdout, stderr, code = execute(question, "javascript", source,
                                   "[1,2,3,4,null,null,5]")

    assert code == 0, stderr
    assert stdout == "3"


@pytest.mark.django_db
def test_a_structural_return_is_normalized_in_both_languages(topic):
    """
    Output normalisation. A returned node must become the canonical text, in
    both languages, rather than a language-specific object repr.
    """
    question = make_question(topic, STARTER_LIST)
    python = textwrap.dedent("""
        class Solution:
            def reverse(self, head: ListNode | None) -> ListNode | None:
                previous = None
                while head is not None:
                    head.next, previous, head = previous, head, head.next
                return previous
    """)
    javascript = (
        "class Solution {\n"
        "  reverse(head) {\n"
        "    let previous = null;\n"
        "    while (head !== null) {\n"
        "      const rest = head.next; head.next = previous;\n"
        "      previous = head; head = rest;\n    }\n"
        "    return previous;\n  }\n}\n")

    assert execute(question, "python", python, "[1,2,3]")[0] == "[3,2,1]"
    assert execute(question, "javascript", javascript, "[1,2,3]")[0] == "[3,2,1]"


@pytest.mark.django_db
def test_an_empty_structural_return_is_the_empty_array_not_blank(topic):
    """
    The empty structure and "no value" are the SAME object in both languages.
    Only the DECLARED return type separates them, which is why the return kind
    is computed server-side and injected. Blank would fail against a stored
    `[]` while looking like a formatting quibble.
    """
    question = make_question(topic, STARTER_LIST)

    assert echoed(question, "python", "[]", kind="linked_list") == "[]"
    assert echoed(question, "javascript", "[]", kind="linked_list") == "[]"


@pytest.mark.django_db
def test_a_method_returning_no_value_still_prints_nothing(topic):
    """
    The control for the rule above. `-> None` is not a structure, so None must
    stay blank — otherwise every void method would start printing `[]`.
    """
    starter = ("class Solution:\n"
               "    def visit(self, root: TreeNode | None) -> None:\n"
               "        pass\n")
    question = make_question(topic, starter)
    source = textwrap.dedent("""
        class Solution:
            def visit(self, root: TreeNode | None) -> None:
                return None
    """)

    stdout, _stderr, code = execute(question, "python", source, "[1,2,3]")

    assert code == 0 and stdout == ""


@pytest.mark.django_db
@pytest.mark.parametrize("stored", [
    "3,9,20,null,null,15,7",     # q104: bare commas, no brackets
    "{-10,9,20,None,None,15,7}",  # q124: braces and Python's None
    "1 2 3",                      # q1448: whitespace tokens
    "0",                          # a bare scalar where an array is declared
])
def test_a_non_canonical_stored_input_fails_LOUDLY(topic, stored):
    """
    Found by running real production questions through the adapter: 79 of the
    124 structural questions store a representation that is not the canonical
    one, and q104 is one of them.

    The requirement is not that they work — a question's stored representation
    cannot be reinterpreted without changing what its expected outputs mean.
    It is that they FAIL VISIBLY. A silent best-effort parse would hand the
    learner a different tree from the one the answer key was written against
    and mark their correct solution wrong, which is the worst outcome
    available. Non-zero exit, named error, no verdict.
    """
    question = make_question(topic, STARTER_TREE)

    stdout, stderr, code = execute(question, "python", PY_ECHO_TREE, stored)

    assert code != 0, f"expected a refusal, got {stdout!r}"
    assert "JSON" in stderr or "json" in stderr or "must be a JSON array" in stderr


# ═════════════════════════════════════════════════════════════
# The two execution models stay two
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("language", ["c", "cpp"])
def test_the_self_contained_languages_receive_the_canonical_text_unaltered(
        topic, language):
    """
    C and C++ are NOT given a node class, a builder or a wrapper. The
    canonical serialised structure reaches the program exactly as stored and
    the learner parses it — which is the established self-contained contract,
    and the line M5 must not cross.
    """
    starter = "#include <stdio.h>\nint main(void) { return 0; }\n"
    question = Question.objects.create(
        title="M5 self-contained", content="c", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={language: starter, "python": STARTER_TREE},
        hidden_test_cases=[{"stdin": "[1,2,3]", "expected_output": "3"}],
        hidden_wrapper_code={}, execution_contract_version=ec.CONTRACT_V2)

    executable, stored = GradingService._build_executable(
        question, language, starter)
    prepared = GradingService.prepare_stdin(question, language,
                                            "[1,2,3,null,null,4]")

    assert executable == starter and stored == starter
    assert prepared == "[1,2,3,null,null,4]"


def test_no_structural_prelude_exists_for_a_self_contained_language():
    """The absence IS the contract, exactly as `V2_WRAPPERS` has none."""
    for key in ("c", "cpp"):
        assert key not in ec.STRUCTURAL_PRELUDES


def test_the_execution_models_are_still_exactly_two():
    from common import languages

    self_contained = {lang.key for lang in languages.REGISTRY
                      if lang.self_contained}
    reflection = {lang.key for lang in languages.REGISTRY
                  if not lang.self_contained}

    assert self_contained == {"c", "cpp"}
    assert reflection == {"python", "java", "javascript"}


# ═════════════════════════════════════════════════════════════
# The prelude is scoped, and ordered
# ═════════════════════════════════════════════════════════════

def test_the_prelude_is_absent_when_no_parameter_is_structural():
    rendered = ec.render_v2(ec.V2_PYTHON_WRAPPER, "USER", ["sequence"])

    assert "class TreeNode" not in rendered
    assert rendered.startswith("USER")


def test_the_prelude_appears_for_a_structural_return_alone():
    """
    A method taking an int and returning a tree still needs the node class —
    to serialise the answer, even though nothing is built from stdin.
    """
    rendered = ec.render_v2(ec.V2_PYTHON_WRAPPER, "USER", ["scalar"], "tree")

    assert "class TreeNode" in rendered


def test_the_prelude_precedes_the_learners_code():
    """
    Load-bearing twice over: `TreeNode | None` is evaluated when the learner's
    class is DEFINED, and a learner who writes their own TreeNode must win by
    defining it later.
    """
    rendered = ec.render_v2(ec.V2_PYTHON_WRAPPER, "USER_MARKER", ["tree"])

    assert rendered.index("class TreeNode") < rendered.index("USER_MARKER")


@pytest.mark.django_db
def test_a_learners_own_node_class_shadows_the_prelude(topic):
    """
    The platform never overwrites submitted code. A learner who defines their
    own TreeNode with an extra field gets theirs.
    """
    question = make_question(topic, STARTER_TREE)
    source = textwrap.dedent("""
        class TreeNode:
            def __init__(self, val=0, left=None, right=None):
                self.val = val
                self.left = left
                self.right = right
                self.mine = "learner"

        class Solution:
            def echo(self, root: TreeNode | None) -> str:
                return getattr(root, "mine", "prelude")
    """)

    stdout, stderr, code = execute(question, "python", source, "[1]")

    assert code == 0, stderr
    assert stdout == "learner"


def test_each_template_can_only_receive_its_own_prelude():
    """
    The placeholder is language-specific, so there is no key to get wrong and
    no way to inject Python source into the JavaScript harness.
    """
    rendered = ec.render_v2(ec.V2_JS_WRAPPER, "USER", ["tree"])

    assert "class TreeNode {" in rendered          # the JS spelling
    assert "def __init__" not in rendered          # not the Python one


def test_no_placeholder_survives_any_structural_rendering():
    for template in ec.V2_WRAPPERS.values():
        for kinds, returns in (([], ""), (["tree"], "tree"),
                               (["linked_list"], "linked_list")):
            rendered = ec.render_v2(template, "USER", kinds, returns)
            for placeholder in ("{structural_prelude_python}",
                                "{structural_prelude_javascript}",
                                "{parameter_kinds}", "{sequence_kind}",
                                "{tree_kind}", "{linked_list_kind}",
                                "{return_kind}", "{user_code}"):
                assert placeholder not in rendered, placeholder


def test_the_rendered_structural_harnesses_are_valid_source():
    for kinds in (["tree"], ["linked_list"]):
        compile(ec.render_v2(ec.V2_PYTHON_WRAPPER, PY_ECHO_TREE, kinds,
                             kinds[0]), "<generated>", "exec")
        proc = subprocess.run(
            ["node", "--check", "-"],
            input=ec.render_v2(ec.V2_JS_WRAPPER, JS_ECHO, kinds, kinds[0]),
            capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr


# ═════════════════════════════════════════════════════════════
# The type contract
# ═════════════════════════════════════════════════════════════

def test_the_registry_is_exactly_the_two_types_with_one_meaning():
    assert {s.name for s in structural_types.REGISTRY} == {"TreeNode",
                                                           "ListNode"}


def test_every_spelling_that_occurs_in_the_bank_resolves():
    """The three forms real starters use, all naming one type."""
    for spelling in ("optional[treenode]", "'treenode'", "treenode | none",
                     "list[treenode]"):
        assert structural_types.by_annotation(spelling) is structural_types.TREE_NODE
    for spelling in ("optional[listnode]", "'listnode'", "listnode | none"):
        assert structural_types.by_annotation(spelling) is structural_types.LIST_NODE


def test_a_name_that_merely_contains_a_type_does_not_resolve():
    assert structural_types.by_annotation("mytreenodething") is None
    assert structural_types.by_annotation("list[int]") is None


def test_child_order_is_declared_by_the_type_not_repeated_per_adapter():
    assert structural_types.TREE_NODE.children == ("left", "right")
    assert structural_types.LIST_NODE.children == ("next",)


def test_node_is_recorded_as_ambiguous_rather_than_implemented():
    """
    75 questions name `Node`, and it is at least seven different structures.
    Implementing one adapter for it would silently mis-execute the other six.
    """
    reason = structural_types.UNSUPPORTED["Node"]

    assert "seven" in reason
    assert structural_types.by_annotation("node") is None
    assert structural_types.unsupported_in("node") == "Node"


def test_immutable_list_node_is_recorded_as_a_different_contract():
    reason = structural_types.UNSUPPORTED["ImmutableListNode"]

    assert "restricted interface" in reason
    assert structural_types.by_annotation("immutablelistnode") is None


def test_the_canonical_empty_is_the_empty_array_for_both_types():
    for structural in structural_types.REGISTRY:
        assert structural.empty == "[]"


# ═════════════════════════════════════════════════════════════
# Readiness distinguishes the structural states
# ═════════════════════════════════════════════════════════════

READINESS_CASES = (
    ("v1 tree is a CONTRACT gap, not a parser gap",
     STARTER_TREE, ec.CONTRACT_V1,
     language_readiness.NOT_READY, language_readiness.STRUCTURAL_TYPE),
    ("v2 tree is ready",
     STARTER_TREE, ec.CONTRACT_V2, language_readiness.READY, ""),
    ("v2 list is ready",
     STARTER_LIST, ec.CONTRACT_V2, language_readiness.READY, ""),
    ("Node is unsupported and says why",
     "class Solution:\n    def f(self, root: Node) -> int:\n        pass\n",
     ec.CONTRACT_V2, language_readiness.NOT_READY,
     language_readiness.STRUCTURAL_UNSUPPORTED),
    ("ImmutableListNode is unsupported and says why",
     "class Solution:\n    def f(self, h: 'ImmutableListNode | None') -> None:\n"
     "        pass\n",
     ec.CONTRACT_V2, language_readiness.NOT_READY,
     language_readiness.STRUCTURAL_UNSUPPORTED),
    ("unquoted Optional still raises, structure or not",
     "class Solution:\n    def f(self, root: Optional[TreeNode]) -> bool:\n"
     "        pass\n",
     ec.CONTRACT_V2, language_readiness.NOT_READY,
     language_readiness.UNDEFINED_ANNOTATION),
)


@pytest.mark.parametrize("label,starter,version,verdict,cause",
                         READINESS_CASES,
                         ids=[row[0] for row in READINESS_CASES])
def test_readiness_distinguishes_the_structural_states(
        label, starter, version, verdict, cause):
    result = language_readiness.assess_source(starter, "python", version)

    assert result.verdict == verdict, result.reason
    assert result.cause == cause, result.reason


def test_a_structural_question_is_not_ready_merely_because_a_parser_exists():
    """
    The rule the milestone brief sets: readiness reflects the whole
    starter/execution contract. Every one of the 124 structural questions in
    the bank declares v1, so the adapter existing changes none of them — the
    migration is a per-question, oracle-verified decision.
    """
    result = language_readiness.assess_source(STARTER_TREE, "python",
                                              ec.CONTRACT_V1)

    assert result.verdict == language_readiness.NOT_READY
    assert "v1" in result.reason


def test_the_unsupported_reason_reaches_the_report():
    """A cause a report can group on, and a sentence a person can act on."""
    result = language_readiness.assess_source(
        "class Solution:\n    def f(self, root: Node) -> int:\n        pass\n",
        "python", ec.CONTRACT_V2)

    assert "N-ary tree" in result.reason and "quadtree" in result.reason


def test_the_structural_name_list_is_derived_not_restated():
    """
    `STRUCTURAL_TYPES` is the union of what `structural_types` can build and
    what it names as unsupported. A literal set here would be a third list to
    keep in step with the other two.
    """
    assert language_readiness.STRUCTURAL_TYPES == (
        {s.name for s in structural_types.REGISTRY}
        | set(structural_types.UNSUPPORTED))


# ═════════════════════════════════════════════════════════════
# One representation for learner, reference and oracle
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_reference_and_learner_reach_the_same_representation(topic):
    """
    The trust pipeline depends on these agreeing. Both go through
    `quality_execution_plan`, which is the packaging of the same two functions
    the grader uses — so a reference cannot be executed under semantics no
    learner experiences.
    """
    question = make_question(topic, STARTER_TREE)
    plan = GradingService.quality_execution_plan(question)

    learner_executable, _ = GradingService._build_executable(
        question, "python", PY_ECHO_TREE)
    plan_executable = plan.build_executable(PY_ECHO_TREE, "python")

    assert plan_executable == learner_executable
    assert plan.prepare_stdin("[1,null,2]", "python") == "[1,null,2]"


def test_the_server_side_and_harness_algorithms_agree():
    """
    `structural_types` builds structures server-side; the prelude builds them
    inside the sandbox. Two implementations of one algorithm, so they are
    executed against the same fixtures and compared — the guard that keeps one
    from drifting.
    """
    probe = textwrap.dedent("""
        class Solution:
            def echo(self, root: TreeNode | None) -> TreeNode | None:
                return root
    """)
    for _name, canonical in CANONICAL_TREES:
        server_side = structural_types.serialize_tree(
            structural_types.build_tree(json.loads(canonical)))
        source = ec.render_v2(ec.V2_PYTHON_WRAPPER, probe, ["tree"], "tree")
        proc = subprocess.run([sys.executable, "-c", source], input=canonical,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.strip()) == server_side, canonical


# ═════════════════════════════════════════════════════════════
# Java: contract defined, NOT executed
# ═════════════════════════════════════════════════════════════

def test_the_java_structural_contract_is_written_down():
    """
    NOT a runtime claim. There is no JVM in this environment, so nothing here
    compiles. This pins the declared shape — the same canonical form and the
    same field names as the two executable adapters, modelled on q2's shipped
    Java wrapper, which is the production precedent.
    """
    java = ec.STRUCTURAL_PRELUDE_JAVA

    assert "class TreeNode" in java and "class ListNode" in java
    for field in ("left", "right", "next", "val"):
        assert field in java


def test_the_java_v2_template_does_not_yet_consume_the_prelude():
    """
    Honest scope: the contract is written down, not wired in. Wiring it
    without a compiler would ship an unexecuted harness to real learners.
    """
    assert "{structural_prelude" not in ec.V2_JAVA_WRAPPER
    assert "java" not in ec.STRUCTURAL_PRELUDES
