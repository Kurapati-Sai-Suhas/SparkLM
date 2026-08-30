# P2.18b — Mutant review pack

**Status:** DECIDED AND RUN. All 33 mutants approved by the operator on
2026-08-29; the gate ran against the stored production suites. **All four
PASS.** No approval, promotion or publication followed — those are separate
states with separate commands.

---

## Gate results — 2026-08-29

| Question | Verdict | Tier-1 | Tier-2 (equivalents excluded) | Blockers |
| --- | --- | --- | --- | --- |
| q1940 | **PASS** | 1.0 (5/5) | 1.0 (3/3) | none |
| q1974 | **PASS** | 1.0 (5/5) | 1.0 (2/2) | none |
| q2057 | **PASS** | 1.0 (5/5) | 1.0 (2/2) | none |
| q2290 | **PASS** | 1.0 (5/5) | 1.0 (2/2) | none |

Thresholds used are the project's existing ones: Tier-1 ≥ 1.0, Tier-2 ≥ 0.80,
minimum 12 hidden tests. Zero malformed cases, zero duplicates, no missing
categories on any of the four.

All four declared equivalents were recognised as `EQUIVALENT` and excluded
from both numerator and denominator, exactly as designed — none was counted
as a kill.

Canonical digests (of the parsed JSON, so they are stable regardless of line
endings — the raw files are CRLF on this platform):

| Question | Spec digest | Report digest |
| --- | --- | --- |
| q1940 | `b5021f82e0a293931e462cc7613d94fe…` | `85b068b6825ede7bc56702a05490f392…` |
| q1974 | `bd542ce671a26b442c1ea3e777dd6f6f…` | `5846337642cfd6aca9d5aec9c0a67072…` |
| q2057 | `31b54d8b920a888bce6a856d081b6f44…` | `88c03edc2b44a6df7ae99449be66d0d0…` |
| q2290 | `c88c74f43037ec8047e781a29b24c832…` | `92d2c3bd169ac837d85b9c024c1831ce…` |

> The raw-byte digests quoted lower down in this document were computed
> before the files were committed, when they still had LF endings. They no
> longer match the files on disk. The canonical digests above are the ones to
> use.

### q1974 failed first, and why

The first q1974 run returned **FAIL** — not on mutants, which were already
1.0/1.0, but on `missing categories: singleton`.

The generic `singleton` rule fires for any sequence problem. q1974's
specification requires **2–1000** integers, so a length-1 list is not a valid
input and testing it would test behaviour outside the contract. That is what
`CategorySubstitution` exists for, and the reconstructed spec omitted one.

The stored suite already carried the right case, labelled **`singleton_pair`**
— the degeneracy where minimum equals maximum and the participating pair
collapses to one value. Phase 19 named it that deliberately. A substitution
was added pointing at that existing label.

**The suite was not modified.** No case was added, relabelled or removed; the
substitution names a case that was already there. This is recorded in the
spec's `provenance.substitution_note`, because it was added *after* the
operator approved the mutant catalogue and it changed the verdict.

### What the gate confirmed about coverage

Two of the mutants written expecting them to survive were killed:

- **q2057 `t1-only-letters-in-both-words`** — the near-miss of the approved
  reference, killed by case 1. The suite does catch the intersection bug.
- **q2290 `t1-no-modulo-reduction`** — invisible until n ≈ 23, killed by case
  8. The suite does carry a large-n case.

Coverage is assessed by the `category` label on each stored case, not by
analysing inputs. All 59 cases across the four suites are labelled.

---

*The original review pack follows, unchanged apart from this header.*

> ## NEW RECONSTRUCTED QUALITY-GATE ASSESSMENT
>
> P2.7 Phase 19 authored these four suites mutant-first and drove the gate to
> a pass in that session. **Those mutant specifications were never persisted
> and are not recoverable.** This catalogue is new, written after the suites
> were already fixed.
>
> Nothing produced from it may be reported as "the Phase 19 gate passed". It
> is a reconstructed post-hoc assessment and is labelled as such in every
> spec file's `provenance.assessment_kind`.

---

## The methodological commitment

P2.18 flagged the weakness in re-authoring these: mutants written against a
known suite come from someone who already knows what the suite catches.

**So the stored hidden suites were never consulted while writing them.** The
catalogue was derived from each question's specification alone, and no mutant
was checked against a suite before this pack was produced. That is recorded
in each spec's `provenance.method`.

The consequence is deliberate: **the gate may fail.** Several Tier-1 mutants
below were written expecting them to survive. A failure would be a true
finding about suite coverage, and the correct response would be to strengthen
the suite — not to weaken the catalogue.

---

## What was verified before this pack

Every mutant was executed against the approved reference across a
specification-derived input space (400–500 inputs per question):

- **20 Tier-1 mutants** — all confirmed genuinely incorrect, each with a
  concrete distinguishing input recorded below.
- **13 Tier-2 mutants** — 9 confirmed incorrect, **4 declared EQUIVALENT**
  and confirmed to have *no* distinguishing input anywhere in the space.

A declared equivalent that turned out to be distinguishable would have
inflated the kill rate; a non-equivalent with no distinguishing input would
have looked like a suite gap that was not one. Neither occurred.

| Question | Spec file digest | Mutants | Tier-1 | Tier-2 | Equivalent |
| --- | --- | --- | --- | --- | --- |
| q1940 | `8df7d75cbd81865e50db76389b25aa6f…` | 9 | 5 | 4 | 1 |
| q1974 | `4c5d45631468c0c91700a38d839c050c…` | 8 | 5 | 3 | 1 |
| q2057 | `4eb5e08ae725b04e771f4217687f6c77…` | 8 | 5 | 3 | 1 |
| q2290 | `f39331497fbf304f1b4c55f276318d04…` | 8 | 5 | 3 | 1 |

Stored suite digests, unchanged and untouched by this phase:

| Question | Hidden suite digest | Cases |
| --- | --- | --- |
| q1940 | `995d97a836749fb6285abdf9288e6e8a…` | 15 |
| q1974 | `ac416cbf8e8c4071116f0285fcd7d53b…` | 14 |
| q2057 | `0f1f617ea44705c286b340eb5c0a1987…` | 16 |
| q2290 | `7937bae0b5aa2a3c570d11be33a661b0…` | 14 |

---

## The decision

Mark each mutant **APPROVE** / **REJECT** / **NEEDS_REVIEW**. Rejecting one
removes it from the catalogue; the gate then runs on what remains.

The question to ask of each Tier-1 entry is: *would a learner who
misunderstood this problem actually write this?* A mutant that no learner
would produce inflates the kill rate for free.

---

### q1940 — Sum of Digits of String After Convert

*Reference #4 · contract `v1` · 15 hidden cases*

| Mutant | T | What it does wrong | Distinguishing input | ref → mutant | Decision |
| --- | --- | --- | --- | --- | --- |
| `t1-sums-positions-instead-of-concatenating` | 1 | Adds alphabet positions instead of concatenating. The specification's load-bearing sentence. | `("zz", 1)` | `16` → `7` | ☐ |
| `t1-alphabet-is-zero-based` | 1 | `ord(c) - 97`, so a maps to 0. The standard Python idiom, wrong here. | `("a", 1)` | `1` → `0` | ☐ |
| `t1-single-round-ignores-k` | 1 | One digit-sum round regardless of k. Every k=1 case still passes. | `("zz", 2)` | `7` → `16` | ☐ |
| `t1-digital-root-instead-of-k-rounds` | 1 | Sums until one digit remains. Agrees whenever k reaches the fixed point. | `("zz", 1)` | `16` → `7` | ☐ |
| `t1-rounds-run-k-minus-one-times` | 1 | Counts the conversion as the first round. | `("z", 1)` | `8` → `26` | ☐ |
| `t2-position-offset-95` | 2 | Boundary nudge. | `("a", 1)` | `1` → `2` | ☐ |
| `t2-max-digit-not-sum` | 2 | Operator swap: max instead of sum. | `("z", 1)` | `8` → `6` | ☐ |
| `t2-drops-last-character` | 2 | Loop bound short by one. | `("a", 1)` | `1` → `0` | ☐ |
| `t2-sum-digits-from-string-directly` | 2 | **EQUIVALENT** — verified, no distinguishing input | — | — | ☐ |

**Equivalence argument** (`t2-sum-digits-from-string-directly`): no alphabet
position is 0, so the concatenation has no leading zero and `int(text)`
round-trips to exactly `text`. Summing the digits of `text` directly gives
the same first-round value; the remaining k−1 rounds are identical.

---

### q1974 — Find Greatest Common Divisor of Array

*Reference #5 · contract `v3` · 14 hidden cases*

| Mutant | T | What it does wrong | Distinguishing input | ref → mutant | Decision |
| --- | --- | --- | --- | --- | --- |
| `t1-gcd-of-every-element` | 1 | Folds gcd across the whole list. The likeliest reading of the title, and contradicts the load-bearing "exactly two values participate". | `[4, 6, 8]` | `4` → `2` | ☐ |
| `t1-first-and-last-element` | 1 | Assumes the list arrives sorted. | `[6, 2, 3]` | `2` → `3` | ☐ |
| `t1-returns-the-smallest-value` | 1 | Returns `min`. Correct whenever the minimum divides the maximum. | `[7, 5, 6, 8, 3]` | `1` → `3` | ☐ |
| `t1-lcm-instead-of-gcd` | 1 | The pair most often swapped. | `[2, 5, 6, 9, 10]` | `2` → `10` | ☐ |
| `t1-second-smallest-and-largest` | 1 | `sorted(nums)[1]` instead of `[0]`. | `[2, 5, 6, 9, 10]` | `2` → `5` | ☐ |
| `t2-max-with-itself` | 2 | Operand swap. | `[2, 5, 6, 9, 10]` | `2` → `10` | ☐ |
| `t2-difference-not-gcd` | 2 | Operator swap. | `[2, 5, 6, 9, 10]` | `2` → `8` | ☐ |
| `t2-operands-reversed` | 2 | **EQUIVALENT** — verified | — | — | ☐ |

**Equivalence argument** (`t2-operands-reversed`): gcd is commutative — the
set of common divisors of a pair does not depend on its order.

---

### q2057 — Check Whether Two Strings are Almost Equivalent

*Reference #6 · contract `v1` · 16 hidden cases*

| Mutant | T | What it does wrong | Distinguishing input | ref → mutant | Decision |
| --- | --- | --- | --- | --- | --- |
| `t1-strict-less-than-three` | 1 | `< 3`; the spec says exactly three is acceptable. | `("abcdeef", "abaaacc")` | `True` → `False` | ☐ |
| `t1-total-difference-across-letters` | 1 | Treats 3 as a budget over the whole word. | `("abcdeef", "abaaacc")` | `True` → `False` | ☐ |
| `t1-only-letters-in-both-words` | 1 | **The near-miss of the approved reference** — intersection instead of union, skipping exactly the letters whose counts differ most. | `("aaaa", "bccb")` | `False` → `True` | ☐ |
| `t1-signed-difference-no-absolute-value` | 1 | Drops `abs`; a letter far more frequent in word2 passes. | `("bcde", "aaaa")` | `False` → `True` | ☐ |
| `t1-compares-sorted-strings` | 1 | Confuses "almost equivalent" with "anagram". | `("abcdeef", "abaaacc")` | `True` → `False` | ☐ |
| `t2-threshold-four` | 2 | Boundary nudge. | `("aaaa", "bccb")` | `False` → `True` | ☐ |
| `t2-any-instead-of-all` | 2 | Quantifier swap. | `("aaaa", "bccb")` | `False` → `True` | ☐ |
| `t2-iterates-all-twenty-six-letters` | 2 | **EQUIVALENT** — verified | — | — | ☐ |

**Equivalence argument** (`t2-iterates-all-twenty-six-letters`): a letter
absent from both words has difference 0, which satisfies ≤ 3
unconditionally, so the full alphabet adds only always-true conjuncts.

This is the traversal the specification's `load_bearing` field describes.
You approved the union form in P2.15 on exactly this reasoning; encoding it
as a verified equivalent makes that ruling machine-checked from here on.

---

### q2290 — Count Number of Ways to Place Houses

*Reference #7 · contract `v1` · 14 hidden cases*

| Mutant | T | What it does wrong | Distinguishing input | ref → mutant | Decision |
| --- | --- | --- | --- | --- | --- |
| `t1-one-side-only` | 1 | Solves one side and forgets the street has two. The central trap. | `n=1` | `4` → `2` | ☐ |
| `t1-plain-fibonacci-base` | 1 | Starts at 1,1 — recognises the shape without re-deriving the base. | `n=1` | `4` → `1` | ☐ |
| `t1-adjacency-across-the-street` | 1 | Models the street as one row of 2n, imposing a constraint the spec denies. | `n=1` | `4` → `3` | ☐ |
| `t1-no-modulo-reduction` | 1 | Never reduces. Invisible in Python until the square exceeds the modulus, ≈ n=23. | `n=22` | `149991410` → `2149991424` | ☐ |
| `t1-loop-runs-n-times` | 1 | Off-by-one on the loop bound. | `n=1` | `4` → `9` | ☐ |
| `t2-modulus-off-by-one` | 2 | Boundary nudge on the prime. | `n=22` | `149991410` → `149991412` | ☐ |
| `t2-doubles-instead-of-squaring` | 2 | Operator swap: `2*b` not `b*b`. | `n=2` | `9` → `6` | ☐ |
| `t2-reduce-only-at-the-end` | 2 | **EQUIVALENT** — verified | — | — | ☐ |

**Equivalence argument** (`t2-reduce-only-at-the-end`): modular reduction is
a ring homomorphism, so reducing at every step and reducing once at the end
give the same residue. Python integers are arbitrary precision, so the unreduced
intermediate cannot overflow.

---

## What happens after you decide

Only after explicit approval, and only against the **stored production
suites** — which this phase did not touch and will not replace:

```bash
python manage.py quality_gate --question <id> --operator <you> \
    --spec backend/LearnLM/quality/q<id>_quality_spec.json \
    --report-out backend/LearnLM/reports/q<id>-quality.json
```

The gate assesses Tier-1 kill rate (threshold **1.0**), Tier-2 kill rate
(threshold **0.80**), minimum hidden tests (**12**), category coverage,
duplicates, reachability and contract compatibility, using the project's
existing thresholds. It is read-only with respect to the question.

If a suite fails, the honest outcome is a failed gate and a suite that needs
strengthening — not a smaller mutant list.

---

## Production state

**Unchanged.** This phase wrote four files to `backend/LearnLM/quality/` and
nothing else. No question, suite, reference, Oracle row, approval or
submission was touched.

| | |
| --- | --- |
| q1940 / q1974 / q2057 / q2290 | all `DRAFT` / `UNVERIFIED` |
| Question approvals | 0 for the pilot (2 globally, both pre-existing) |
| Promotions | 0 |
| Publications | 0 |
