# v2 structural migration wave — prepared, not executed (Phase 1 M12)

> ## ⚠ SUPERSEDED, 2026-09-15 — do not act on the numbers below
>
> **The worklist is now `python manage.py v2_migration_worklist`** (read-only,
> `--json` for machine output). It is the authoritative inventory; this
> document is kept as the M12 record of how the analysis was reasoned about,
> not as a list to work from.
>
> Two claims here are now known to be false:
>
> * **Judge0 is not `NOT_SUBSCRIBED`.** Probed 2026-09-14: HTTP 200, 71
>   languages, all five SparkLM ids present. The real M14 blocker is the
>   operator-authored references, of which **0 of 28 candidates have one**.
> * **SAFE_TO_MIGRATE is 28, not 22**, over 130 structural questions rather
>   than 128. The count here was derived by inspection and never implemented,
>   so it could not be re-derived or corrected as content changed. Re-deriving
>   it found four defect classes the prose analysis had no way to catch:
>   structures that never reach the graded method (q382, q919), collections of
>   structures (q95, q652, q725), arity mismatches (q543 and 14 others), and
>   answer keys that stop matching once output is serialised canonically
>   (q450). Buckets `BLOCKED_BY_TEST_CONTRACT_ARITY`, `UNSUPPORTED_COLLECTION`
>   and `STATEFUL_STRUCTURE` did not exist in this document at all.
>
> The "Still blocked — 2 of 9" section below is also stale: M13 took the
> `Optional[X] → X | None` decision it describes as escalated, and q108 was
> repaired. q105 was not, and remains `BLOCKED_BY_STARTER`.

**Nothing in this document has been executed.** A v2 migration changes the
input representation and therefore what a question's stored expected outputs
*mean*. That is oracle work, and Judge0 is `NOT_SUBSCRIBED`.

## The nine candidates: what was actually wrong

M11 reported nine questions "blocked because their Python starter does not
declare the structural type". Inspecting each individually found **three
different causes**, and only three of the nine needed a content change.

### 1. An adapter defect — 5 of 9, and 68 questions bank-wide

`chosen_function` read the **first class in the file**. Every harness
instantiates `Solution`. The idiomatic structural starter defines its node
class first:

```python
class TreeNode:                 # ← read this one
    def __init__(...)           # ← filtered out as private
class Solution:                 # ← never reached
    def goodNodes(self, root: TreeNode) -> int:
```

so `declared_signature` returned `None` and the kind vector came back empty.
**All 68 starters in the bank that define a helper class before `Solution` were
affected.** It also silently refused every v3 envelope for those questions.

Fixed in `chosen_function` and `public_method_names`; the `classes[0]` fallback
is kept for starters that name their class something else. This resolved
**q21, q108, q298, q606** and part of q112/q199 with **no content change at
all**.

### 2. A genuinely wrong or missing annotation — 3 of 9

| q | before | after | evidence |
|---|---|---|---|
| **100** | `tree1: list[int], tree2: list[int]` | `tree1: TreeNode \| None, tree2: TreeNode \| None` | statement says "the roots of two binary trees"; Java starter declares `TreeNode`; stored data contains `[1,null,2]`, which is not a `list[int]` |
| **112** | `root` (unannotated) | `root: TreeNode \| None` | statement describes a height-balanced **binary tree**; Java declares `TreeNode root`; data is `[3,9,20,null,null,15,7]` |
| **199** | `root` (unannotated) | `root: TreeNode \| None` | statement is "Binary Tree Right Side View"; Java declares `TreeNode root`; data is `[1,2,3,null,5]` |

Three independent sources agree in every case: **statement, Java starter,
stored data**. Applied through `remediate_boilerplate` under frozen pre-images
in batch `p12-structural-annotations` — annotation-only, one line each, method
name and arity unchanged.

**q100's stored outputs stay valid** because canonical level-order
serialization is bijective: two trees are equal iff their canonical arrays are.
All four stored cases hold under either reading.

### 3. A defect in M11's own classifier — 2 of 9

It asked only whether a **parameter** was structural. **q105, q106 and q108**
build a tree *from* two arrays — the structure is in the **return**. Their
parameters really are sequences. Nothing to repair.

### Still blocked — 2 of 9

**q105 and q108** declare `List[int]` and `Optional[TreeNode]`, both undefined
at runtime. The repair is `list[int]` and `TreeNode | None`, but
`remediate_boilerplate`'s canonical-form rule maps typing generics that have
**builtin** counterparts, and `Optional` has none — so the command refuses it.

Widening a trust-adjacent command is a decision, not a side effect. **This is
the same call M6.1 escalated rather than took.** The narrow extension would be
to accept `Optional[X] → X | None` as canonical, which M5 already established
as this project's spelling for an optional structure.

## Readiness went **down**, and that is correct

| | READY | NOT_READY | `structural_type` |
|---|---:|---:|---:|
| python before | 1,578 | 210 | 110 |
| python **after** | **1,575** | **213** | **113** |

q100, q112 and q199 were reported READY because their declaration was *wrong* —
an untyped or `list[int]` parameter has no structural blocker. Now that they
declare a tree under a v1 contract, they are correctly NOT_READY. **The repair
made an existing defect visible.**

Java is unchanged (541 UNKNOWN / 1,247 NOT_READY): Java readiness reads the
*Java* starter, which already named `TreeNode`.

## Refreshed migration worklist (128 structural questions)

| bucket | count | ids (first 16) |
|---|---:|---|
| **SAFE_TO_MIGRATE** | **22** | 21, 100, 110, 111, 112, 114, 199, 298, 337, 366, 515, 549, 572, 606, 655, 783 |
| `BLOCKED_BY_TEST_CONTRACT` (non-canonical input) | 58 | 83, 86, 95, 101, 107, 124, 147, 203, … |
| `BLOCKED_BY_STARTER` (not Python-ready under v2) | 28 | 2, 19, 25, 61, 82, 92, 99, 102, 103, 104, **105**, **108**, … |
| `BLOCKED_BY_TEST_CONTRACT` (non-canonical structural **output**) | 5 | **106**, 654, 889, 1038, 1382 |
| `UNSUPPORTED` (`Node`) | 13 | 117, 133, 138, 426, 428, 430, 431, 510, … |
| `UNSUPPORTED` (`ImmutableListNode`) | 1 | 1265 |
| `REQUIRES_CONTENT_REVIEW` (quarantined) | 1 | **98** |

`BLOCKED_BY_TEST_CONTRACT (non-canonical structural output)` is new in M12:
q106's expected output is an ASCII tree drawing (`3\n  / \\\n 9  20 …`), and
q105's is `{3,9,20,15,7}`. A question whose *return* is a structure must also
store its output canonically, and five do not.

## The wave, when Judge0 returns

Order is mandatory; **none of it may be skipped because a local adapter
passes**:

```
pre-image capture  →  representation migration  →  execution validation
      →  reference validation  →  hidden-test validation
      →  Oracle  →  approval  →  promotion
```

Specifically, for each of the 22:

1. `preimage_capture --freeze` (rollback anchor)
2. flip `execution_contract_version` to `v2` — **the expected outputs are not
   touched**
3. execute the stored cases through the real seam in Python, JavaScript and
   Java; **any disagreement stops that question**
4. operator-authored reference, reviewed
5. `oracle_execute` on Judge0 — the only thing that establishes truth
6. `quality_gate`, `question_review`, `question_approve`, `question_promote`

**Do not assume existing outputs survive the migration.** Under v1 a tree
question was fed a JSON array and splatted; under v2 it is fed the canonical
array and built into a node. A question that "passed" before may have been
passing for the wrong reason.

## Unchanged

Trust: 6 ORACLE_VERIFIED · 6 PUBLISHED · 8 references · 238 oracle executions ·
8 approvals · 6 adaptive-eligible. **q98 remains quarantined**, answer key
untouched. **K2** (v3 Python-only) untouched.
