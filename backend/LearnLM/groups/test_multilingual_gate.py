"""
Phase 1 integration gate: do the systems built in M1-M8 work TOGETHER?
(Phase 1 M9)

This is not a feature milestone. It asks one question of the whole pipeline:

    question -> language selection -> readiness -> trust eligibility ->
    contract selection -> starter/harness -> hidden-test input ->
    language adapter -> learner code -> execution -> output normalization ->
    verdict -> ProgressionService -> adaptive eligibility -> learner state

── The honesty rule this module is built around ────────────────────────────

A test that cannot run must SKIP with a named reason, never pass quietly. Two
of the five registered languages have no runtime in this environment and
Judge0 answers 403, so a suite that reported "supported" for all five would be
reporting the limits of its own environment as a property of the platform.

`RUNTIME` is probed, not assumed, and every state below is derived from it:

    VALIDATED           executed here, real process, real stdin/stdout
    STRUCTURAL_ONLY     contract asserted; no runtime exists to execute it
    ENVIRONMENT_BLOCKED a runtime is required and absent
    NOT_READY           the platform itself reports it cannot execute
    FAIL                executed and wrong

── What is NOT claimed ─────────────────────────────────────────────────────

Judge0 was probed once and returned 403 Forbidden, so NOTHING here is Judge0
validation. Java, C and C++ have no local toolchain. Their contracts are
asserted structurally and their runtime status is ENVIRONMENT_BLOCKED — which
is a different statement from "works".
"""

import json
import shutil
import subprocess
import sys
import textwrap

import pytest
from django.contrib.auth import get_user_model

from groups import (content_quarantine, execution_contract, language_readiness,
                    structural_types)
from groups.models import (CodeSubmission, CodingPortal, Question, Topic,
                           UserCodingProfile, UserTopicMastery)
from groups.services import GradingService, ProgressionService

User = get_user_model()

# ═════════════════════════════════════════════════════════════
# Runtime capability — probed, never assumed
# ═════════════════════════════════════════════════════════════

RUNTIME = {
    "python": True,                                # this interpreter
    "javascript": shutil.which("node") is not None,
    "java": (shutil.which("javac") is not None
             and shutil.which("java") is not None),
    "c": shutil.which("gcc") is not None,
    "cpp": shutil.which("g++") is not None,
}

#: Judge0 is the only runtime for the languages with no local toolchain. It
#: answered 403 Forbidden when probed for this milestone; the constant is
#: False rather than a live call so the suite never depends on a network.
JUDGE0_AVAILABLE = False

VALIDATED = "PASS"
STRUCTURAL_ONLY = "STRUCTURAL_ONLY"
ENVIRONMENT_BLOCKED = "UNKNOWN"
NOT_READY = "NOT_READY"


def requires(language):
    """Skip with a reason rather than passing on an absent runtime."""
    return pytest.mark.skipif(
        not RUNTIME[language],
        reason=(f"no local {language} runtime and Judge0 is unavailable "
                f"(403); this is ENVIRONMENT_BLOCKED, not a pass"))


RUNNERS = {"python": [sys.executable, "-c"], "javascript": ["node", "-e"]}


# ═════════════════════════════════════════════════════════════
# Shared canonical fixtures — the same values every language sees
# ═════════════════════════════════════════════════════════════

SCALARS = (
    ("scalar integer", ["int"], "5", "5"),
    ("scalar string", ["str"], "hello", '"hello"'),
    ("boolean", ["bool"], "true", "true"),
)
SEQUENCES = (
    ("empty array", ["list[int]"], "", "[]"),
    ("singleton array", ["list[int]"], "5", "[5]"),
    ("multi array", ["list[int]"], "1 2 3", "[1,2,3]"),
    ("negative and duplicate", ["list[int]"], "-1 -1 2", "[-1,-1,2]"),
)
MULTI_ARG = (
    ("sequence + scalar", ["list[int]", "int"], "5\n9", "[[5],9]"),
    ("scalar + sequence", ["int", "list[str]"], "9\ncat", '[9,["cat"]]'),
)
TREES = (
    ("empty tree", "[]"),
    ("single node", "[1]"),
    ("balanced", "[1,2,3]"),
    ("skewed", "[1,null,2,null,3]"),
    ("absent children", "[5,1,4,null,null,3,6]"),
    ("duplicate values", "[1,1,1]"),
    ("negative values", "[-10,9,20,null,null,15,7]"),
)
LISTS = (
    ("empty list", "[]"),
    ("single node", "[1]"),
    ("multiple nodes", "[1,2,3]"),
    ("duplicate values", "[7,7,7,7]"),
    ("negative values", "[-1,-1,2]"),
)


def starter_for(annotations, returns=None):
    parameters = ", ".join(
        f"a{i}" + (f": {a}" if a else "") for i, a in enumerate(annotations))
    suffix = f" -> {returns}" if returns else ""
    return (f"class Solution:\n    def probe(self, {parameters}){suffix}:\n"
            f"        pass\n")


def python_probe(annotations, returns=None):
    names = [f"a{i}" for i in range(len(annotations))]
    parameters = ", ".join(
        name + (f": {a}" if a else "") for name, a in zip(names, annotations))
    suffix = f" -> {returns}" if returns else ""
    return textwrap.dedent(f"""
        import json
        class Solution:
            def probe(self, {parameters}){suffix}:
                return json.dumps([{', '.join(names)}], separators=(",", ":"))
    """)


def js_probe(count):
    names = ", ".join(f"a{i}" for i in range(count))
    return (f"class Solution {{\n  probe({names}) "
            f"{{ return JSON.stringify([{names}]); }}\n}}\n")


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M9 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M9Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, starter, version=execution_contract.CONTRACT_V2,
                  extra_languages=(), cases=None, **fields):
    starters = {"python": starter}
    for language in extra_languages:
        starters[language] = ("#include <stdio.h>\nint main(void)"
                              "{ return 0; }\n")
    return Question.objects.create(
        title="M9", content="c", topic=topic, base_difficulty=1200.0,
        boilerplate_code=starters,
        hidden_test_cases=cases or [{"stdin": "5", "expected_output": "5"}],
        hidden_wrapper_code={}, execution_contract_version=version, **fields)


def execute(question, language, source, stdin):
    executable, _stored = GradingService._build_executable(
        question, language, source)
    prepared = GradingService.prepare_stdin(question, language, stdin)
    proc = subprocess.run(RUNNERS[language] + [executable], input=prepared,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


# ═════════════════════════════════════════════════════════════
# A-D, K, L: scalar, sequence, singleton, empty, multi-argument
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("label,annotations,stdin,expected",
                         SCALARS + SEQUENCES,
                         ids=[row[0] for row in SCALARS + SEQUENCES])
@requires("javascript")
def test_scalar_and_sequence_categories_agree_across_runtimes(
        topic, label, annotations, stdin, expected):
    """
    Categories A-D and K, both executable languages, one fixture table.
    Singleton and empty arrays are here because they are where M8's defect
    lived, and a gate that dropped them would not notice its return.
    """
    question = make_question(topic, starter_for(annotations))

    python = execute(question, "python", python_probe(annotations), stdin)[0]
    javascript = execute(question, "javascript",
                         js_probe(len(annotations)), stdin)[0]

    assert json.loads(python) == json.loads(javascript), label
    assert json.loads(python) == [json.loads(expected)]


@pytest.mark.django_db
@pytest.mark.parametrize("label,annotations,stdin,expected", MULTI_ARG,
                         ids=[row[0] for row in MULTI_ARG])
@requires("javascript")
def test_multiple_arguments_keep_independent_types(
        topic, label, annotations, stdin, expected):
    """Category L. One argument right by luck is possible; two is not."""
    question = make_question(topic, starter_for(annotations))

    python = execute(question, "python", python_probe(annotations), stdin)[0]
    javascript = execute(question, "javascript",
                         js_probe(len(annotations)), stdin)[0]

    assert json.loads(python) == json.loads(javascript)
    assert json.loads(python) == json.loads(expected)


V1_NESTED = (
    ("nested array", "[[[1],[2]]]", 1),
    ("array of arrays", "[[[1,2],[3,4]]]", 1),
    ("object", '[{"a":1}]', 1),
    ("mixed nesting", '[[1,[2,[3]]],"x"]', 2),
)


@pytest.mark.django_db
@pytest.mark.parametrize("label,envelope,count", V1_NESTED,
                         ids=[row[0] for row in V1_NESTED])
@requires("javascript")
def test_nested_structures_agree_where_the_contract_supports_them(
        topic, label, envelope, count):
    """
    Category E, and the honest scope of it: v2's line format is flat tokens
    and cannot express nesting, so nesting is a v1/v3 capability — the JSON
    envelope carries it exactly. Tested where the contract supports it rather
    than asserted where it does not.
    """
    question = make_question(topic, starter_for([None] * count),
                             version=execution_contract.CONTRACT_V1)

    python = execute(question, "python",
                     python_probe([None] * count), envelope)[0]
    javascript = execute(question, "javascript", js_probe(count), envelope)[0]

    assert json.loads(python) == json.loads(javascript), label
    assert json.loads(python) == json.loads(envelope)


# ═════════════════════════════════════════════════════════════
# F-J: structural categories, end to end
# ═════════════════════════════════════════════════════════════

TREE_STARTER = ("class Solution:\n"
                "    def probe(self, root: TreeNode | None) -> TreeNode | None:\n"
                "        pass\n")
LIST_STARTER = ("class Solution:\n"
                "    def probe(self, head: ListNode | None) -> ListNode | None:\n"
                "        pass\n")
PY_ECHO = textwrap.dedent("""
    class Solution:
        def probe(self, node):
            return node
""")
JS_ECHO = "class Solution {\n  probe(node) { return node; }\n}\n"


@pytest.mark.django_db
@pytest.mark.parametrize("label,canonical", TREES,
                         ids=[row[0] for row in TREES])
@requires("javascript")
def test_tree_categories_round_trip_in_both_runtimes(topic, label, canonical):
    """
    Categories F, H, I, J and K for trees: canonical text in, real object
    built, canonical text back — identical in two languages that share no
    code.
    """
    question = make_question(topic, TREE_STARTER)

    python = execute(question, "python", PY_ECHO, canonical)[0]
    javascript = execute(question, "javascript", JS_ECHO, canonical)[0]

    assert python == javascript == canonical, label


@pytest.mark.django_db
@pytest.mark.parametrize("label,canonical", LISTS,
                         ids=[row[0] for row in LISTS])
@requires("javascript")
def test_linked_list_categories_round_trip_in_both_runtimes(
        topic, label, canonical):
    """Category G, plus H/I/J/K for lists."""
    question = make_question(topic, LIST_STARTER)

    python = execute(question, "python", PY_ECHO, canonical)[0]
    javascript = execute(question, "javascript", JS_ECHO, canonical)[0]

    assert python == javascript == canonical, label


@pytest.mark.django_db
@requires("javascript")
def test_a_structural_algorithm_agrees_across_runtimes(topic):
    """
    Not an echo: a real traversal in each language, so the object the adapter
    built is actually navigated rather than only round-tripped.
    """
    question = make_question(topic, TREE_STARTER)
    python = textwrap.dedent("""
        class Solution:
            def probe(self, root):
                if root is None:
                    return 0
                return 1 + max(self.probe(root.left), self.probe(root.right))
    """)
    javascript = ("class Solution {\n  probe(root) {\n"
                  "    if (root === null) return 0;\n"
                  "    return 1 + Math.max(this.probe(root.left),"
                  " this.probe(root.right));\n  }\n}\n")

    for canonical, depth in (("[]", "0"), ("[1]", "1"), ("[1,2,3]", "2"),
                             ("[1,null,2,null,3]", "3")):
        assert execute(question, "python", python, canonical)[0] == depth
        assert execute(question, "javascript", javascript, canonical)[0] == depth


# ═════════════════════════════════════════════════════════════
# M: the self-contained model, unchanged
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@pytest.mark.parametrize("language", ["c", "cpp"])
def test_the_self_contained_contract_holds_without_a_compiler(topic, language):
    """
    Category M, as far as this environment can honestly go. No gcc, no g++,
    Judge0 403 — so this asserts the CONTRACT BOUNDARY, which is the part the
    platform owns: the learner's program reaches the runner unaltered, under
    the right language id, with stdin untransformed.

    Compilation and runtime behaviour are Judge0's and are reported as
    ENVIRONMENT_BLOCKED, not as a pass.
    """
    from common import languages

    program = ("#include <stdio.h>\nint main(void){int x;scanf(\"%d\",&x);"
               "printf(\"%d\",x);return 0;}\n")
    question = make_question(topic, TREE_STARTER, extra_languages=[language])

    executable, stored = GradingService._build_executable(
        question, language, program)
    prepared = GradingService.prepare_stdin(question, language, "[1,2,3]")

    assert executable == program and stored == program
    assert prepared == "[1,2,3]"
    assert languages.get(language).self_contained
    assert language not in execution_contract.STRUCTURAL_PRELUDES
    assert execution_contract.V2_WRAPPERS.get(language) is None


def test_the_environment_is_reported_rather_than_assumed():
    """
    The fact this module's honesty depends on: every state in the matrix is
    derived from `RUNTIME`, which is probed.

    M10 installed a JDK and MinGW-W64, so java/c/cpp are now available in a
    shell that inherits the post-install PATH — and absent in one started
    before it. Neither is asserted here: a test that fails on shell provenance
    teaches people to ignore it. What IS asserted is that the two runtimes
    this module's PASS cells depend on are present, and that Judge0 remains
    unavailable, because a Judge0 claim would be a claim about production.
    """
    assert RUNTIME["python"] is True
    assert RUNTIME["javascript"] is True
    assert JUDGE0_AVAILABLE is False


# ═════════════════════════════════════════════════════════════
# Java: structural only, and said so
# ═════════════════════════════════════════════════════════════

def test_java_has_a_harness_and_a_structural_contract_not_yet_wired_in():
    java = execution_contract.V2_WRAPPERS["java"]

    assert "Solution" in java and "exactly one public method" in java
    assert "class TreeNode" in execution_contract.STRUCTURAL_PRELUDE_JAVA
    assert "class ListNode" in execution_contract.STRUCTURAL_PRELUDE_JAVA
    # Still not wired in after M10 installed a JVM. A runtime makes the Java
    # structural adapter TESTABLE, not done — wiring it is its own milestone,
    # and shipping an unexecuted structural harness to learners would be worse
    # than saying it is not ready.
    assert "java" not in execution_contract.STRUCTURAL_PRELUDES


# ═════════════════════════════════════════════════════════════
# Readiness feeds serving, and is not overridden by execution work
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_a_language_unready_starter_is_never_advertised_as_ready(topic):
    """
    M1's rule, re-checked at the gate: readiness is reported from the starter,
    and no amount of adapter work makes an absent starter servable.
    """
    question = make_question(topic, TREE_STARTER)

    ready = language_readiness.ready_languages(question)
    blocked = language_readiness.blocked_languages(question)

    assert "python" in ready                       # v2 + a buildable structure
    for language in ("c", "cpp"):
        assert language in blocked
        assert language not in ready


@pytest.mark.django_db
def test_readiness_still_separates_a_contract_gap_from_an_unsupported_type(
        topic):
    """M5's split, verified through a real question rather than a source."""
    v1_tree = make_question(topic, TREE_STARTER,
                            version=execution_contract.CONTRACT_V1)
    unsupported = make_question(
        topic, "class Solution:\n    def probe(self, root: Node) -> int:\n"
               "        pass\n")

    assert language_readiness.assess(v1_tree, "python").cause == \
        language_readiness.STRUCTURAL_TYPE
    assert language_readiness.assess(unsupported, "python").cause == \
        language_readiness.STRUCTURAL_UNSUPPORTED


# ═════════════════════════════════════════════════════════════
# Trust x language — the M3 matrix, at the gate
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def verified_in_python(db, topic):
    """A question the oracle verified in Python, and nothing else."""
    return make_question(
        topic, starter_for(["int"]),
        status=Question.STATUS_PUBLISHED,
        trust_state=Question.TRUST_ORACLE_VERIFIED,
        verified_language="python")


@pytest.mark.django_db
@pytest.mark.parametrize("language,eligible", [
    ("python", True),
    ("java", False),
    ("javascript", False),
    ("c", False),
    ("cpp", False),
])
def test_only_the_verified_language_is_adaptive_eligible(
        verified_in_python, language, eligible):
    """
    The M3 invariant, stated as the matrix the brief asks for. The oracle ran
    one reference in one language; that is the only language its answer key
    demonstrably grades correctly.
    """
    assert verified_in_python.adaptive_eligible_for(language) is eligible


@pytest.mark.django_db
def test_an_unverified_question_is_eligible_in_no_language(topic):
    question = make_question(topic, starter_for(["int"]))

    for language in ("python", "java", "javascript", "c", "cpp"):
        assert question.adaptive_eligible_for(language) is False


@pytest.mark.django_db
def test_execution_eligibility_and_adaptive_trust_are_different_questions(
        topic):
    """
    The conflation the brief warns against, pinned. A question can be perfectly
    executable and carry no trust at all — readiness says "this can run",
    trust says "the answer key has been checked".
    """
    question = make_question(topic, starter_for(["int"]))

    assert language_readiness.assess(question, "python").verdict == \
        language_readiness.READY
    assert question.adaptive_eligible_for("python") is False


# ═════════════════════════════════════════════════════════════
# THE gate invariant: a mismatched language must not teach the model
# ═════════════════════════════════════════════════════════════

@pytest.fixture
def learner(db):
    return User.objects.create_user(username="m9-learner", password="pw",
                                    email="m9@example.com")


class _Grade:
    """The shape ProgressionService reads from a graded submission."""

    def __init__(self, all_passed=True, status="accepted"):
        self.all_passed = all_passed
        self.final_status = status
        self.stored_code = "submitted source"
        self.results = [{"time": "0.01", "memory": 1024}]


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["java", "javascript", "c", "cpp"])
def test_a_mismatched_language_submission_cannot_move_learner_state(
        verified_in_python, learner, language):
    """
    THE most important invariant in this gate.

    The learner still practises: the verdict is computed and the attempt is
    recorded. What must not happen is the learner MODEL moving, because the
    oracle never demonstrated that this question's answer key grades this
    language correctly — so a Wrong Answer here may be the platform's defect
    rather than the learner's mistake.
    """
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    profile.elo_rating = 1234.0
    profile.save()
    mastery, _ = UserTopicMastery.objects.get_or_create(
        user=learner, topic=verified_in_python.topic)
    # Every learner-model column ProgressionService can touch: the rating, the
    # mastery mean, and the SM-2/HLR retention state.
    before = {
        "elo": profile.elo_rating,
        "accuracy": mastery.accuracy,
        "reviews": mastery.reviews,
        "hlr_halflife": mastery.hlr_halflife,
        "hlr_alpha": mastery.hlr_alpha,
        "mastery_elo": mastery.elo_rating,
    }

    submission, elo_result, profile = ProgressionService.apply_submission(
        learner, verified_in_python, language, 1300.0, _Grade())

    submission.refresh_from_db()
    profile.refresh_from_db()
    mastery.refresh_from_db()

    # The attempt IS recorded — the learner practised.
    assert CodeSubmission.objects.filter(
        user=learner, question=verified_in_python).count() == 1
    assert submission.language == language
    assert submission.status == "accepted"

    # And nothing in the learner model moved.
    assert submission.adaptive_eligible is False
    assert elo_result["rating_change"] == 0.0
    assert profile.elo_rating == before["elo"]
    assert mastery.accuracy == before["accuracy"]
    assert mastery.reviews == before["reviews"]
    assert mastery.hlr_halflife == before["hlr_halflife"]
    assert mastery.hlr_alpha == before["hlr_alpha"]
    assert mastery.elo_rating == before["mastery_elo"]


@pytest.mark.django_db
def test_the_verified_language_DOES_move_learner_state(verified_in_python,
                                                       learner):
    """
    The control. Without it the test above would pass just as well against a
    system that had stopped updating anything at all.
    """
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    profile.elo_rating = 1234.0
    profile.save()

    submission, elo_result, profile = ProgressionService.apply_submission(
        learner, verified_in_python, "python", 1300.0, _Grade())

    submission.refresh_from_db()
    profile.refresh_from_db()
    assert submission.adaptive_eligible is True
    assert elo_result["rating_change"] != 0.0
    assert profile.elo_rating != 1234.0


@pytest.mark.django_db
def test_an_untrusted_question_records_the_attempt_and_moves_nothing(
        topic, learner):
    """
    The other half: execution where supported, no learner-model write. An
    untrusted question is practice, and practice is not evidence.
    """
    question = make_question(topic, starter_for(["int"]))
    profile, _ = UserCodingProfile.objects.get_or_create(user=learner)
    profile.elo_rating = 1500.0
    profile.save()

    submission, elo_result, profile = ProgressionService.apply_submission(
        learner, question, "python", 1300.0, _Grade())

    profile.refresh_from_db()
    assert CodeSubmission.objects.filter(user=learner).count() == 1
    assert submission.adaptive_eligible is False
    assert profile.elo_rating == 1500.0
    assert "not yet verified" in elo_result["insight"]


# ═════════════════════════════════════════════════════════════
# Trust safety: execution work cannot mint trust
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_building_and_feeding_an_executable_creates_no_trust(topic):
    """
    The whole execution seam, exercised, then every trust artifact counted.
    Nothing in M4/M5/M6/M8 has a write path to trust, and this is the
    assertion that says so rather than the absence of one.
    """
    from groups.models import (OracleExecution, QuestionApproval,
                               ReferenceSolution)

    question = make_question(topic, TREE_STARTER)
    before = (Question.objects.filter(
                  trust_state=Question.TRUST_ORACLE_VERIFIED).count(),
              Question.objects.filter(status=Question.STATUS_PUBLISHED).count(),
              ReferenceSolution.objects.count(),
              OracleExecution.objects.count(),
              QuestionApproval.objects.count())

    for canonical, _label in ((c, l) for l, c in TREES):
        GradingService._build_executable(question, "python", PY_ECHO)
        GradingService.prepare_stdin(question, "python", canonical)
    GradingService.quality_execution_plan(question)

    question.refresh_from_db()
    after = (Question.objects.filter(
                 trust_state=Question.TRUST_ORACLE_VERIFIED).count(),
             Question.objects.filter(status=Question.STATUS_PUBLISHED).count(),
             ReferenceSolution.objects.count(),
             OracleExecution.objects.count(),
             QuestionApproval.objects.count())

    assert before == after
    assert question.trust_state == Question.TRUST_UNVERIFIED
    assert question.status == Question.STATUS_DRAFT
    assert question.verified_language in (None, "")
    assert question.is_adaptive_eligible is False


def test_no_execution_module_can_write_a_trust_field():
    """
    Structural, and broader than any single scenario: the modules M4-M8 built
    must have no write path at all.

    The check is "does this module reach the ORM", not "does it call something
    named update" — `names.update(...)` on a set is not a queryset write, and
    a test that cannot tell them apart would have to be either wrong or
    disabled. Reaching `.objects` is the honest signal, because every ORM
    write in this codebase goes through a manager.
    """
    import ast
    import inspect
    import pathlib

    from groups import execution_adapter, language_readiness as lr
    from groups import structural_types as st

    forbidden = {"trust_state", "status", "verified_language",
                 "is_adaptive_eligible"}
    for module in (execution_contract, execution_adapter, lr, st,
                   content_quarantine):
        source = pathlib.Path(inspect.getfile(module)).read_text("utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assert target.attr not in forbidden, module.__name__
            if isinstance(node, ast.Attribute):
                assert node.attr != "objects", \
                    f"{module.__name__} reaches the ORM"
            if isinstance(node, ast.Call) and isinstance(node.func,
                                                         ast.Attribute):
                assert node.func.attr != "save", \
                    f"{module.__name__} calls save()"


# ═════════════════════════════════════════════════════════════
# q98: quarantined, not corrected
# ═════════════════════════════════════════════════════════════

def test_q98_is_quarantined_with_the_evidence_that_produced_it():
    entry = content_quarantine.entry_for(98)

    assert entry is not None
    assert "[1,null,2,null,3]" in entry.finding
    assert "valid binary search tree" in entry.finding
    assert "oracle" in entry.resolution


def test_the_quarantine_blocks_approval_rather_than_editing_data():
    """
    The mechanism, not a note in a report. The approval command reads this
    registry and appends a REFUSAL; it has no path to change a stored answer.
    """
    import inspect

    from groups.management.commands import question_approve

    source = inspect.getsource(question_approve)

    assert "content_quarantine.blocker_for" in source
    assert "problems.append(quarantine)" in source
    blocker = content_quarantine.blocker_for(98)
    assert blocker and "quarantined for content review" in blocker


def test_the_quarantine_changes_nothing_about_the_question():
    """
    Quarantine is a reason to look, addressed to a person. It must not be a
    silent edit, a status change, or a way to improve a readiness number.
    """
    import ast
    import inspect
    import pathlib

    tree = ast.parse(pathlib.Path(
        inspect.getfile(content_quarantine)).read_text("utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Import) or all(
            alias.name != "django" for alias in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in ("save", "update", "create", "delete")


def test_an_unquarantined_question_is_unaffected():
    assert content_quarantine.blocker_for(226) is None
    assert content_quarantine.is_quarantined(226) is False


# ═════════════════════════════════════════════════════════════
# K1 / K2: do the M8 findings break Phase 1?
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
@requires("javascript")
def test_K1_the_annotation_stripping_divergence_is_bounded(topic, learner):
    """
    K1: a Python learner who deletes the annotation gets the length rule while
    JavaScript honours the question's declaration.

    Bounded, and measured here rather than argued: it needs the learner to
    CONTRADICT the starter they were handed, and — this is the part that
    decides whether it is a Phase-1 failure — it cannot reach the learner
    model, because a question whose answers are unverified is not adaptive
    eligible in ANY language, and one verified in Python is not eligible for a
    JavaScript submission either. So the divergence can produce a wrong verdict
    on an untrusted question; it cannot produce a wrong RATING.
    """
    question = make_question(topic, starter_for(["list[int]"]))
    stripped = textwrap.dedent("""
        import json
        class Solution:
            def probe(self, a0):
                return json.dumps([a0], separators=(",", ":"))
    """)

    python = execute(question, "python", stripped, "5")[0]
    javascript = execute(question, "javascript", js_probe(1), "5")[0]

    assert json.loads(python) == [5]          # the length rule
    assert json.loads(javascript) == [[5]]    # the declared kind
    # The bound: neither can move learner state on this question.
    assert question.adaptive_eligible_for("python") is False
    assert question.adaptive_eligible_for("javascript") is False


@pytest.mark.django_db
def test_K2_v3_questions_refuse_other_languages_loudly(topic):
    """
    K2: v3's stdin envelope is built for Python only, and q1974/q1436/q3309 are
    v3, ORACLE_VERIFIED and PUBLISHED.

    Whether that is a Phase-1 FAILURE depends entirely on what happens to a
    JavaScript submission, so this executes the decision. It raises a named
    ExecutionContractError — no verdict, no silent wrong answer, and no
    learner-state write. A capability gap that fails loudly, not a
    correctness defect.
    """
    from groups.services import ExecutionContractError

    question = make_question(topic, starter_for(["int"]),
                             version=execution_contract.CONTRACT_V3)

    assert GradingService.prepare_stdin(question, "python", "5") is not None
    for language in ("javascript", "java", "c", "cpp"):
        with pytest.raises(ExecutionContractError, match="python only"):
            GradingService.prepare_stdin(question, language, "5")


# ═════════════════════════════════════════════════════════════
# One representation for learner, reference and oracle
# ═════════════════════════════════════════════════════════════

@pytest.mark.django_db
def test_the_reference_path_and_the_learner_path_are_the_same_code(topic):
    """
    The trust pipeline's precondition. If these ever differ, an answer key can
    be minted under semantics no learner experiences.
    """
    question = make_question(topic, TREE_STARTER)
    plan = GradingService.quality_execution_plan(question)

    for _label, canonical in TREES:
        assert plan.prepare_stdin(canonical, "python") == \
            GradingService.prepare_stdin(question, "python", canonical)
    assert plan.build_executable(PY_ECHO, "python") == \
        GradingService._build_executable(question, "python", PY_ECHO)[0]


@pytest.mark.django_db
@requires("javascript")
def test_the_server_side_structures_match_what_the_harness_builds(topic):
    """
    `structural_types` builds structures server-side for the reference path;
    the prelude builds them inside the sandbox for the learner. Two
    implementations of one algorithm, executed against one fixture table.
    """
    question = make_question(topic, TREE_STARTER)

    for _label, canonical in TREES:
        server_side = structural_types.serialize_tree(
            structural_types.build_tree(json.loads(canonical)))
        assert json.loads(execute(question, "python", PY_ECHO,
                                  canonical)[0]) == server_side
        assert json.loads(execute(question, "javascript", JS_ECHO,
                                  canonical)[0]) == server_side
