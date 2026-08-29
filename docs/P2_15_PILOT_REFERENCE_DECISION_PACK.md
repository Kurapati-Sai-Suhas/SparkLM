# P2.15 — Pilot reference decision pack

**Status:** DECIDED AND EXECUTED. Four references approved by the operator on
2026-08-29; one Oracle execution run on q1974. Nothing promoted or published.

---

## Operator decision — recorded 2026-08-29

| Reference | Question | Decision | Lifecycle reached |
| --- | --- | --- | --- |
| #4 | q1940 | **APPROVE** | APPROVED, not active |
| #5 | q1974 | **APPROVE** | APPROVED + **ACTIVE** (canonical) |
| #6 | q2057 | **APPROVE** | APPROVED, not active |
| #7 | q2290 | **APPROVE** | APPROVED, not active |

Oracle target chosen by the operator: **q1974**.

Only #5 was activated. `activate` selects the canonical reference the Oracle
runs, and only the question being executed needs one — the other three are
approved and inert.

### The precedent this sets

Approving **#6** decides the open question the pack raised: `load_bearing`
constrains the **result**, not the traversal. q2057's reference checks only
the letters that appear rather than all twenty-six, produces identical output
on every input, and was accepted on that basis.

That now applies to every reference reviewed after this one. A future
reference may take a different route through a specification provided its
results are identical; a reviewer who wants the method pinned must say so in
`required_operation`, not in `load_bearing`.

### Verification after approval

Every stored `source_hash` equals the digest published in this pack for
review, so what was approved is what was reviewed:

| Reference | `source_hash` | Matches pack |
| --- | --- | --- |
| #4 | `6e6e7573…8a85edb54` | ✓ |
| #5 | `7c8748a0…8ccafbdc` | ✓ |
| #6 | `ab53947a…955a57592` | ✓ |
| #7 | `85cf0800…9c7cb664b` | ✓ |

`approved_by = Suhas` and `approved_at` populated on all four; provenance
reported `intact` for all four. Source is now frozen by a database constraint
that recomputes the digest — an approved reference cannot be edited, only
superseded.

---

## Oracle execution — q1974, 2026-08-29

Dry run first (`record=False`): 14 agree, 0 conflict, 0 absent, 0 unsettled,
and the command reported `DRY RUN — no provenance recorded`. Then one recorded
execution (`--execute --operator Suhas --alias oracle`, role
`learnlm_oracle_rw`), same result.

| | |
| --- | --- |
| Cases | 14 agree · 0 conflict · 0 absent · 0 unsettled |
| Rows written | **28** — see below |
| Reference source hash on every row | `7c8748a078305212e05fb5318f86db68506fb576744f4dd89bc4253d8ccafbdc` |
| Contract recorded | `v3`, matching the question |
| `is_authoritative` | `False` on all 28 |
| Status | `SUCCESS` on all 28 |
| Hidden suite digest before and after | `ac416cbf8e8c4071116f0285fcd7d53b…` — unchanged |
| q1974 status / trust after | `DRAFT` / `UNVERIFIED` — unchanged |

**Why 28 rows for 14 cases.** The pipeline runs each case **twice** with
`verify_determinism=False` and compares the two results itself, recording
both executions. `OracleService.run` would have verified determinism
internally and discarded the second result; hoisting it keeps both attempts
as evidence. Exactly 2 rows per `case_digest`, ~1s apart, identical output on
every pair — so the reference is deterministic on all 14 inputs, and that is
now recorded rather than asserted.

`is_authoritative = False` throughout: this is evidence, not grading truth.
The command cannot write `expected_output` — no writer exists in this phase.

**What this document is.** Everything needed to decide whether each of the four
pilot references is correct, presented so the decision is yours. I did not
certify any of them and will not: I wrote these references, and reference/suite
agreement is same-author evidence, not verification.

**What this document is not.** It contains no reference source and no hidden
test outputs. The source is grading truth and this repository is public; read
it with `reference_review inspect <id> --operator <you> --show-source`, which
is the command that also records that you looked.

---

## The decision

For each reference, choose one:

| Reference | Question | Title | Decision |
| --- | --- | --- | --- |
| #4 | q1940 | Sum of Digits of String After Convert | ☐ APPROVE ☐ REJECT ☐ NEEDS_REVIEW |
| #5 | q1974 | Find Greatest Common Divisor of Array | ☐ APPROVE ☐ REJECT ☐ NEEDS_REVIEW |
| #6 | q2057 | Check Whether Two Strings are Almost Equivalent | ☐ APPROVE ☐ REJECT ☐ NEEDS_REVIEW |
| #7 | q2290 | Count Number of Ways to Place Houses | ☐ APPROVE ☐ REJECT ☐ NEEDS_REVIEW |

**One question only** proceeds to the Oracle after this decision (§25D). Name
which.

---

## Shared provenance

All four are identical on these, and all four match:

| Field | Value |
| --- | --- |
| `origin` | `llm` |
| `provider` | `claude-opus-5 (assistant)` |
| `model_name` | `claude-opus-5` |
| `prompt_version` | `phase15-reference-from-specification/v1` |
| `review_state` | `DRAFT` |
| `is_active` / `canonical` | `False` / `False` |
| `source_hash` | not yet set — populated by the approval step |
| Batch | `p27-pilot-2`, state `CAPTURED`, frozen 2026-08-25 |
| Question state | `DRAFT` / `UNVERIFIED` |

Every `specification_digest` stored on the reference **matches** the digest in
the corresponding spec file. Verified this phase.

---

## #4 — q1940 · Sum of Digits of String After Convert

| | |
| --- | --- |
| Specification digest | `3e837b04ce81d24f2f3c5b94024c4f62a492b3750cbd9ab8f8215eff3ff8e4ac` |
| Reference source sha256 | `6e6e7573b44e997839c897af170d48f9aea7eda1c3d062bc374344f8a85edb54` |
| Contract | `v1` |
| Hidden tests | 15 · suite digest `995d97a836749fb6285abdf9288e6e8a…` |
| Signature | `sumOfDigitsOfStringAfterConvert(s, k)` — matches the declared starter |

**Operation.** Maps each letter to its alphabet position, concatenates those
positions into one run of digits, converts that run to an integer, then
replaces the integer by the sum of its decimal digits, k times.

**Assumptions it makes.**
- Every character is a lowercase `a`–`z`. The position is computed by
  subtracting 96 from the code point, so an uppercase letter or a digit would
  produce a wrong value silently rather than an error.
- `k ≥ 1`. With `k = 0` the loop never runs and it returns the whole
  concatenated number. The spec bounds k to 1–10, so this is unreachable.

**Edge cases against the specification.**
- Spec: "*Rounds continue even once the number is a single digit.*" The loop
  runs unconditionally k times, so a single digit is summed to itself. ✓
- Spec: "*concatenates positions rather than adding them*" — load-bearing, and
  the reference concatenates. ✓
- No letter maps to `0`, so the string→int conversion cannot lose a leading
  zero. The round trip is lossless *because* the alphabet starts at 1.

**Ambiguity worth your attention.** The `int()` / `str()` round trip is
**unnecessary** — the digits could be summed straight from the string. It also
imports a limit that need not exist: since Python 3.11 there is a 4,300-digit
cap on int↔str conversion, and exceeding it raises. At 100 letters the maximum
is 200 digits, so it is safe **because the specification bounds the input**. If
that bound is ever relaxed, this reference breaks. Correct today; fragile by
construction.

`import math` is present and unused.

---

## #5 — q1974 · Find Greatest Common Divisor of Array

| | |
| --- | --- |
| Specification digest | `eb8458fd460a64b62bc129e0662f3af509523dace43db127afdb0e90d01ffc84` |
| Reference source sha256 | `7c8748a078305212e05fb5318f86db68506fb576744f4dd89bc4253d8ccafbdc` |
| Contract | **`v3`** — the only pilot question on v3 |
| Hidden tests | 14 · suite digest `ac416cbf8e8c4071116f0285fcd7d53b…` |
| Signature | `findGreatestCommonDivisorOfArray(nums)` — matches the declared starter |

**Operation.** Returns the greatest common divisor of the smallest and largest
values in the list. One line.

**Assumptions it makes.**
- The list is non-empty. `min()` on an empty list raises. The spec requires
  2–1000 integers, so unreachable.
- Values are positive integers, as the spec states.

**Edge cases against the specification.**
- Spec load-bearing: "*Exactly two values participate: the smallest and the
  largest. Every other element is ignored.*" The reference does precisely
  that, and nothing more. ✓
- Spec: "*When every value is identical … the answer is that number.*"
  `gcd(x, x) = x`. ✓
- Spec: "*A list containing 1 always yields 1.*" 1 would be the minimum, and
  `gcd(1, max) = 1`. ✓

**Ambiguity worth your attention.** The reference annotates `nums: list`; the
declared starter annotates `nums: list[int]`. Under **contract v3 this is
cosmetic** — v3 reuses v1's harness with a canonical envelope built server-side
and splatted positionally, so annotations are never read. It would become
load-bearing if this question were ever moved to **v2**, whose wrapper
inspects the annotation at runtime and branches on whether it mentions `list`.
Worth knowing before anyone changes the contract.

---

## #6 — q2057 · Check Whether Two Strings are Almost Equivalent

| | |
| --- | --- |
| Specification digest | `d8febbe3bb5f02ae0cf72181a65f4788fd8dd5e9db7499f399f0c22e26a55d26` |
| Reference source sha256 | `ab53947a8ace447facfb01d28585d470f5ac9ea089587d325d89262955a57592` |
| Contract | `v1` |
| Hidden tests | 16 · suite digest `0f1f617ea44705c286b340eb5c0a1987…` |
| Signature | `checkWhetherTwoStringsAreAlmostEquivalent(word1, word2)` — matches |

**Operation.** For every character occurring in either word, compares the two
frequencies and requires the difference to be at most 3.

**⚠️ This is the one that needs a real decision.**

The specification's `load_bearing` field says, verbatim:

> *Every letter of the alphabet is checked, not only the letters that appear.*

The reference iterates `set(word1) | set(word2)` — **only the letters that
appear.** It does not check all twenty-six.

**The output is nevertheless identical.** A letter occurring in neither string
has counts 0 and 0, a difference of 0, which can never exceed 3, so no
unchecked letter can change the verdict. The two traversals agree on every
possible input.

So the question in front of you is not "is it correct" — it produces the right
answer. It is: **does a reference have to follow the traversal the
specification declares load-bearing, or only agree with its results?**

That is a policy decision about what `load_bearing` means in this project, and
it will apply to every reference after this one. I am not going to decide it.

- If load-bearing describes the *result*, this is APPROVE.
- If load-bearing describes the *method* — which is what "not only the letters
  that appear" reads like — this is REJECT or NEEDS_REVIEW, and the reference
  should iterate the full alphabet.

**Other notes.** `word1.count(c)` inside the loop rescans the string per
character, so the cost is O(26·n) rather than O(n). Irrelevant at the spec's
100-character bound. Empty strings give an empty set and `all([])` is `True` —
unreachable, since the spec requires at least 1 character.

---

## #7 — q2290 · Count Number of Ways to Place Houses

| | |
| --- | --- |
| Specification digest | `37f4230790e3c76074bc7dff6a56977a1be3091130c47bb5557710ecef6e4a20` |
| Reference source sha256 | `85cf0800aa7b2b6c7cd8f516630f7ae1e038de71274f0b869ff80829c7cb664b` |
| Contract | `v1` |
| Hidden tests | 14 · suite digest `7937bae0b5aa2a3c570d11be33a661b0…` |
| Signature | `countNumberOfWaysToPlaceHouses(n)` — matches the declared starter |

**Operation.** Iterates a Fibonacci-style recurrence to count the arrangements
on one side of the street, then squares it, reducing modulo 1,000,000,007.

**Assumptions it makes.**
- `n ≥ 1`. At `n = 0` the loop body never runs and it returns 4, where the
  correct count is 1 (the empty arrangement, squared). The spec bounds n to
  1–10,000, so **unreachable — but only because of that bound.**

**Edge cases against the specification.**
- `n = 1` → 4. Two arrangements per side (empty, or one house), squared. ✓
- `n = 2` → 9; `n = 3` → 25. Matches the recurrence f(1)=2, f(2)=3.
- Spec load-bearing: "*the adjacency restriction applies only within a side*"
  and "*the total is the count for one side multiplied by itself*." The
  reference squares a single-side count and never models cross-street
  adjacency. ✓
- Spec: "*Leaving every plot empty is a valid arrangement and is counted.*"
  The base case `b = 2` at n=1 counts it. ✓

**Ambiguity worth your attention.** The running value `a` is reduced modulo the
prime each step, and the final `b * b` multiplies two already-reduced values —
in Python that is exact regardless of size, so no overflow exists. Reviewers
used to fixed-width integers may expect an intermediate reduction that is not
needed here.

---

## Preflight — everything except your decision

Run this phase, read-only, against production:

| Check | q1940 | q1974 | q2057 | q2290 |
| --- | --- | --- | --- | --- |
| APPROVED reference exists | **FAIL** | **FAIL** | **FAIL** | **FAIL** |
| Reference source compiles | PASS | PASS | PASS | PASS |
| Signature matches declared starter | PASS | PASS | PASS | PASS |
| Execution contract declared | PASS `v1` | PASS `v3` | PASS `v1` | PASS `v1` |
| Hidden suite ≥ 12 cases | PASS 15 | PASS 14 | PASS 16 | PASS 14 |
| Pre-image in frozen batch | PASS | PASS | PASS | PASS |
| LLM provenance complete | PASS | PASS | PASS | PASS |
| Question still DRAFT / UNVERIFIED | PASS | PASS | PASS | PASS |

**The only failing prerequisite is the one that is yours.** Everything the
Oracle needs is in place except an approved reference, and the Oracle will not
be run while any check fails.

---

## What happens after you decide

For each APPROVE, in order, through the sanctioned command only:

```bash
python manage.py reference_review inspect <id> --operator <you> --show-source
python manage.py reference_review submit  <id> --operator <you>
python manage.py reference_review approve <id> --operator <you> --confirm
```

Nothing is marked canonical or active by hand; the command walks the states
the model's own constraints enforce. Then `approved_by`, `approved_at` and
`source_hash` are verified as populated before anything else happens.

Then **one** Oracle execution, on the single question you name, and a stop.

---

## What promotion will require — and why it is not this phase

Fresh Oracle evidence does **not** make a question trusted. Promotion to
`ORACLE_VERIFIED` additionally requires:

1. An oracle execution recorded against **this** reference and **this** hidden
   suite digest — evidence is tied to a case set, so a suite edit invalidates it.
2. Every hidden case producing the expected output under the declared contract.
3. A `QuestionApproval` row, written by `question_approve`, by a named
   operator — separate from reference approval and not implied by it.
4. `question_promote` moving `trust_state` DRAFT→ORACLE_VERIFIED, which is the
   only writer of that field besides `question_demote`.
5. Publication (`question_status` PUBLISHED) is a further, separate step.

Only after 4 **and** 5 does `is_adaptive_eligible` become true and the question
start producing learner evidence. **None of this happens in P2.15.**
