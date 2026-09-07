# Structural inputs (Phase 1 M5)

One canonical, language-independent representation; deterministic adapters per
execution model. There is no third execution model and no per-question
structural hack.

```
stored canonical text        "[5,1,4,null,null,3,6]"
        ↓
structural type descriptor   structural_types.TREE_NODE  (kind=tree, children=(left,right))
        ↓
kind vector                  v2_parameter_kinds(starter) -> ["tree"]
        ↓
language adapter             prelude injected above the learner's code
        ↓
learner-facing object        TreeNode / ListNode
        ↓
execution
        ↓
return normalization         serialize_tree(...) -> "[5,1,4,null,null,3,6]"
```

## The representation was found, not chosen

The bank already carries structural inputs — in **five** different forms.
Measured across the 124 Python questions whose starter declares a structural
type:

| structure | form | example | count |
|---|---|---|---:|
| tree | level-order JSON | `[5,1,4,null,null,3,6]` | **75** |
| tree | braces + Python `None` | `{-10,9,20,None,None,15,7}` | 45 |
| tree | whitespace + `None` | `3 1 4 3 None 1 5` | 37 |
| tree | JSON object envelope | `{"root": [3,9,20,null,null,15,7]}` | — |
| tree | one value per line | `1\n2\n3\nNone` | — |
| list | JSON array | `[1,2,3,4,5]` | **32** |
| list | arrow notation | `1->2->3->4->5` | 13 |
| list | whitespace tokens | `2 4 3` (q2, q1265) | — |

(Counts overlap: one question may use several forms across its cases.)

Level-order JSON is both the most common and the only one already
deterministic, language-independent, serializable and unambiguous. It is
therefore the canonical form — a **selection from what exists**, not a new
invention.

**The other four are not rewritten.** Changing a question's stored
representation changes what its expected outputs mean, which is an
oracle-verified decision per question. They are classified in the worklist
below and keep executing exactly as they do today.

## Tree

A JSON array in level order (breadth-first). `null` marks an absent child. A
null's children are **not** listed. Trailing nulls are omitted, so one tree has
exactly one spelling.

| case | canonical |
|---|---|
| empty | `[]` |
| single node | `[1]` |
| balanced | `[1,2,3]` |
| full | `[1,2,3,4,5,6,7]` |
| absent children | `[5,1,4,null,null,3,6]` |
| left-skewed | `[1,2,null,3]` |
| right-skewed | `[1,null,2,null,3]` |
| duplicate values | `[1,1,1]` |
| negative values | `[-10,9,20,null,null,15,7]` |

Child order is `("left", "right")`, declared once on the type descriptor rather
than repeated in each adapter.

## Linked list

A JSON array of the values in order.

| case | canonical |
|---|---|
| empty | `[]` |
| single node | `[1]` |
| multiple | `[1,2,3]` |
| duplicates | `[7,7,7,7]` |
| negatives | `[-1,-1,2]` |

**No cycle representation.** q141 `hasCycle` stores `[3,2,0,-4]\n1`, where the
second field is LeetCode's `pos` — but its declared signature takes ONE
parameter, so the stored data already contradicts the starter. That is a
content defect, not a missing feature, and inventing a cycle spelling would add
a structure no correct question asks for. `serialize_linked_list` **refuses** a
cyclic chain rather than hanging: a hang is reported as a timeout, which reads
to a learner as "too slow" rather than "this cannot be represented".

## Round-trip

`serialize(build(x)) == x` for every canonical `x`, in both executed languages.
Without it one tree would have several spellings and a correct submission would
fail on formatting rather than on being wrong. This property is what lets a
learner run, a reference run and an oracle run share one fixture.

## The two execution models, unchanged

| model | languages | what M5 does |
|---|---|---|
| **Reflection** | python, javascript | prelude injected **above** the learner's code: node class, builder, serializer |
| **Reflection** | java | contract written down (`STRUCTURAL_PRELUDE_JAVA`), **not wired in** — no JVM here, so it is unexecuted |
| **Self-contained** | c, cpp | **nothing.** The canonical text reaches the program on stdin unaltered and the learner parses it |

`STRUCTURAL_PRELUDES` has no `c` or `cpp` entry, and that absence is the
contract — exactly as `V2_WRAPPERS` has none. Injecting a node class into a
self-contained language would be the first step of turning it into a reflection
language.

### Why the prelude goes *above* the learner's code

Two reasons, both load-bearing:

1. `TreeNode | None` is evaluated when the learner's class is **defined**. A
   node class defined after their code raises `NameError` before their first
   line — the M6 defect, from the harness side.
2. A learner who writes their own `TreeNode` must win. Theirs is defined later,
   so it shadows the prelude's. The platform never overwrites submitted code.

This is the layout **q2's shipped per-question wrapper already uses** — the only
structural deserializer in production — so the ordering is precedent.

## Output normalization

A returned node is normalized to the canonical array **before anything else
looks at it**, so grading never compares a language's object identity. Rendered
as compact JSON rather than v2's space-joined tokens, because a tree carries
`null` for an absent child and joining would make an absent child
indistinguishable from a missing value.

**The empty structure and "no value" are the same object** in every reflection
language. Only the declared return type separates them, so `v2_return_kind` is
computed server-side from the starter and injected into both harnesses:
`-> TreeNode | None` returning `None` is the **empty tree** and prints `[]`;
`-> None` returning `None` prints nothing.

## Type inventory

| name | decision | reason |
|---|---|---|
| `TreeNode` | **supported** | one meaning throughout the bank |
| `ListNode` | **supported** | one meaning throughout the bank |
| `Node` | **unsupported** | at least **seven** different structures: N-ary tree (q1490), random-pointer list (q138), expression tree (q1623), graph (q133), multilevel doubly-linked list (q430), skiplist (q1206), quadtree (q558). A name, not a type — one adapter would silently mis-execute the other six |
| `ImmutableListNode` | **unsupported** | q1265 only. A real structure, but with a deliberately restricted interface (the problem exists *because* you cannot read `.val`) and graded on printed side effects rather than a return value. A different execution contract, not a parser gap |

`Node` and `ImmutableListNode` are **classified, not relabelled**: both report
`structural_unsupported` with the reason above, and neither counts as ready.

## Readiness

The old single `structural_type` verdict answered two different questions with
one word. It is now split:

| cause | meaning |
|---|---|
| `structural_type` | the platform **can** build this, but not at the contract version this question declares. A scheduling decision |
| `structural_unsupported` | no adapter exists and none is planned, with the reason named. An architecture decision |

A structural question is **not** marked READY merely because the parser exists.
Every one of the 124 declares **v1**, so the adapter changes none of them —
migration is per question, with oracle re-verification.

## Worklist

| bucket | count | what it needs |
|---|---:|---|
| `SAFE_REPAIR` | **31** | stored data is already canonical; needs a v2 migration |
| `REQUIRES_REVIEW` (representation) | **79** | stored data is in one of the four non-canonical forms |
| `REQUIRES_REVIEW` (no usable cases) | **1** | q92 |
| `UNSUPPORTED` — `Node` | **13** | needs a per-structure decision first |
| `UNSUPPORTED` — `ImmutableListNode` | **1** | q1265, restricted-interface contract |

`SAFE_REPAIR` means **the representation is canonical and the adapter executes
it**. It does not mean the answer key is correct — that is what oracle
verification establishes, and the pilot below found a counter-example.

## Pilot — real questions, real stored outputs

Read-only, in memory. No question was migrated, promoted or altered.

| question | result |
|---|---|
| **q226** Invert Binary Tree | **4/4 match**, both languages, including `[]` → `[]` and the structural return |
| **q98** Validate BST | **3/4 match.** The fourth, `[1,null,2,null,3]`, is 1 → right 2 → right 3 — in-order 1,2,3, a **valid** BST. Both languages independently answer `true`; the **stored expected output says `false`**. The answer key is wrong, and this is left for oracle verification rather than "fixed" |
| **q104** Maximum Depth | **fails loudly**, as predicted: its stdin is `3,9,20,null,null,15,7` — bare commas, no brackets. Classified `REQUIRES_REVIEW`, and the failure is a named `JSONDecodeError`, not a silent wrong answer |

The pilot validates the worklist classifier itself: `SAFE_REPAIR` questions
execute, `REQUIRES_REVIEW` questions refuse visibly.

## What is NOT claimed

- **Judge0 was not used.** All execution here is local `python` and `node`
  subprocesses.
- **Java is not executed.** No JVM in this environment; the Java structural
  contract is written down and asserted structurally, and M7 is where it gets
  compiled.
- **C and C++ are not executed** either, and are not changed — the tests assert
  the canonical text reaches the program unaltered.
