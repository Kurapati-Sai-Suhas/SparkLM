"""
`Optional[X]` -> `X | None` as a canonical repair (Phase 1 M13).

── Why this is a spelling change and not a type change ─────────────────────

`Optional[X]` MEANS `X | None` and always has. The two annotations denote the
same type, so rewriting one to the other changes how the contract is written
and nothing about what it says. That is the only reason a governed,
trust-adjacent command is allowed to make it without a human reading each one.

The reason it MUST be made is that `Optional` is not a builtin and the
reflection harness emits no imports, so `Optional[TreeNode]` raises
`NameError` at class-definition time — before the learner's first line. 22
questions in the bank name `Optional`; 11 name `List`. They are the entire
remaining `undefined_annotation` population.

── Why it is deliberately two rules and not a typing normaliser ────────────

`CANONICAL_GENERICS` renames a node. This restructures a subscript. They are
kept apart so the next contributor is not invited to add `Union`, `Sequence`
and `Mapping` behind them, and `Union[X, None]` is explicitly NOT handled:
it is spelled several ways (`Union[None, X]`, `Union[X, Y, None]`) and each
would be a separate judgement rather than one mechanical rule.

Anything the rule cannot do mechanically returns the annotation UNCHANGED,
which `remediate_boilerplate` reads as "no canonical repair exists" and
refuses. A best effort would be the general rewriter this is not.
"""

import ast
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from groups import language_readiness, pre_image, structural_types
from groups import execution_contract as ec
from groups.models import (CodingPortal, Question, RemediationAction,
                           RemediationBatch, Topic)

User = get_user_model()


def canonical(text):
    """The canonical spelling of one annotation, written as source."""
    return language_readiness.canonical_annotation(
        ast.parse(text, mode="eval").body)


# ═════════════════════════════════════════════════════════════
# The rule
# ═════════════════════════════════════════════════════════════

REWRITTEN = (
    ("Optional[int]", "int | None"),
    ("Optional[str]", "str | None"),
    ("Optional[TreeNode]", "TreeNode | None"),
    ("Optional[ListNode]", "ListNode | None"),
    ("Optional[List[int]]", "list[int] | None"),
    ("Optional[list[list[str]]]", "list[list[str]] | None"),
    ("List[Optional[int]]", "list[int | None]"),
)


@pytest.mark.parametrize("before,after", REWRITTEN,
                         ids=[row[0] for row in REWRITTEN])
def test_optional_is_rewritten_to_a_pep604_union(before, after):
    assert canonical(before) == after


UNCHANGED = (
    ("already canonical", "TreeNode | None"),
    ("already canonical builtin", "list[int]"),
    ("a plain name", "int"),
    ("a structural name", "TreeNode"),
    # Union is several spellings, so each would be a judgement, not a rule.
    ("Union with None", "Union[int, None]"),
    ("Union without None", "Union[int, str]"),
    # Deque's replacement is not a builtin — M6.1's reason, unchanged.
    ("a non-builtin alias", "Deque[int]"),
    ("nested Optional", "Optional[Optional[int]]"),
)


@pytest.mark.parametrize("label,text", UNCHANGED,
                         ids=[row[0] for row in UNCHANGED])
def test_what_the_rule_deliberately_leaves_alone(label, text):
    """
    Returning the annotation UNCHANGED is how the rule says "no canonical
    repair exists" — `remediate_boilerplate` then refuses, because a proposal
    must EQUAL the canonical form and an unchanged one never will.
    """
    assert canonical(text) == text


def test_nested_optional_is_refused_rather_than_unwrapped_twice():
    """
    Regression for a real defect in this milestone: `generic_visit` is
    depth-first, so a nesting check placed after it saw an inner `Optional`
    that had already been rewritten. `Optional[Optional[int]]` came out as
    `int | None | None` — neither canonical nor what anyone wrote.
    """
    assert canonical("Optional[Optional[int]]") == "Optional[Optional[int]]"
    assert canonical("Optional[Optional[TreeNode]]") == \
        "Optional[Optional[TreeNode]]"


MALFORMED = (
    # `generic_visit` is depth-first, so by the time the arity check fires the
    # inner `List` has ALREADY been renamed to `list`. Without the refusal
    # guard the result would be `Optional[list[int], str]` — a half-applied
    # rewrite of something nobody can write correctly.
    ("two arguments", "Optional[List[int], str]"),
    ("nested around a generic", "Optional[Optional[List[int]]]"),
)


@pytest.mark.parametrize("label,text", MALFORMED,
                         ids=[row[0] for row in MALFORMED])
def test_a_refused_rewrite_is_abandoned_whole_not_left_half_applied(label,
                                                                    text):
    """
    Found by a mutation that SURVIVED: deleting the `refused` guard changed
    nothing, because every refusal path the suite covered happened to return
    an untouched subtree anyway. This is the path where it is load-bearing.
    """
    assert canonical(text) == text


def test_the_refusal_reason_is_recorded_rather_than_swallowed():
    """A caller debugging a refusal should be able to see which rule stopped it."""
    for text, expected in (("Optional[List[int], str]", "exactly one argument"),
                           ("Optional[Optional[int]]", "nested Optional")):
        canonicaliser = language_readiness._Canonicaliser()
        canonicaliser.visit(ast.parse(text, mode="eval").body)
        assert expected in canonicaliser.refused


def test_the_rule_is_idempotent():
    """Canonical text is already canonical; applying it twice changes nothing."""
    for before, after in REWRITTEN:
        assert canonical(after) == after


def test_optional_is_the_only_restructuring_rule():
    """
    A structural pin. The map renames; exactly one name restructures. Adding
    `Union` or `Sequence` here would be the general typing rewriter this is
    deliberately not, and it should be a visible change to this test.
    """
    assert language_readiness._OPTIONAL == "Optional"
    for name in ("Union", "Sequence", "Mapping", "Iterable", "Annotated",
                 "Literal", "Final"):
        assert name not in language_readiness.CANONICAL_GENERICS
        assert canonical(f"{name}[int]") == f"{name}[int]"


# ═════════════════════════════════════════════════════════════
# The structural safety guard
# ═════════════════════════════════════════════════════════════

def test_the_rewrite_preserves_which_structural_type_is_named():
    for before, after in REWRITTEN:
        assert structural_types.by_annotation(before) is \
            structural_types.by_annotation(after)


def test_canonicalisation_never_introduces_or_removes_a_structure():
    """
    Defence in depth. No rule above can do this today; this is what stops the
    next one from doing it quietly — a repair that turned `Optional[int]` into
    a tree, or a tree into a scalar, would change what the learner is handed
    while wearing a spelling change's clothes.
    """
    for text in ("Optional[int]", "Optional[TreeNode]", "Optional[ListNode]",
                 "List[int]", "Optional[List[TreeNode]]", "int", "TreeNode"):
        assert structural_types.by_annotation(text) is \
            structural_types.by_annotation(canonical(text))


def test_the_guard_refuses_a_rewrite_that_changed_the_structure(monkeypatch):
    """
    The guard, forced to fire. It cannot fire through the real rules, so a
    mutation sweep could delete it with everything still green — and losing it
    would let a future rule relabel a structural parameter silently.
    """
    class Relabel(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.Name(id="TreeNode", ctx=node.ctx)

    real = language_readiness._Canonicaliser      # captured BEFORE patching

    def rigged():
        canonicaliser = real()
        canonicaliser.visit = Relabel().visit
        canonicaliser.refused = None
        return canonicaliser

    monkeypatch.setattr(language_readiness, "_Canonicaliser", rigged)

    # The rigged rewrite turns `int` into `TreeNode` — a structure appearing
    # where there was none. The guard must return the original untouched.
    assert canonical("int") == "int"


# ═════════════════════════════════════════════════════════════
# Through the governed command
# ═════════════════════════════════════════════════════════════

#: The node class is DEFINED in these fixtures, and that is not incidental.
#: `remediate_boilerplate` refuses a repair that leaves ANY undefined name
#: behind, and 25 of the 28 candidates in the bank mention their node class
#: only in a COMMENT. For those the `Optional` half is repairable and the
#: `TreeNode` half is not, so the command refuses the whole proposal —
#: correctly, since it would trade one NameError for another. They are
#: unblocked by the v2 migration, whose prelude defines the class.
NODE_CLASSES = (
    "class TreeNode:\n"
    "    def __init__(self, val=0, left=None, right=None):\n"
    "        self.val = val\n"
    "        self.left = left\n"
    "        self.right = right\n"
    "\n"
    "class ListNode:\n"
    "    def __init__(self, val=0, next=None):\n"
    "        self.val = val\n"
    "        self.next = next\n"
    "\n"
)

TREE_BEFORE = NODE_CLASSES + (
    "class Solution:\n"
    "    def isValidBST(self, root: Optional[TreeNode]) -> bool:\n"
    "        pass\n")
TREE_AFTER = NODE_CLASSES + (
    "class Solution:\n"
    "    def isValidBST(self, root: TreeNode | None) -> bool:\n"
    "        pass\n")
LIST_BEFORE = NODE_CLASSES + (
    "class Solution:\n"
    "    def reverse(self, head: ListNode) -> Optional[ListNode]:\n"
    "        pass\n")
LIST_AFTER = NODE_CLASSES + (
    "class Solution:\n"
    "    def reverse(self, head: ListNode) -> ListNode | None:\n"
    "        pass\n")
JAVA = "class Solution { }"


@pytest.fixture
def operator(db):
    return User.objects.create_user(username="m13-op", password="pw",
                                    email="m13@example.com", is_staff=True)


@pytest.fixture
def topic(db):
    portal = CodingPortal.objects.create(name="M13 Portal")
    made, _ = Topic.objects.get_or_create(
        name="M13Topic", defaults={"structure_type": "flat", "portal": portal})
    return made


def make_question(topic, starter, pk):
    return Question.objects.create(
        id=pk, title=f"M13-{pk}", content="c", topic=topic,
        base_difficulty=1200.0,
        boilerplate_code={"python": starter, "java": JAVA},
        hidden_test_cases=[{"stdin": "[1]", "expected_output": "true"}],
        hidden_wrapper_code={}, execution_contract_version=ec.CONTRACT_V1)


@pytest.fixture
def tree_question(db, topic):
    return make_question(topic, TREE_BEFORE, 9310)


@pytest.fixture
def list_question(db, topic):
    return make_question(topic, LIST_BEFORE, 9311)


@pytest.fixture
def batch(db, operator, tree_question, list_question):
    made = RemediationBatch.objects.create(
        batch_key="m13-batch", purpose="test", created_by=operator)
    pre_image.capture(made, tree_question, operator)
    pre_image.capture(made, list_question, operator)
    pre_image.freeze(made, operator)
    return made


def repair(tmp_path, operator, question_id, source, name="s.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    call_command("remediate_boilerplate", "--batch", "m13-batch",
                 "--question", str(question_id), "--language", "python",
                 "--source-file", str(path), "--reason", "M13 canonical",
                 "--operator", operator.username, "--local", "--apply",
                 "--confirm")


@pytest.mark.django_db
def test_a_parameter_optional_repair_is_accepted(batch, tree_question,
                                                 operator, tmp_path):
    repair(tmp_path, operator, 9310, TREE_AFTER)

    tree_question.refresh_from_db()
    assert tree_question.boilerplate_code["python"] == TREE_AFTER
    assert ec.v2_parameter_kinds(TREE_AFTER) == [ec.TREE_KIND]


@pytest.mark.django_db
def test_a_RETURN_optional_repair_is_accepted(batch, list_question, operator,
                                              tmp_path):
    """
    The return path enforces the canonical form exactly (M6.1 condition 4), so
    this is the case the extension had to reach.
    """
    repair(tmp_path, operator, 9311, LIST_AFTER)

    list_question.refresh_from_db()
    assert list_question.boilerplate_code["python"] == LIST_AFTER
    assert ec.v2_return_kind(LIST_AFTER) == ec.LINKED_LIST_KIND


@pytest.mark.django_db
def test_the_repair_is_recorded_and_rollback_able(batch, tree_question,
                                                  operator, tmp_path):
    repair(tmp_path, operator, 9310, TREE_AFTER)

    action = RemediationAction.objects.get(question=tree_question)
    assert action.action_class == RemediationAction.CLASS_BOILERPLATE_REPAIR

    pre_image.rollback(batch, operator, questions=[tree_question])
    tree_question.refresh_from_db()
    assert tree_question.boilerplate_code["python"] == TREE_BEFORE


@pytest.mark.django_db
def test_the_repair_changes_no_trust_or_publication_state(batch, tree_question,
                                                          operator, tmp_path):
    repair(tmp_path, operator, 9310, TREE_AFTER)

    tree_question.refresh_from_db()
    assert tree_question.status == Question.STATUS_DRAFT
    assert tree_question.trust_state == Question.TRUST_UNVERIFIED
    assert tree_question.verified_language in (None, "")
    assert tree_question.is_adaptive_eligible is False
    assert tree_question.execution_contract_version == ec.CONTRACT_V1


def refused(tmp_path, operator, question_id, source, message):
    with pytest.raises(CommandError, match=message):
        repair(tmp_path, operator, question_id, source)


@pytest.mark.django_db
def test_a_return_rewritten_to_something_else_is_still_refused(
        batch, list_question, operator, tmp_path):
    """Condition 4 holds: the canonical form, not a chosen one."""
    refused(tmp_path, operator, 9311,
            LIST_BEFORE.replace("Optional[ListNode]", "int"),
            "canonical repair")


@pytest.mark.django_db
def test_Optional_of_an_unknown_name_is_refused(batch, list_question,
                                                operator, tmp_path):
    """
    `Optional[Unknown]` rewrites mechanically to `Unknown | None`, and the
    command still refuses it — the NameError is only half fixed. Refused at
    the layer that knows what a starter defines, not by teaching the spelling
    rule about scope.
    """
    refused(tmp_path, operator, 9311,
            LIST_BEFORE.replace("-> Optional[ListNode]", "-> Unknown | None"),
            "still names Unknown")


@pytest.mark.django_db
def test_a_body_change_alongside_the_repair_is_refused(batch, tree_question,
                                                       operator, tmp_path):
    refused(tmp_path, operator, 9310,
            TREE_AFTER.replace("        pass", "        return True"),
            "more than annotations")


@pytest.mark.django_db
def test_a_renamed_method_alongside_the_repair_is_refused(batch, tree_question,
                                                          operator, tmp_path):
    refused(tmp_path, operator, 9310,
            TREE_AFTER.replace("isValidBST", "validBST"),
            "more than annotations")


@pytest.mark.django_db
def test_a_parameter_and_return_repair_together_is_still_refused(
        batch, list_question, operator, tmp_path):
    """
    M6.1 condition 5, untouched by M13: each half is a separate audited
    repair. The extension widens what a return annotation may BECOME, never
    what one proposal may carry.
    """
    both = NODE_CLASSES + (
        "class Solution:\n"
        "    def reverse(self, head: ListNode | None) -> ListNode | None:\n"
        "        pass\n")

    refused(tmp_path, operator, 9311, both,
            "both a parameter annotation and the return annotation")


@pytest.mark.django_db
def test_an_already_canonical_return_cannot_be_rewritten(batch, list_question,
                                                         operator, tmp_path):
    """
    A return that already resolves is out of scope however it is spelled —
    the extension does not turn the return path into a general editor.
    """
    repair(tmp_path, operator, 9311, LIST_AFTER)

    refused(tmp_path, operator, 9311,
            LIST_AFTER.replace("ListNode | None:", "ListNode:"),
            "already resolves")


# ═════════════════════════════════════════════════════════════
# Readiness: the repair is what makes v2 possible, not v1 ready
# ═════════════════════════════════════════════════════════════

def test_the_repair_makes_a_starter_python_ready_under_v2():
    assert language_readiness.assess_source(
        TREE_BEFORE, "python", ec.CONTRACT_V2).cause == \
        language_readiness.UNDEFINED_ANNOTATION
    assert language_readiness.assess_source(
        TREE_AFTER, "python", ec.CONTRACT_V2).verdict == \
        language_readiness.READY


def test_the_repair_does_not_make_a_v1_question_ready():
    """
    A correct annotation makes a question MIGRATABLE, not ready. Under v1 the
    harness still passes a string where a tree is declared.
    """
    result = language_readiness.assess_source(TREE_AFTER, "python",
                                              ec.CONTRACT_V1)

    assert result.verdict == language_readiness.NOT_READY
    assert result.cause == language_readiness.STRUCTURAL_TYPE


def test_the_kind_vector_is_identical_before_and_after():
    """
    The strongest evidence that this is a spelling change: what the adapters
    derive from the declaration does not move.
    """
    assert ec.v2_parameter_kinds(TREE_BEFORE) == \
        ec.v2_parameter_kinds(TREE_AFTER) == [ec.TREE_KIND]
    assert ec.v2_return_kind(LIST_BEFORE) == \
        ec.v2_return_kind(LIST_AFTER) == ec.LINKED_LIST_KIND
