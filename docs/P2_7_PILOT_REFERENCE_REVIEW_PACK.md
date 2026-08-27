# P2.7 — Pilot reference review pack

**Phase 20.11 · M2 P2.7h-37 · for operator review. Nothing approved.**

Four reference candidates await a semantic decision that only you can make.
Everything mechanical has been checked and passes; what remains is the one
question no automated check answers — *does this implement the specification?*

---

## Why I cannot answer that question

I wrote these references. I also drafted the specifications they were written
from, and generated the hidden-test suites whose expected outputs came from
executing them. Three artifacts, one author.

So the reading below is **the author's own reading**. It is offered to make
your review faster, not to substitute for it. Where I am genuinely uncertain,
it says so.

### Hidden-test agreement carries no weight

| | |
|---|---|
| q1940 | 15/15 |
| q1974 | 14/14 |
| q2057 | 16/16 |
| q2290 | 14/14 |

**SAME-AUTHOR EVIDENCE.** The expected outputs were produced by running these
references. Agreement is identity recomputed. It is *not* independent
verification, and no oracle has run.

---

## The four candidates

### q1940 — reference #4

```
specification  3e837b04ce81d24f2f3c5b94024c4f62a492b3750cbd9ab8f8215eff3ff8e4ac
source sha256  6e6e7573b44e997839c897af170d48f9aea7eda1c3d062bc374344f8a85edb54
origin         llm · claude-opus-5 (assistant) · phase15-reference-from-specification/v1
signature      sumOfDigitsOfStringAfterConvert(s: str, k: int) -> int   contract v1
```

**Operation implemented.** Replace each letter by its alphabet position
(a=1 … z=26), **concatenate** those positions into one decimal number, then
perform exactly `k` rounds, each replacing the number by the sum of its
decimal digits. Return the value after the final round.

**Assumptions.** `s` contains only lowercase a–z (`ord(c) - 96` is wrong for
anything else) — the specification guarantees this. Positions are 1–26, so no
position contributes a leading zero.

**Edge cases.** Rounds run unconditionally `k` times, so a value that is
already one digit is passed through further rounds unchanged — which is what
the specification's `edge_cases` states. A two-digit position contributes two
digits, per `required_operation`.

**Ambiguity.** None material that I can identify.

---

### q1974 — reference #5

```
specification  eb8458fd460a64b62bc129e0662f3af509523dace43db127afdb0e90d01ffc84
               (supersedes ad2a9eb0…, style-only revision, semantic change NONE)
source sha256  7c8748a078305212e05fb5318f86db68506fb576744f4dd89bc4253d8ccafbdc
origin         llm · claude-opus-5 (assistant) · phase15-reference-from-specification/v1
signature      findGreatestCommonDivisorOfArray(nums: list[int]) -> int   contract v3
```

**Operation implemented.** `math.gcd(min(nums), max(nums))`.

**Explicitly NOT** `gcd` of all elements. The suite contains the
discriminating case `[12, 15, 6, 9, 30]`: `gcd(min,max) = gcd(6,30) = 6`,
while the gcd of all five is 3. A gcd-of-all implementation cannot pass.

**Assumptions.** The list is non-empty — the specification requires at least
two integers.

**Ambiguity.** The specification says "the smallest value present" and "the
largest value present". `min()` and `max()` are value-based, not
position-based, which matches. Worth your eye: this is the one place where a
positional reading ("first and last") would be wrong, and it is exactly the
misconception this question exists to catch.

---

### q2057 — reference #6

```
specification  d8febbe3bb5f02ae0cf72181a65f4788fd8dd5e9db7499f399f0c22e26a55d26
source sha256  ab53947a8ace447facfb01d28585d470f5ac9ea089587d325d89262955a57592
origin         llm · claude-opus-5 (assistant) · phase15-reference-from-specification/v1
signature      checkWhetherTwoStringsAreAlmostEquivalent(word1: str, word2: str) -> bool
               contract v1
```

**Operation implemented.** For every letter occurring in *either* word,
`abs(count_in_word1 - count_in_word2) <= 3`; the result is the conjunction —
`all`, not `any`. A difference of exactly 3 qualifies.

**A literal mismatch you should weigh.** The specification says *"for each of
the twenty-six lowercase letters"*. The reference iterates only letters
**present in either word**. These are equivalent — a letter in neither has
count 0 in both, so its difference is 0 and the predicate holds trivially, and
the specification's own `edge_cases` says exactly this. But the reference does
not literally do what that sentence says, and if you want the code to mirror
the specification word-for-word, this is the line to change.

**Assumptions.** Equal length and lowercase-only, both guaranteed by the
specification. Note the reference does not *check* either — it relies on them.

---

### q2290 — reference #7

```
specification  37f4230790e3c76074bc7dff6a56977a1be3091130c47bb5557710ecef6e4a20
source sha256  85cf0800aa7b2b6c7cd8f516630f7ae1e038de71274f0b869ff80829c7cb664b
origin         llm · claude-opus-5 (assistant) · phase15-reference-from-specification/v1
signature      countNumberOfWaysToPlaceHouses(n: int) -> int   contract v1
```

**Operation implemented.** A Fibonacci-style recurrence seeded `a, b = 1, 2`,
iterated `n-1` times, giving the count for **one side**; the result is that
count **squared**, modulo 1 000 000 007.

**This is the largest inferential leap of the four, and the one most worth
your attention.** The specification describes a *combinatorial* problem — `n`
plots per side, no two houses adjacent on the same side, the opposite side
unrestricted. It does **not** state a Fibonacci recurrence. The reference
*assumes* the derivation:

- one side with `n` plots and no two adjacent houses has `Fib(n+2)`
  arrangements;
- the two sides are independent, so the total is that count squared;
- modulo per the specification.

**Corroboration, and its limit.** Phase 19's second implementation derives the
one-side count by direct dynamic programming over plots — no Fibonacci
assumed — and agrees on all 14 cases. That is genuine corroboration *of the
derivation*, but both implementations are mine, so it catches an arithmetic
slip rather than a misreading of the problem.

**Hand-checkable anchors** (verifiable with pencil and paper):

| n | one side | total |
|---|---|---|
| 1 | 2 (empty, or house) | 4 |
| 2 | 3 (00, 10, 01 — 11 forbidden) | 9 |
| 3 | 5 | 25 |

**Assumptions.** Independence of the two sides — which the specification
states explicitly in its `edge_cases`.

---

## Your decision

Four records, one per reference. Allowed decisions: **APPROVE**,
**REJECT**, **NEEDS_REVIEW**.

| Field | q1940 | q1974 | q2057 | q2290 |
|---|---|---|---|---|
| reference id | #4 | #5 | #6 | #7 |
| reviewed_digest (source) | `6e6e7573…` | `7c8748a0…` | `ab53947a…` | `85cf0800…` |
| specification digest | `3e837b04…` | `eb8458fd…` | `d8febbe3…` | `37f42307…` |
| reviewer | ☐ | ☐ | ☐ | ☐ |
| reviewed_at | ☐ | ☐ | ☐ | ☐ |
| **decision** | ☐ | ☐ | ☐ | ☐ |
| notes | ☐ | ☐ | ☐ | ☐ |

Review the **source hash**, not the file — the row is what the oracle would
execute. All four are DRAFT, inactive and non-canonical; canonical remains
`#1, #2, #3`.

When you decide, the sanctioned path is `reference_review` — nothing here
bypasses it, and nothing in this phase moved any reference along its
lifecycle.

---

## The seven questions, answered as analysis

Not as decisions. Question 1 is yours; the rest are mechanical and verified.

| | q1940 | q1974 | q2057 | q2290 |
|---|---|---|---|---|
| 1. implements the specification? | **operator** | **operator** | **operator** | **operator** |
| 2. every required behaviour present? | yes, by my reading | yes | yes | yes, *given the derivation* |
| 3. behaviour not allowed by the spec? | none found | none found | none found | none found |
| 4. edge cases consistent? | yes | yes | yes | yes |
| 5. declared signature correct? | verified | verified | verified | verified |
| 6. matches the selected contract? | v1 ✓ | **v3 ✓** | v1 ✓ | v1 ✓ |
| 7. hidden assumption that should block? | none | none | **the 26-letter wording** | **the Fibonacci derivation** |

Rows 5 and 6 are machine-checked against the stored row: signature name and
arity match the question's declared starter, and every stored case binds under
the question's contract — including q1974's fourteen through the v3 envelope.

Rows 2, 3, 4 and 7 are my reading and carry the same author bias as the
references themselves.
