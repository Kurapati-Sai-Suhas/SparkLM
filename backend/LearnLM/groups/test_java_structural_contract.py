"""
Java's structural adapter (Phase 1 M11).

M5 built the canonical structural representation and wired Python and
JavaScript to it, writing Java's contract down but deliberately not wiring it:
there was no JVM to compile it with, and shipping an unexecuted harness to
learners is worse than saying it is not ready. M10 installed a JVM. This
milestone wires it, and executes it.

── Nothing here is new architecture ────────────────────────────────────────

The representation is M5's, unchanged: level-order JSON with `null` for an
absent child, trailing nulls trimmed; a list is its values in order. The field
shapes are not invented either — the Java bank already declares
`class ListNode { val, next }` (14 starters) and
`class TreeNode { val, left, right }` (11), which is the shape M5's registry
describes. The adapter matches the content; the content did not have to move.

── The one thing Java genuinely needs differently ──────────────────────────

JAVA HAS NO SHADOWING. Python and JavaScript put the node class above the
learner's code and let a learner's own definition win by being defined later.
Two top-level classes with one name in one Java file is a compile error
instead, so a class the learner already declares is OMITTED. That is the same
guarantee — the platform never overwrites submitted code — in a language that
cannot shadow.

What it is NOT: the learner's source deciding what a parameter IS. The
structural kind still comes from the question's declared starter, exactly as
in the other two languages, and a test below submits a deliberately mistyped
signature to prove it.

── What is NOT claimed ─────────────────────────────────────────────────────

Judge0 is NOT_SUBSCRIBED, so nothing here is production or oracle validation.
These are local `javac`/`java` 21 subprocesses. Three passing structural
shapes are also not broad Java readiness: 1,247 Java starters remain
NOT_READY, and the worklist says why.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from groups import execution_contract as ec
from groups import language_readiness, structural_types
from groups.models import CodingPortal, Question, Topic
from groups.services import GradingService

JAVAC = shutil.which("javac")
JAVA = shutil.which("java")
NODE = shutil.which("node")

needs_java = pytest.mark.skipif(
    not (JAVAC and JAVA),
    reason=("no local JDK on this shell's PATH; Judge0 is NOT_SUBSCRIBED. "
            "BLOCKED, not a pass — winget writes the user PATH, so a shell "
            "started before M10's install will not see it."))


# ═════════════════════════════════════════════════════════════
# THE canonical fixtures — the same table Python and JS are held to
# ═════════════════════════════════════════════════════════════

CANONICAL_TREES = (
    ("empty", "[]"),
    ("single node", "[1]"),
    ("balanced", "[1,2,3]"),
    ("full", "[1,2,3,4,5,6,7]"),
    ("absent children", "[5,1,4,null,null,3,6]"),
    ("left-skewed", "[1,2,null,3]"),
    ("right-skewed", "[1,null,2,null,3]"),
    ("duplicate values", "[1,1,1]"),
    ("negative values", "[-10,9,20,null,null,15,7]"),
    ("zero value", "[0]"),
)
CANONICAL_LISTS = (
    ("empty", "[]"),
    ("single node", "[1]"),
    ("multiple nodes", "[1,2,3]"),
    ("duplicate values", "[7,7,7,7]"),
    ("negative values", "[-1,-1,2]"),
    ("zero value", "[0]"),
)
TREE_IDS = [name for name, _ in CANONICAL_TREES]
LIST_IDS = [name for name, _ in CANONICAL_LISTS]


# ═════════════════════════════════════════════════════════════
# Execution helpers
# ═════════════════════════════════════════════════════════════

def run_java(user_code, stdin, kinds=(), returns=""):
    """Render the real v2 Java harness, compile it, run it."""
    source = ec.render_v2(ec.V2_JAVA_WRAPPER, user_code, kinds, returns)
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "Main.java").write_text(source, encoding="utf-8")
        build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                               capture_output=True, text=True, timeout=300)
        if build.returncode != 0:
            return None, build.stderr, "COMPILE_ERROR"
        proc = subprocess.run([JAVA, "-cp", directory, "Main"], input=stdin,
                              capture_output=True, text=True, timeout=90)
        return (proc.stdout.strip(), proc.stderr,
                "OK" if proc.returncode == 0 else "RUNTIME_ERROR")


ECHO_TREE = ("class Solution {\n  public TreeNode echo(TreeNode root) "
             "{ return root; }\n}\n")
ECHO_LIST = ("class Solution {\n  public ListNode echo(ListNode head) "
             "{ return head; }\n}\n")


# ═════════════════════════════════════════════════════════════
# TreeNode and ListNode, executed
# ═════════════════════════════════════════════════════════════

@pytest.mark.parametrize("label,canonical", CANONICAL_TREES, ids=TREE_IDS)
@needs_java
def test_a_tree_round_trips_through_the_java_adapter(label, canonical):
    """
    Canonical text in, a real TreeNode built, canonical text back. The same
    property Python and JavaScript are held to, on the same fixtures.
    """
    stdout, stderr, outcome = run_java(ECHO_TREE, canonical, ["tree"], "tree")

    assert outcome == "OK", f"{outcome}: {stderr[:400]}"
    assert stdout == canonical


@pytest.mark.parametrize("label,canonical", CANONICAL_LISTS, ids=LIST_IDS)
@needs_java
def test_a_list_round_trips_through_the_java_adapter(label, canonical):
    stdout, stderr, outcome = run_java(ECHO_LIST, canonical, ["linked_list"],
                                       "linked_list")

    assert outcome == "OK", f"{outcome}: {stderr[:400]}"
    assert stdout == canonical


@needs_java
def test_the_learner_receives_a_real_object_not_a_string():
    """
    The defect the adapter closes. Without it the Java harness bound the raw
    line to the parameter and `invoke` threw IllegalArgumentException.
    """
    depth = ("class Solution {\n  public int depth(TreeNode root) {\n"
             "    if (root == null) return 0;\n"
             "    return 1 + Math.max(depth(root.left), depth(root.right));\n"
             "  }\n}\n")

    for canonical, expected in (("[]", "0"), ("[1]", "1"), ("[1,2,3]", "2"),
                                ("[1,null,2,null,3]", "3"),
                                ("[1,2,3,4,null,null,5]", "3")):
        stdout, stderr, outcome = run_java(depth, canonical, ["tree"], "")
        assert outcome == "OK", stderr[:300]
        assert stdout == expected, canonical


@needs_java
def test_child_order_is_left_then_right():
    """Reading the pair backwards mirrors every tree; asserted asymmetrically."""
    probe = ("class Solution {\n  public int leftVal(TreeNode root) "
             "{ return root.left.val; }\n}\n")

    stdout, stderr, outcome = run_java(probe, "[1,2,3]", ["tree"], "")

    assert outcome == "OK", stderr[:300]
    assert stdout == "2"


@needs_java
def test_list_order_is_preserved_not_reversed():
    probe = ("class Solution {\n  public int second(ListNode head) "
             "{ return head.next.val; }\n}\n")

    stdout, _stderr, outcome = run_java(probe, "[1,2,3]", ["linked_list"], "")

    assert outcome == "OK" and stdout == "2"


@needs_java
def test_a_structural_return_is_normalized_to_the_canonical_form():
    """Output normalization: a returned node, not an object repr."""
    reverse = ("class Solution {\n  public ListNode reverse(ListNode head) {\n"
               "    ListNode previous = null;\n    while (head != null) {\n"
               "      ListNode rest = head.next; head.next = previous;\n"
               "      previous = head; head = rest;\n    }\n"
               "    return previous;\n  }\n}\n")

    for stdin, expected in (("[1,2,3]", "[3,2,1]"), ("[7]", "[7]"),
                            ("[-1,-1,2]", "[2,-1,-1]")):
        stdout, stderr, outcome = run_java(reverse, stdin, ["linked_list"],
                                           "linked_list")
        assert outcome == "OK", stderr[:300]
        assert stdout == expected


@needs_java
def test_an_empty_structural_return_is_the_empty_array_not_blank():
    """
    The empty structure and "no value" are the same reference in Java too, so
    only the DECLARED return kind separates them.
    """
    stdout, _stderr, outcome = run_java(ECHO_LIST, "[]", ["linked_list"],
                                        "linked_list")

    assert outcome == "OK" and stdout == "[]"


@needs_java
def test_a_method_returning_no_value_still_prints_nothing():
    """The control: `-> None` is not a structure, so null stays blank."""
    visit = ("class Solution {\n  public void visit(TreeNode root) { }\n}\n")

    stdout, _stderr, outcome = run_java(visit, "[1,2,3]", ["tree"], "")

    assert outcome == "OK" and stdout == ""


# ═════════════════════════════════════════════════════════════
# Scalars, arrays and mixed arguments still work
# ═════════════════════════════════════════════════════════════

JAVA_BASICS = (
    ("scalar", "class Solution {\n  public int twice(int x) "
               "{ return x * 2; }\n}\n", "21", "42", [], ""),
    ("array", "class Solution {\n  public int total(int[] nums) {\n"
              "    int s = 0; for (int v : nums) s += v; return s;\n  }\n}\n",
     "1 2 3", "6", [], ""),
    ("singleton array", "class Solution {\n  public int total(int[] nums) {\n"
                        "    int s = 0; for (int v : nums) s += v; return s;\n"
                        "  }\n}\n", "5", "5", [], ""),
    ("empty array", "class Solution {\n  public int size(int[] nums) "
                    "{ return nums.length; }\n}\n", "", "0", [], ""),
)


@pytest.mark.parametrize("label,source,stdin,expected,kinds,returns",
                         JAVA_BASICS, ids=[row[0] for row in JAVA_BASICS])
@needs_java
def test_non_structural_java_is_unaffected(label, source, stdin, expected,
                                           kinds, returns):
    """
    Regression, and it caught a real defect: the structural BINDING branches
    name `SparkLMStructures`, so while they were unconditional and the prelude
    was not, every non-structural Java submission failed to compile. Java is
    unforgiving that way, which is why both halves are placeholders.
    """
    stdout, stderr, outcome = run_java(source, stdin, kinds, returns)

    assert outcome == "OK", f"{outcome}: {stderr[:400]}"
    assert stdout == expected


@needs_java
def test_mixed_scalar_and_structural_arguments():
    probe = ("class Solution {\n  public int count(TreeNode root, int k) {\n"
             "    if (root == null) return 0;\n"
             "    return k + count(root.left, k) + count(root.right, k);\n"
             "  }\n}\n")

    stdout, stderr, outcome = run_java(probe, "[1,2,3]\n2", ["tree", "scalar"],
                                       "")

    assert outcome == "OK", stderr[:300]
    assert stdout == "6"


@needs_java
def test_two_structural_arguments_keep_independent_identities():
    same = ("class Solution {\n  public boolean same(TreeNode a, TreeNode b) {"
            "\n    if (a == null || b == null) return a == b;\n"
            "    return a.val == b.val && same(a.left, b.left)"
            " && same(a.right, b.right);\n  }\n}\n")

    assert run_java(same, "[1,2,3]\n[1,2,3]", ["tree", "tree"], "")[0] == "true"
    assert run_java(same, "[1,2,3]\n[1,3,2]", ["tree", "tree"], "")[0] == "false"


# ═════════════════════════════════════════════════════════════
# The question declares the structure, not the submission
# ═════════════════════════════════════════════════════════════

@needs_java
def test_a_learner_cannot_change_the_structural_type_by_retyping_it():
    """
    THE invariant the brief names: the submitted signature does not get a
    vote. Here the learner declares `Object`, which the old type-driven
    binding would have filled with the raw String — the kind vector still
    builds a tree.
    """
    probe = ("class Solution {\n  public String kind(Object root) {\n"
             "    return root == null ? \"null\" : root.getClass().getName();"
             "\n  }\n}\n")

    stdout, stderr, outcome = run_java(probe, "[1,2,3]", ["tree"], "")

    assert outcome == "OK", stderr[:300]
    assert stdout == "TreeNode"


@needs_java
def test_a_learner_declaring_their_own_node_class_still_compiles():
    """
    Java has no shadowing: the platform must OMIT a class the learner already
    declares, or the file has two `TreeNode`s and does not compile.
    """
    own = ("class TreeNode {\n  int val;\n  TreeNode left;\n  TreeNode right;\n"
           "  TreeNode() {}\n  TreeNode(int val) { this.val = val; }\n"
           "  TreeNode(int val, TreeNode left, TreeNode right) {\n"
           "    this.val = val; this.left = left; this.right = right;\n  }\n}\n"
           "class Solution {\n  public int depth(TreeNode root) {\n"
           "    if (root == null) return 0;\n"
           "    return 1 + Math.max(depth(root.left), depth(root.right));\n"
           "  }\n}\n")

    stdout, stderr, outcome = run_java(own, "[1,2,3]", ["tree"], "")

    assert outcome == "OK", f"{outcome}: {stderr[:400]}"
    assert stdout == "2"


def test_the_omission_is_per_class_not_all_or_nothing():
    """
    A learner who declares only ListNode must still receive TreeNode.
    Asserted on the rendered source, because a compile error here would be
    indistinguishable from a dozen other causes.
    """
    declares_list = ("class ListNode { int val; ListNode next; }\n"
                     "class Solution { public int f(TreeNode r) "
                     "{ return 0; } }\n")

    rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, declares_list, ["tree"], "")

    assert rendered.count("class ListNode") == 1      # the learner's
    assert "class TreeNode" in rendered               # the platform's
    assert "class SparkLMStructures" in rendered


# ═════════════════════════════════════════════════════════════
# The reflection contract is untouched
# ═════════════════════════════════════════════════════════════

@needs_java
def test_two_public_methods_are_still_refused():
    ambiguous = ("class Solution {\n  public int a(TreeNode r) { return 1; }\n"
                 "  public int b(TreeNode r) { return 2; }\n}\n")

    _stdout, stderr, outcome = run_java(ambiguous, "[1]", ["tree"], "")

    assert outcome == "RUNTIME_ERROR"
    assert "exactly one public method" in stderr


@needs_java
def test_a_private_helper_is_still_allowed():
    helper = ("class Solution {\n  public int depth(TreeNode root) "
              "{ return go(root); }\n"
              "  private int go(TreeNode n) { return n == null ? 0 :"
              " 1 + Math.max(go(n.left), go(n.right)); }\n}\n")

    stdout, stderr, outcome = run_java(helper, "[1,2,3]", ["tree"], "")

    assert outcome == "OK", stderr[:300]
    assert stdout == "2"


@needs_java
def test_a_non_canonical_input_fails_loudly():
    """
    Same rule as Python and JavaScript: a representation the question does not
    use must refuse visibly, never guess. A silent best-effort parse would
    hand the learner a different tree from the one the key was written for.
    """
    stdout, stderr, outcome = run_java(ECHO_TREE, "1 2 3", ["tree"], "tree")

    assert outcome == "RUNTIME_ERROR", stdout
    assert "must be a JSON array" in stderr


# ═════════════════════════════════════════════════════════════
# Cross-language parity: Python, JavaScript, Java
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M11 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M11Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


TREE_STARTER = ("class Solution:\n"
                "    def echo(self, root: TreeNode | None) -> TreeNode | None:\n"
                "        pass\n")
LIST_STARTER = ("class Solution:\n"
                "    def echo(self, head: ListNode | None) -> ListNode | None:\n"
                "        pass\n")
PY_ECHO = textwrap.dedent("""
    class Solution:
        def echo(self, node):
            return node
""")
JS_ECHO = "class Solution {\n  echo(node) { return node; }\n}\n"


def make_question(topic, starter, java_starter):
    return Question.objects.create(
        title="M11", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code={"python": starter, "java": java_starter},
        hidden_test_cases=[{"stdin": "[]", "expected_output": "[]"}],
        hidden_wrapper_code={}, execution_contract_version=ec.CONTRACT_V2)


def through_seam(question, language, source, stdin):
    executable, _stored = GradingService._build_executable(
        question, language, source)
    prepared = GradingService.prepare_stdin(question, language, stdin)
    if language == "java":
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Main.java").write_text(executable,
                                                    encoding="utf-8")
            build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                                   capture_output=True, text=True, timeout=300)
            assert build.returncode == 0, build.stderr[:400]
            proc = subprocess.run([JAVA, "-cp", directory, "Main"],
                                  input=prepared, capture_output=True,
                                  text=True, timeout=90)
    else:
        runner = ([sys.executable, "-c"] if language == "python"
                  else [NODE, "-e"])
        proc = subprocess.run(runner + [executable], input=prepared,
                              capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:400]
    return proc.stdout.strip()


@pytest.mark.django_db
@pytest.mark.parametrize("label,canonical", CANONICAL_TREES, ids=TREE_IDS)
@needs_java
def test_all_three_reflection_languages_agree_on_a_tree(topic, label,
                                                        canonical):
    """
    The parity the milestone exists to establish, through the REAL seam.
    Semantic comparison — the three languages are free to print differently,
    and here they do not have to be told to agree; they were built from one
    representation.
    """
    question = make_question(topic, TREE_STARTER, ECHO_TREE)

    python = through_seam(question, "python", PY_ECHO, canonical)
    javascript = through_seam(question, "javascript", JS_ECHO, canonical)
    java = through_seam(question, "java", ECHO_TREE, canonical)

    assert json.loads(python) == json.loads(javascript) == json.loads(java)
    assert json.loads(python) == json.loads(canonical)


@pytest.mark.django_db
@pytest.mark.parametrize("label,canonical", CANONICAL_LISTS, ids=LIST_IDS)
@needs_java
def test_all_three_reflection_languages_agree_on_a_list(topic, label,
                                                        canonical):
    question = make_question(topic, LIST_STARTER, ECHO_LIST)

    python = through_seam(question, "python", PY_ECHO, canonical)
    javascript = through_seam(question, "javascript", JS_ECHO, canonical)
    java = through_seam(question, "java", ECHO_LIST, canonical)

    assert json.loads(python) == json.loads(javascript) == json.loads(java)
    assert json.loads(python) == json.loads(canonical)


@pytest.mark.django_db
@needs_java
def test_the_server_side_structures_match_what_java_builds(topic):
    """
    `structural_types` builds structures server-side for the reference path;
    the Java prelude builds them inside the sandbox. Two implementations of
    one algorithm, executed against one fixture table.
    """
    question = make_question(topic, TREE_STARTER, ECHO_TREE)

    for _label, canonical in CANONICAL_TREES:
        server_side = structural_types.serialize_tree(
            structural_types.build_tree(json.loads(canonical)))
        assert json.loads(
            through_seam(question, "java", ECHO_TREE, canonical)) == server_side


# ═════════════════════════════════════════════════════════════
# Readiness: the structural gap is no longer hidden in UNKNOWN
# ═════════════════════════════════════════════════════════════

JAVA_TREE = ("class Solution {\n  public boolean isValidBST(TreeNode root) "
             "{ return false; }\n}\n")
JAVA_LIST = ("class Solution {\n  public ListNode rev(ListNode head) "
             "{ return null; }\n}\n")
JAVA_NODE = "class Solution {\n  public int f(Node root) { return 0; }\n}\n"
JAVA_SCALAR = "class Solution {\n  public int twice(int x) { return x*2; }\n}\n"

READINESS = (
    ("v1 tree is a CONTRACT gap", JAVA_TREE, ec.CONTRACT_V1,
     language_readiness.NOT_READY, language_readiness.STRUCTURAL_TYPE),
    ("v1 list is a CONTRACT gap", JAVA_LIST, ec.CONTRACT_V1,
     language_readiness.NOT_READY, language_readiness.STRUCTURAL_TYPE),
    ("Node is unsupported", JAVA_NODE, ec.CONTRACT_V2,
     language_readiness.NOT_READY, language_readiness.STRUCTURAL_UNSUPPORTED),
    ("v2 tree is decidable no further", JAVA_TREE, ec.CONTRACT_V2,
     language_readiness.UNKNOWN, ""),
    ("v2 scalar is unchanged", JAVA_SCALAR, ec.CONTRACT_V2,
     language_readiness.UNKNOWN, ""),
)


@pytest.mark.parametrize("label,starter,version,verdict,cause", READINESS,
                         ids=[row[0] for row in READINESS])
def test_java_readiness_distinguishes_the_structural_states(
        label, starter, version, verdict, cause):
    result = language_readiness.assess_source(starter, "java", version)

    assert result.verdict == verdict, result.reason
    assert result.cause == cause, result.reason


def test_a_v1_structural_java_question_is_no_longer_hidden_in_UNKNOWN():
    """
    The readiness defect M11 corrects. UNKNOWN counts as SERVABLE, and a v1
    Java question declaring TreeNode cannot execute at all — the harness binds
    a raw String and `invoke` throws. "We cannot decide" was the wrong answer
    to a decidable question.
    """
    result = language_readiness.assess_source(JAVA_TREE, "java",
                                              ec.CONTRACT_V1)

    assert result.verdict == language_readiness.NOT_READY
    assert result.ready is False
    assert "v1" in result.reason


def test_a_v2_structural_java_question_is_not_claimed_READY():
    """
    The adapter existing does not make a question READY. Without a compiler a
    checker cannot establish more than that the shape is right, and claiming
    otherwise would be claiming a compile.
    """
    result = language_readiness.assess_source(JAVA_TREE, "java",
                                              ec.CONTRACT_V2)

    assert result.verdict == language_readiness.UNKNOWN
    assert result.verdict != language_readiness.READY


def test_the_adapter_list_is_read_off_the_templates():
    """
    A stale list could claim an adapter that does not exist. Wiring one is
    what changes readiness.
    """
    assert language_readiness._STRUCTURAL_ADAPTERS == frozenset(
        {"python", "javascript", "java"})
    for key in language_readiness._STRUCTURAL_ADAPTERS:
        assert "{structural_prelude_" in ec.V2_WRAPPERS[key]


def test_the_self_contained_languages_gained_no_java_style_adapter():
    """M11 must not have moved C or C++ toward reflection."""
    for key in ("c", "cpp"):
        assert ec.V2_WRAPPERS.get(key) is None
        assert key not in ec.STRUCTURAL_PRELUDES
        assert key not in language_readiness._STRUCTURAL_ADAPTERS


# ═════════════════════════════════════════════════════════════
# The Java prelude, structurally
# ═════════════════════════════════════════════════════════════

def test_the_prelude_is_absent_when_nothing_is_structural():
    rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, "class Solution {}", ["scalar"])

    assert "class TreeNode" not in rendered
    assert "SparkLMStructures" not in rendered


def test_the_prelude_appears_for_a_structural_return_alone():
    rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, "class Solution {}",
                            ["scalar"], "tree")

    assert "class TreeNode" in rendered and "SparkLMStructures" in rendered


def test_the_java_field_shapes_match_the_registry_and_the_bank():
    """
    Not invented: the bank already declares these fields, and M5's registry
    describes the same child order.
    """
    for field in ("int val;", "TreeNode left;", "TreeNode right;",
                  "ListNode next;"):
        assert field in ec.STRUCTURAL_PRELUDE_JAVA, field
    assert structural_types.TREE_NODE.children == ("left", "right")
    assert structural_types.LIST_NODE.children == ("next",)


def test_no_placeholder_survives_any_java_rendering():
    for kinds, returns in (([], ""), (["tree"], "tree"),
                           (["linked_list"], "linked_list"),
                           (["scalar", "tree"], "")):
        rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, "class Solution {}",
                                kinds, returns)
        for placeholder in ("{structural_prelude_java}", "{structural_bind_java}",
                            "{structural_render_java}", "{parameter_kinds_java}",
                            "{return_kind}", "{user_code}"):
            assert placeholder not in rendered, placeholder


def test_the_kind_vector_renders_as_a_java_array_not_json():
    rendered = ec.render_v2(ec.V2_JAVA_WRAPPER, "class Solution {}",
                            ["tree", "scalar"])

    assert 'SPARKLM_KINDS = {"tree", "scalar"}' in rendered
    assert 'SPARKLM_KINDS = ["tree"' not in rendered


@needs_java
def test_the_rendered_java_harness_compiles_in_every_shape():
    for kinds, returns in (([], ""), (["tree"], "tree"),
                           (["linked_list"], "linked_list"),
                           (["scalar", "tree"], "")):
        source = ec.render_v2(
            ec.V2_JAVA_WRAPPER,
            "class Solution {\n  public int f(Object a) { return 0; }\n}\n",
            kinds, returns)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "Main.java").write_text(source, encoding="utf-8")
            build = subprocess.run([JAVAC, "Main.java"], cwd=directory,
                                   capture_output=True, text=True, timeout=300)
            assert build.returncode == 0, f"{kinds}/{returns}: {build.stderr[:400]}"
