# P2.7 — Adjudication record: batch `p27-pilot-1`

**DECISION RECORD — proposals only. No grading truth was written.**

Nothing below is a decision. Every classification is a recommendation awaiting
your sign-off; the `human reviewer` field is deliberately blank on every row,
because a model's reasoning is not adjudication (§9).

---

## ⚠ STOP CONDITION — the SAFE control is defective

**q264 case 3 has a wrong answer key, and I have not touched it (§5).**

```
n = 15   stored expected: 30   independently computed: 24
```

Ugly numbers: 1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18, 20, **24** ← 15th.
30 is the 18th.

Verified by brute-force min-heap enumeration, cross-checked against the two
cases that *do* agree — including `n = 1690 → 2123366400`, a large value that a
wrong algorithm would not reproduce. The statement's own Example 3 repeats the
same error, so statement and key agree with each other and both are wrong.

**This does not mean the earlier verification failed.** `SAFE` has always meant
"the adapter can invoke this question as its signature declares" and never
"the stored answers are correct" — that distinction was stated in the impact
report and is now demonstrated. q264 was drawn deterministically from the 500
SAFE questions precisely because the audit sample contained none; the first one
examined closely has a defective key.

**One data point, not a rate.** It does not license a claim about the other 499.

### Your options

| option | effect |
|---|---|
| **A. Keep q264 as control** | The invariant is "unchanged", not "correct". A defective-but-untouched control still proves the workflow does not touch what it was not asked to. Cheapest, and still meaningful. |
| **B. Replace it** | Draw another SAFE question and verify its keys *before* admitting it. The same seeded draw offered q650 and q1693. Requires re-capturing into a new batch — membership is frozen. |
| **C. Keep it and add it as a repair subject** | Turns the control into an eighth remediation subject; loses the control entirely. Not recommended. |

**I recommend A**, with the defect recorded here, and q264's key repaired in a
later batch rather than this one. Changing the control mid-pilot would mean the
pilot no longer tests what it was designed to test.

---

## A. Per-question adjudication

Legend: statement/contract/tests/keys → the four judgements; `HUMAN` → decision
required from you.

### q1689 — Reformat Phone Number · `dec99939…fb080c`

| field | finding |
|---|---|
| issue class | statement defect + semantic key error (4/4 cases) |
| statement correct? | **NO** |
| contract correct? | YES — `reformatNumber(number: str)`, adapter binds `['1-800-555-0144']` |
| tests structurally valid? | YES |
| keys trustworthy? | **NO** |
| human decision? | **YES** |

**Every case states a different rule**, in its own `explanation` field:

| case | explanation | implied rule |
|---|---|---|
| 1 | "split the remaining digits into groups of three" | 3-3-3-2 → `180-055-501-44` |
| 2 | "Remove all non-digit characters" | no grouping at all |
| 3 | "Split into three groups" | key has **four** groups, and drops 3 of 16 digits |
| 4 | "groups of three and the last group can have up to five" | a rule unique to this case |

The statement adds a fifth contradiction: it declares the form `XX-XXX-XXXX`
(2-3-4) while its own worked example shows `866-111-1111` (3-3-4) *and*
country-code stripping, which no case performs.

**No single rule reproduces any stored key.** The title matches a well-known
problem whose rule would explain cases 1 and 4 — but that rule appears **nowhere
in the repository**, and §2 forbids importing it.

→ **STATEMENT_UNRECOVERABLE** → `MANUAL_REVIEW`

### q963 — Minimum Area Rectangle II · `06a9bb6b…eda59c`

| field | finding |
|---|---|
| issue class | statement defect + semantic key error + structurally invalid case |
| statement correct? | **NO** |
| contract correct? | YES — `minAreaRect(points: list[list[int]])` binds one list |
| tests structurally valid? | **NO** — case 4 `expected_output` is `int 1` |
| keys trustworthy? | **NO** |
| human decision? | **YES** |

Three separate contradictions, all inside the repository:

1. **Title vs statement.** Title says "II"; statement says "each side must be
   parallel to either the x-axis or y-axis". Case 1's key (`2`) is correct only
   under the *any-orientation* reading — the four points form a 45°-rotated
   square of area 2. Axis-parallel gives 0.
2. **Example 1's explanation belongs to Example 2.** It cites points
   `(0,0),(0,1),(1,0),(1,1)` that are absent from Example 1's input — and are
   exactly the first four points of Example 2's input.
3. **Example 2 is false on its own terms.** It says "No rectangle can be
   formed" for an input containing the unit square; area 1 exists under *both*
   readings, so its key of `0` is wrong regardless of which reading wins.

Case 3's key (`2`) is also wrong under both readings (computed: 1).

→ **STATEMENT_REPAIR_REQUIRED**, and the repair is decidable — see §B.

### q1436 — Destination City · `0d442588…340508`

| field | finding |
|---|---|
| issue class | contract defect + one input violating the stated constraint |
| statement correct? | **YES** — the definition is clear and standard |
| contract correct? | **NO** — `paths` unannotated |
| tests structurally valid? | YES |
| keys trustworthy? | 3 of 4 |
| human decision? | **YES** (case 2 only) |

The statement is sound. Two defects:

- **Contract:** `destCity(self, paths)` has no annotation. Under v1 the JSON
  list is splatted into 3 arguments → `TypeError`; under the adapter it arrives
  as one raw string. Neither yields the list of pairs the statement describes.
- **Case 2 violates the stated constraint.** `[["B","C"],["D","B"],["C","D"]]`
  is the cycle B→C→D→B. Every city has an outgoing edge, so no destination
  exists — yet the statement guarantees one. Its key `D` is not derivable, and
  its explanation ("all of them end at D") is false: D→B is an edge.

Cases 1, 3, 4 are correct.

→ **STATEMENT_VALID**, `CONTRACT_REPAIR_REQUIRED`, and case 2 needs a human
call: repair the *input* (drop it, or change it to a non-cyclic graph) rather
than the key.

### q3309 — First Occurrence in a String · `2395b945…d47a0a`

| field | finding |
|---|---|
| issue class | contract defect (stored form) + statement self-contradiction |
| statement correct? | **AMBIGUOUS** |
| contract correct? | **NO** (for case 4 only) |
| tests structurally valid? | YES |
| keys trustworthy? | **YES** — all five semantically correct |
| human decision? | **YES** (narrow) |

Keys verified: `hello/ll→2`, `aaaaa/bba→-1`, `abc/a→0`, `mississippi/issip→4`,
and empty/empty→`0`. All correct.

The contradiction: constraints say `1 <= |needle|`, while the statement also
says "If needle is an empty string, return 0". Case 4 (`'\n\n'`, both empty)
exercises the branch the constraints forbid.

Mechanically, case 4's stored form is two blank lines, which cannot supply two
declared parameters — blank lines are filtered before mapping.

→ **CONTRACT_REPAIR_REQUIRED** (case 4's stored form), plus a one-line
statement decision: keep the empty-needle rule and relax the constraint to
`0 <=`, or drop case 4. **The key is right either way.**

### q17 — Letter Combinations of a Phone Number · `4dd7a168…d4213a`

| field | finding |
|---|---|
| issue class | structurally broken test data only |
| statement correct? | YES (thin, but not wrong) |
| contract correct? | YES — `letterCombinations(digits: str)` |
| tests structurally valid? | **NO** — cases 3 and 4 store lists |
| keys trustworthy? | **YES** — all four semantically correct |
| human decision? | NO |

The cleanest question in the batch. Cases 1–2 are correct strings. Cases 3–4
are **semantically right but structurally wrong**: `expected_output` holds
`[]` and `['w','x','y','z']` as JSON lists rather than text.

For `digits='9'` the letters *are* w, x, y, z — the content is correct; only
the storage type is wrong. The statement's example block also renders empty
(the mapping table it references is missing), which is a cosmetic content gap,
not a semantic one.

**This is the one question where the repair is purely mechanical**: serialise
those two values as the canonical string form the wrapper emits. No semantic
judgement, no oracle needed to decide *what* the answer is.

→ `HIDDEN_TEST_REPAIR`

### q266 — Palindrome Permutation · `1ba2e68f…de26411`

| field | finding |
|---|---|
| issue class | **formatting only** |
| statement correct? | YES |
| contract correct? | YES |
| tests structurally valid? | YES |
| keys trustworthy? | **YES — all four semantically correct** |
| human decision? | NO |

All four keys verified: `code→False`, `aabbccee→True`, `abcba→True`,
`carerac→True`. Every one is right.

The defect is **only** that they are stored `True`/`False` while the wrapper
emits `true`/`false` and `normalize_output` does not fold case. So every
correct submission fails.

Explicitly **not**: contract-level (the signature and binding are right),
semantic (the answers are right), or expected-output corruption (the values are
the intended ones, in the wrong casing).

**Minimum safe remediation:** rewrite four strings, `True`→`true`,
`False`→`false`. Deterministic, no oracle, no judgement — the same class as the
64 boolean-casing questions bank-wide.

→ `HIDDEN_TEST_REPAIR` (formatting)

### q264 — Ugly Number II · `396d211e…c80271` — **CONTROL**

| field | finding |
|---|---|
| issue class | **semantic key error in the control** |
| statement correct? | **NO** — Example 3 says `n=15 → 30`; correct is 24 |
| contract correct? | YES — `nthUglyNumber(n: int)` binds `[15]` |
| tests structurally valid? | YES |
| keys trustworthy? | **3 of 4** — case 3 wrong |
| human decision? | **YES — see the STOP CONDITION above** |

→ **`KEEP` (recommended)** — unchanged in this pilot, defect recorded, repaired
in a later batch.

---

## B. Statement repairs proposed — **not written, awaiting approval**

Only **q963** is repairable from repository evidence. q1689 is not; q1436 needs
an input decision rather than a statement rewrite.

### q963 proposal · pre-image `06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c`

**Contradiction:** the title says "II", the statement says axis-parallel, and
case 1's key is correct only under any-orientation. Example 1's explanation
describes Example 2's input. Example 2's key is wrong under both readings.

**Which reading wins, from repository evidence alone:** the *any-orientation*
one. Two independent signals agree — the title, and case 1's stored key of `2`,
which is unreachable under the axis-parallel reading (that gives 0). No repo
evidence supports axis-parallel except the one sentence being corrected.

**Proposed statement** (replacing the constraint sentence):

> Given a set of points in the plane, find the minimum area of any rectangle
> formed from these points, **with sides not necessarily parallel to the
> coordinate axes**. The four vertices must be among the given points. If no
> such rectangle exists, return 0.

**Proposed examples:**

| # | input | output | explanation |
|---|---|---|---|
| 1 | `[[1,2],[2,1],[1,0],[0,1]]` | `2` | the four points form a square rotated 45°, side √2, area 2 |
| 2 | `[[0,0],[0,1],[1,0],[1,1],[2,1],[2,0]]` | `1` | **corrected from 0** — the unit square (0,0),(0,1),(1,0),(1,1) |

**Also required, and not a statement change:** case 2's key `0`→`1` and case
3's key `2`→`1`, and case 4's `expected_output` `int 1`→`"1"`. Those are
`KEY_REPAIR_AFTER_ORACLE`, not part of the statement edit.

**Rationale:** the repair follows the title and the one key that is internally
consistent, and corrects two examples that are false on their own inputs. It
imports nothing.

---

## C. Contract decisions

| q | actual input | arity | output | v1 adequate? | classification |
|---|---|---|---|---|---|
| 1689 | one string | 1 | string | YES | **CONTRACT_OK** |
| 963 | one `list[list[int]]` | 1 | int | **NO** — v1 splats the outer list into 4+ args | **CONTRACT_MIGRATION_REQUIRED** (v3) |
| 1436 | one list of pairs | 1 | string | **NO** — same splat; and the parameter is unannotated | **CONTRACT_REPAIR_REQUIRED** (annotate `paths: list[list[str]]`) then v3 |
| 3309 | two strings | 2 | int | partly — cases 1–3, 5 work; case 4 (both empty) cannot be expressed | **CONTRACT_REPAIR_REQUIRED** (case 4's stored form) |
| 17 | one string | 1 | list rendered as string | YES | **CONTRACT_OK** |
| 266 | one string | 1 | bool | YES | **CONTRACT_OK** |
| 264 | one int | 1 | int | YES | **CONTRACT_OK** |

No production contract was modified.

## D. Control result

**DEFECTIVE — stopped, not changed.** See the STOP CONDITION. q264 remains
byte-identical to its pre-image; the freeze verification confirms it.

## E. Boolean-output decision (q266)

**Formatting-only.** Not contract, not semantic, not corruption. Four strings,
deterministic transformation, no oracle required.

## F. Invalid-input decisions (q17, q963)

| q | classification |
|---|---|
| **q17** | **valid and recoverable through storage repair.** The values are semantically correct; only their JSON type is wrong. Serialising them as the canonical string is faithful, not a fix-to-pass. |
| **q963** | **semantically invalid, and separately malformed.** Case 4 stores `int 1` (type), *and* cases 2 and 3 hold wrong answers (semantics). The type must not be repaired in isolation — doing so would make a wrong key executable, which is worse than one that cannot run. |

Neither is being repaired to make a gate pass.

## G. Next action — one per question

| q | action |
|---|---|
| **1689** | `MANUAL_REVIEW` — statement unrecoverable from repo evidence |
| **963** | `STATEMENT_REPAIR` → then `CONTRACT_REPAIR` → then `KEY_REPAIR_AFTER_ORACLE` |
| **1436** | `CONTRACT_REPAIR` (annotate) + `MANUAL_REVIEW` for case 2's input |
| **3309** | `CONTRACT_REPAIR` (case 4 stored form) + one statement decision |
| **17** | `HIDDEN_TEST_REPAIR` (serialise two values) |
| **266** | `HIDDEN_TEST_REPAIR` (boolean casing) |
| **264** | `KEEP` — unchanged; defect recorded for a later batch |

## H. Human decisions required

1. **q264 control** — option A, B or C above. **Blocks the pilot.**
2. **q1689** — accept `MANUAL_REVIEW`, or authorise importing the external rule
   the title implies. §2 forbids me doing the latter unilaterally.
3. **q963** — approve or amend the proposed statement and examples in §B.
4. **q1436 case 2** — repair the input, drop the case, or relax the stated
   guarantee.
5. **q3309** — keep the empty-needle rule and relax `1 <=` to `0 <=`, or drop
   case 4.

## I. Removal from the pilot

**None recommended.** q1689 stays as the `MANUAL_REVIEW` exemplar — a pilot
that only contains repairable questions would not prove the workflow can stop.
Removal would in any case require a new batch, since membership is frozen.

## J. Grading-truth writes — **ZERO**

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0

fingerprint 1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
IDENTICAL
```

All 7 pre-images verify and still match their live rows. 0 remediation actions.
This review was conducted entirely through `learnlm_census_ro`, reading from
the frozen pre-images.

## K. Next phase after approval

1. Resolve the five decisions in §H — **the q264 one first**, since it
   determines whether this batch is the pilot.
2. `STATEMENT_REPAIR` for q963 only, as a `RemediationAction` against the
   frozen batch, with `preimage_inspect` before and after.
3. **Stop again.** Statement repair is the only class approved at that point;
   contract repair, oracle execution and key repair each need their own review.
4. Exercise `preimage_rollback` on one repaired question deliberately — it has
   never run against real data, and the pilot is the right place to find out.

---

```
BATCH             = FROZEN (p27-pilot-1, 7 members)
ADJUDICATION      = PROPOSED, AWAITING HUMAN SIGN-OFF
CONTROL q264      = DEFECTIVE, UNCHANGED, DECISION REQUIRED
REMEDIATION       = NOT STARTED
ORACLE            = NO
PROMOTION         = NO
RESEED            = NO
```
