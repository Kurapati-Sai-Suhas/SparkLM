# Java structural adapter (Phase 1 M11)

M5 built the canonical structural representation and wired Python and
JavaScript to it. Java's contract was **written down and deliberately not
wired**: there was no JVM to compile it with, and shipping an unexecuted
harness to learners is worse than saying it is not ready. M10 installed a JVM.
M11 wires it, and executes it.

**Nothing here is new architecture.** The representation is M5's, unchanged.

## The field shapes were found, not chosen

The Java bank already declares them:

| declaration | starters |
|---|---:|
| `class ListNode { int val; ListNode next; }` | **14** |
| `class TreeNode { int val; TreeNode left; TreeNode right; }` | **11** |

Identical to M5's registry (`children = ("left","right")` / `("next",)`). The
adapter matches the content; the content did not have to move.

Java signature shapes confirm what must be bound and normalized:
`int m(TreeNode)` ×15, `ListNode m(ListNode)` ×11, `ListNode m(ListNode,
ListNode)` ×7, `ListNode m(ListNode, int)` ×6, `TreeNode m(TreeNode)` ×3 —
plus `Node m(Node)` ×6, which stays **unsupported** for M5's reason.

## The one thing Java genuinely needs differently

**Java has no shadowing.** Python and JavaScript put the node class above the
learner's code and let a learner's own definition win by being defined later.
Two top-level classes with one name in one Java file is a **compile error**
instead.

So a class the learner already declares is **omitted**. That is the same
guarantee — *the platform never overwrites submitted code* — expressed in a
language that cannot shadow. It is per class: a learner who declares only
`ListNode` still receives `TreeNode`.

What it is **not**: the learner's source deciding what a parameter *is*. The
structural kind still comes from the question's declared starter. A test
submits `public String kind(Object root)` — a deliberately mistyped signature
— and asserts the argument arrives as a `TreeNode` anyway.

Detection is textual, because this repository has no Java parser. A false
positive omits a class the learner did not really define, and the result is a
**compile error they can read** — never a silent wrong answer.

## Everything structural is placeholder-gated

Four placeholders, all empty for a non-structural question:

| placeholder | contents |
|---|---|
| `{structural_prelude_java}` | node classes (minus declared ones) + `SparkLMStructures` |
| `{structural_bind_java}` | the `tree` / `linked_list` binding branches |
| `{structural_render_java}` | the `instanceof` normalization branches |
| `{parameter_kinds_java}` | `{"tree", "scalar"}` — a Java array, not JSON |

**Why binding and rendering are separate placeholders, not permanent text:**
they *name* `SparkLMStructures`, which only exists when the prelude is
injected. While the binding branches were unconditional and the prelude was
not, **every non-structural Java submission failed to compile**. Found by
probing a scalar question immediately after wiring the structural path.

## Output normalization

`TreeNode` → canonical level-order; `ListNode` → canonical ordered array;
trailing nulls trimmed so one tree has one spelling; a cycle raises rather than
hanging. The empty structure and "no value" are the same reference in Java too,
so **only the declared return kind separates them**: a method declared to
return a tree that returns `null` returned the *empty* tree and prints `[]`.

## Executed, locally

`javac`/`java` 21.0.12.1. All 10 canonical trees and 6 canonical lists
round-trip, plus: depth traversal, list reversal, mixed scalar+structural, two
structural arguments, a learner-declared node class, two-public-method
rejection, private helpers, and a non-canonical input failing loudly.

**Three-way parity** through the real seam — Python, JavaScript and Java built
from one representation produce one canonical result on every fixture.

**No Judge0 claim.** Judge0 is `NOT_SUBSCRIBED`; this is development/runtime
validation only.

## Readiness — the structural gap was hidden in UNKNOWN

Before M11, Java structural questions reported `UNKNOWN`, **which counts as
servable**. A v1 Java question declaring `TreeNode root` cannot execute at all
— the harness bound a raw `String` and `invoke` threw. "We cannot decide" was
the wrong answer to a decidable question.

| language | | READY | UNKNOWN | NOT_READY |
|---|---|---:|---:|---:|
| java | before | 0 | 635 | 1,153 |
| java | **after** | 0 | **541** | **1,247** |
| javascript | before | 0 | 624 | 1,164 |
| javascript | **after** | 0 | **600** | **1,188** |

Java NOT_READY by cause: `no_starter` 1,147 · **`structural_type` 82** ·
**`structural_unsupported` 12** · `no_solution_class` 6.

94 Java and 24 JavaScript questions left the servable pool for those
languages. They were never executable there — this makes an existing defect
visible rather than creating one.

A v2 structural Java question is `UNKNOWN`, **not READY**: without a compiler
a checker cannot establish more, and claiming otherwise would be claiming a
compile.

**Known limit:** JavaScript starters carry no type names at all, so this
detection cannot see a structure a JS starter does not mention. Only the 24
that name one moved. Reading the kind from the question's Python starter
would close that, and is a separate change.

## Content backlog (whole bank, 2,926)

| bucket | count |
|---|---:|
| `NO_STARTER` | **1,147** |
| `STRUCTURAL — SAFE_AUTOFIX` (canonical data, needs a v2 migration) | **20** |
| `STRUCTURAL — REQUIRES_REVIEW` (non-canonical data) | 61 |
| `STRUCTURAL — REQUIRES_REVIEW` (quarantined: q98) | 1 |
| `STRUCTURAL — REQUIRES_REVIEW` (no usable cases: q92) | 1 |
| `UNSUPPORTED` (`Node`) | 22 |
| `REQUIRES_REVIEW` (no `Solution` class) | 6 |

**Of the 20 SAFE_AUTOFIX, only 11 are truly ready.** The kind vector is
derived from the **Python** starter, so Java structural support requires the
Python starter to declare the structure:

- **kind vector present (11):** q82, q110, q111, q114, q160, q226, q337, q366, q515, q549, q572
- **kind vector empty (9):** q21, q100, q105, q106, q108, q112, q199, q298, q606 — the Python starter must be repaired first

**No content was repaired.** A v2 migration changes what a question's stored
expected outputs mean, which is oracle work, and Judge0 is unavailable. There
is no safe Java content repair in M11 that does not need it — saying so is
more useful than manufacturing one.

## Environment note

C/C++ local execution is **intermittently blocked by Windows Application
Control** (`WinError 4551`) when running a freshly built unsigned binary from a
temp directory. The compile always succeeds; the OS sometimes declines to run
the result. Those tests skip with that reason rather than failing, because it
says nothing about the contract under test.
