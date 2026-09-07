"""
The canonical structural input representation (Phase 1 M5).

PURE. Standard library only — no ORM, no Django, no I/O, no clock. Same rule
as `execution_adapter`, and for the same reason: the learner path, the
reference path and the oracle path all reach structural values through this
module, so there is exactly one definition of what `[1,null,2]` means.

── The representation was FOUND, not chosen ────────────────────────────────

The stored content already carries structural inputs, in more than one form.
Measured across the 124 Python questions whose starter declares a structural
type:

    trees   [2,1,3] / [5,1,4,null,null,3,6]   level-order JSON      75
            {-10,9,20,None,None,15,7}         braces + Python None
            3 1 4 3 None 1 5                  whitespace + None
            {"root": [3,9,20,null,null,15,7]} JSON object envelope
            1\\n2\\n3\\nNone                    one value per line

    lists   [1,2,3,4,5]                       JSON array            32
            1->2->3->4->5                     arrow notation
            2 4 3                             whitespace tokens (q2, q1265)

Level-order JSON is both the most common and the only form that is already
deterministic, language-independent, serializable and unambiguous, so it is
the canonical one. The others are NOT rewritten by this module: changing a
question's stored representation changes what its expected outputs mean, which
is an oracle-verified decision per question. They are reported in the M5
worklist as REQUIRES_REVIEW, and a question keeps executing exactly as it does
today until someone deliberately migrates it.

── The canonical forms ─────────────────────────────────────────────────────

TREE      A JSON array in level order (breadth-first). `null` marks an absent
          child. A null's children are not listed. Trailing nulls are omitted,
          so one tree has one spelling.

              []                    empty tree
              [1]                   single node
              [1,2,3]               balanced
              [1,2,3,4,5,6,7]       full
              [5,1,4,null,null,3,6] absent children
              [1,2,null,3]          left-skewed
              [1,null,2,null,3]     right-skewed
              [1,1,1]               duplicate values — allowed, not deduped
              [-10,9,20]            negative values

LINKED    A JSON array of the values in order.
LIST
              []                    empty list
              [1]                   single node
              [1,2,3]               multiple
              [-1,-1,2]             negatives and duplicates

Both use `[]` for empty, and both round-trip: `serialize(parse(x)) == x` for
every canonical x. That property is what lets the same fixture drive a
learner's run, a reference run and an oracle run.

── What is deliberately NOT here ───────────────────────────────────────────

CYCLES. q141 `hasCycle` stores `[3,2,0,-4]\\n1`, where the second field is
LeetCode's `pos`. Its declared signature takes ONE parameter, so the stored
data already contradicts the starter — a content defect, not a missing
feature. Inventing a cycle representation would add a structure no correct
question asks for.

`Node`. Seventy-five questions name it, and it is at least seven different
structures: N-ary tree (q1490), random-pointer list (q138), expression tree
(q1623), graph (q133), multilevel doubly-linked list (q430), skiplist (q1206),
quadtree (q558). `Node` is a NAME, not a type, and no single adapter can be
correct for it.

`ImmutableListNode`. One question (q1265, DRAFT). A real structure, but with a
deliberately restricted interface — the problem exists BECAUSE you cannot read
`.val` — and graded on printed side effects rather than a return value. Both
are separate contracts; see `UNSUPPORTED`.
"""

from collections import deque
from dataclasses import dataclass

# ── Kinds ───────────────────────────────────────────────────────────────────

TREE = "tree"
LINKED_LIST = "linked_list"


@dataclass(frozen=True)
class StructuralType:
    """
    One structural type the platform can deserialize, and how.

    `annotations` are the spellings a starter may use for it. They are matched
    as whole words against the annotation text, so `Optional[TreeNode]`,
    `'TreeNode'` and `TreeNode | None` all resolve to the same type — the
    three spellings that actually occur in the bank.
    """

    name: str
    kind: str
    #: Attribute names, in the order a child is visited. Trees have two; a
    #: linked list has one. This is what makes child ORDER part of the type
    #: rather than a convention repeated in each adapter.
    children: tuple
    #: The canonical empty value, as stored.
    empty: str = "[]"

    @property
    def is_tree(self):
        return self.kind == TREE


TREE_NODE = StructuralType("TreeNode", TREE, ("left", "right"))
LIST_NODE = StructuralType("ListNode", LINKED_LIST, ("next",))

#: Every structural type M5 supports. Deliberately two.
REGISTRY = (TREE_NODE, LIST_NODE)

#: Structural names that appear in real content but have NO adapter, with the
#: reason. Kept here rather than in a comment so `language_readiness` can
#: report WHY a question is unsupported instead of only that it is.
UNSUPPORTED = {
    "Node": ("`Node` names at least seven different structures in this bank "
             "(N-ary tree, random-pointer list, expression tree, graph, "
             "multilevel doubly-linked list, skiplist, quadtree); no single "
             "adapter can be correct for it"),
    "ImmutableListNode": ("a linked list with a deliberately restricted "
                          "interface, graded on printed output rather than a "
                          "return value; that is a separate execution "
                          "contract, not a parser gap"),
}


def by_annotation(annotation):
    """
    The structural type an annotation declares, or None.

    Whole-word matching, so `Optional[TreeNode]`, `'TreeNode'` and
    `TreeNode | None` all resolve — the three spellings that occur in the bank
    — while `MyTreeNodeThing` does not.

    Case-insensitive because `execution_adapter._annotation_text` lower-cases
    every annotation it reads, so by the time a declaration reaches here the
    original casing is already gone. Matching on the exact name would silently
    resolve nothing, which is a failure that looks like "no structural
    questions exist".
    """
    words = _words(annotation)
    for structural in REGISTRY:
        if structural.name.lower() in words:
            return structural
    return None


def unsupported_in(annotation):
    """The unsupported structural name an annotation declares, or None."""
    words = _words(annotation)
    for name in UNSUPPORTED:
        if name.lower() in words:
            return name
    return None


def _words(text):
    """Lower-cased identifier-shaped words in an annotation."""
    if not isinstance(text, str):
        return set()
    out, current = set(), []
    for character in text.lower():
        if character.isalnum() or character == "_":
            current.append(character)
        elif current:
            out.add("".join(current))
            current = []
    if current:
        out.add("".join(current))
    return out


# ── The Python structures ───────────────────────────────────────────────────
#
# Defined here AND injected into the v2 Python harness as source text. They
# have to be real classes here so the reference and oracle paths can build and
# compare structures server-side, and source text there because the harness
# runs inside Judge0 with nothing importable. `test_structural_types.py`
# executes both and asserts they agree, which is what keeps one from drifting.

class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next


# ── Deserialization ─────────────────────────────────────────────────────────

def build_tree(values):
    """
    A TreeNode from a level-order list, or None for the empty tree.

    `values` is the DECODED JSON array, not text — parsing text is
    `execution_adapter`'s job and there must not be a second JSON reader.
    """
    if not values or values[0] is None:
        return None

    root = TreeNode(values[0])
    queue = deque([root])
    index = 1
    while queue and index < len(values):
        node = queue.popleft()
        for attribute in TREE_NODE.children:
            if index >= len(values):
                break
            value = values[index]
            index += 1
            if value is None:
                # A null has no children and contributes no queue entry. This
                # is the whole reason the format is compact: a null subtree
                # costs one token, not a whole level.
                continue
            child = TreeNode(value)
            setattr(node, attribute, child)
            queue.append(child)
    return root


def build_linked_list(values):
    """A ListNode chain from a list of values, or None for the empty list."""
    head = None
    for value in reversed(values or []):
        head = ListNode(value, head)
    return head


# ── Serialization / output normalization ────────────────────────────────────

def serialize_tree(root):
    """
    The canonical level-order list for a tree.

    Trailing nulls are trimmed so that one tree has exactly one spelling —
    without it, `[1,2,3]` and `[1,2,3,null,null,null,null]` would be the same
    tree and different expected outputs, and a correct submission would fail
    on formatting.
    """
    if root is None:
        return []

    out = []
    queue = deque([root])
    while queue:
        node = queue.popleft()
        if node is None:
            out.append(None)
            continue
        out.append(node.val)
        for attribute in TREE_NODE.children:
            queue.append(getattr(node, attribute, None))

    while out and out[-1] is None:
        out.pop()
    return out


def serialize_linked_list(head):
    """
    The canonical value list for a linked list.

    Bounded: a cyclic chain would otherwise hang the grader, and a hang is
    reported as a timeout, which reads to a learner as "too slow" rather than
    "your list has a cycle". The bound is generous enough that no acyclic list
    in this bank reaches it.
    """
    out = []
    seen = set()
    node = head
    while node is not None:
        if id(node) in seen:
            raise ValueError(
                "linked list contains a cycle; the canonical representation "
                "has no spelling for one")
        seen.add(id(node))
        out.append(node.val)
        node = getattr(node, "next", None)
    return out


def serialize(value):
    """
    Normalize a returned value if it IS a structure, otherwise return it.

    Used on the return path so a `TreeNode` comes back as `[1,2,3]` and never
    as a language-specific object repr. Anything that is not a node — an int, a
    list, a bool — is already comparable and is passed through untouched.
    """
    if isinstance(value, TreeNode):
        return serialize_tree(value)
    if isinstance(value, ListNode):
        return serialize_linked_list(value)
    return value
