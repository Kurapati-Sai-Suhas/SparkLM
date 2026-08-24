# P2.7h-23 — Operator/external specification verification

Read-only. No production write of any kind, no artifact regenerated, no
specification edited.

**Headline: 0 of 5 specifications are independently verified, and 1 of 5
examples is contradicted by its own specification.** The pilot is not safe to
freeze.

---

## The constraint this phase operates under

I wrote these five specifications, from recall, in Phase 7. If I now certify
them from that same recall, the result is one source agreeing with itself —
which is not verification, and is precisely the failure mode Phase 5 measured
at 60%.

So this report separates two questions that have been running together:

| question | verifiable here? |
|---|---|
| does the example agree with its specification? | **yes** — implement the stated operation and run it. Arithmetic, not authority. |
| does the specification describe the intended problem? | **no** — that needs a source, and Phase 6 established there isn't one. |

Everything below respects that division.

---

## 9A — digest integrity: INTACT

```
q1940  live 3e837b04ce81d24f  file 3e837b04ce81d24f  freeze 3e837b04ce81d24f  MATCH
q1974  live ad2a9eb09a60a7a8  file ad2a9eb09a60a7a8  freeze ad2a9eb09a60a7a8  MATCH
q2027  live f6937c82a0f38eec  file f6937c82a0f38eec  freeze f6937c82a0f38eec  MATCH
q2057  live d8febbe3bb5f02ae  file d8febbe3bb5f02ae  freeze d8febbe3bb5f02ae  MATCH
q2290  live 37f4230790e3c760  file 37f4230790e3c760  freeze 37f4230790e3c760  MATCH
```

All five Phase 8 artifacts trace to these digests. Nothing has drifted.

---

## 9B — authoritative source search: NONE FOUND

Phase 6's finding stands, re-checked against current state:

| source | verdict |
|---|---|
| `groups_question` | no provenance field |
| pre-M2 SQL backup | placeholders only |
| git history | no statement corpus ever tracked |
| `Leetcode_Questions*.csv` | **identity only** — number, slug, difficulty, tags, acceptance. No behavioural text. |
| `media/study_materials/` (22,123 learner uploads, new since Phase 6) | checked: **no mention of any of the five problems**; sampled content is junk test data |
| operator-supplied specifications | **none provided** |
| canonical URLs | HTTP 403 to automated access; not circumvented |

The CSV row for each of the five gives topic tags and an acceptance rate. For
q1974 that is `['Array','Math','Number Theory']` at 78.3% — which is equally
consistent with both candidate readings and therefore has **zero discriminating
power**. Title match is not specification verification.

**Consequence: source authority level = NONE for all five.**

---

## 9C — requirement-by-requirement classification

Because no authoritative source exists, every *semantic* requirement in every
specification classifies as **NOT VERIFIABLE**. It would be dishonest to record
otherwise, and collapsing "not verifiable" into "verified" is the specific
error this phase exists to prevent.

What *can* be established, and was:

| check | method | result |
|---|---|---|
| internal consistency (spec contradicts itself?) | inspection of all 8 fields | **5/5 consistent** |
| signature agreement (declared parameters match described input) | compare spec `input_semantics` to the declared starter | **5/5 agree** |
| example within stated constraints | evaluate constraints against example input | **5/5 within** |
| example agrees with the stated operation | execute the operation, compare | **4/5 consistent, 1 contradicted** |

```
q1940  sumOfDigitsOfStringAfterConvert(s: str, k: int) -> int        input matches spec
q1974  findGreatestCommonDivisorOfArray(nums: list[int]) -> int      input matches spec
q2027  removeColoredPiecesIfBothNeighborsAreTheSameColor(colors: str) -> bool
q2057  checkWhetherTwoStringsAreAlmostEquivalent(word1: str, word2: str) -> bool
q2290  countNumberOfWaysToPlaceHouses(n: int) -> int                 input matches spec
```

These are real findings — a specification that contradicted itself or
mismatched its signature would be defective regardless of any source. None
does. But **internal consistency is not correctness**, and none of it bears on
whether the intended problem is the one described.

---

## 9D — the named regression checks

### q1974 — GCD(smallest, largest) vs GCD(all elements)

**NOT VERIFIABLE. I cannot resolve this, and saying otherwise would be the
exact error this phase exists to catch.**

The specification asserts GCD of the smallest and largest. Phase 5's title-only
generator produced GCD of all elements. These give different answers — for
`[3, 6, 4]`, 3 versus 1 — so at most one is right, and nothing accessible
distinguishes them:

- the CSV has no behavioural text
- the canonical URL is 403
- **my own recall is the source that wrote the specification**, so invoking it
  again adds no evidence

The specification is *internally* consistent and its example agrees with it.
That is all that can be said. **This is the single most important unresolved
item in the pilot**, because Phase 5 demonstrated the alternative reading is
the one a model reaches unaided.

### q1970 — adjacent-pair sign flip vs single-cell flip

**Out of scope: q1970 is not in the pilot slice.** It was a Phase 5 artifact
(LeetCode 1975, `maximum-matrix-sum`); the five frozen specifications are
q1940, q1974, q2027, q2057 and q2290. No specification for it exists to verify,
and its Phase 5 finding remains an unverified assertion for the same reason as
q1974.

### q2027 — the incorrect example

The three-way question is answered definitively **by execution**:

```
example input   ('AABAA',)
artifact claims True
spec computes   False          ← the specification's own stated rule
verdict         CONTRADICTED
specification carries an example?  no
```

Implementing the specification exactly as written — Alice may remove an `A`
only when **both** immediate neighbours are `A`; a player unable to move
loses — gives Alice **zero** legal moves on `"AABAA"` (the middle character is
`B`; neither interior `A` has two `A` neighbours). Alice moves first, cannot
move, and loses. The correct answer under the specification is `false`.

**Answer: (2) the generated artifact introduced the incorrect example.** The
specification contains no example at all, so the model invented both the input
and a false narrative about it ("Alice can remove the middle A"). This is not a
specification defect; it is a generation defect that every gate passed.

---

## 9E — specification style review

Phase 8 established the mechanism: a load-bearing word inside a *negation* or
an *aside* becomes mandatory vocabulary in the learner-facing statement. The
review distinguishes three kinds of negation, which matters:

- **load-bearing negation** — *is* the requirement ("two houses may not occupy
  adjacent plots"). Must stay.
- **contrastive negation** — clarifies by exclusion ("Both neighbours must
  match, not one"). Inflates vocabulary; usually harmless.
- **meta-commentary** — addressed to whoever is writing the question. Should
  never be in a specification.

| q | required terms | findings | terms mandated ONLY by a negative clause |
|---|---|---|---|
| q1940 | 8 | contrastive negation ×2, `method_behaviour` negation | none |
| **q1974** | **19** | **meta-commentary ×2**, illustrative aside, negations ×4 | **adjacent, any, different, pair, sorted** |
| q2027 | 11 | contrastive + legitimate edge-case negation | last |
| q2057 | 16 | contrastive negation | none |
| q2290 | 14 | **load-bearing** negation (the core rule) | adjacent, exactly |

**q1974 requires style review.** It carries "The greatest common divisor of the
whole list is NOT what is wanted and is a different quantity" — pure authoring
guidance — plus "Not all elements. Not any adjacent pair." and an illustrative
"for [3, 6, 4]…". Between them these mandate five terms that appear *only* in
negative clauses, which is why the Phase 8 artifact reads "no other pair, not
any adjacent pair, and not all elements are considered".

**q2290 is the instructive counter-example**: `adjacent` is mandated only by a
negative clause, and that is *correct* — "may not occupy adjacent plots" is the
whole problem. A blanket rule stripping negated spans would delete the
requirement. Any future fix must distinguish the three kinds above, not treat
"negation" as a defect.

`method_behaviour` negations are inert: Phase 7 excluded that field from
conformance.

**Nothing was edited.** q1974 is reported as `SPEC_STYLE_REVIEW_REQUIRED`.

---

## 9F — example verification, by execution

Each specification's stated operation was implemented from its prose alone, in
a local verification script, and run against the example its Phase 8 artifact
claims:

```
q1940  ("abc", 2)      claims 6     spec computes 6      CONSISTENT
q1974  ([3, 6, 4],)    claims 3     spec computes 3      CONSISTENT
q2027  ("AABAA",)      claims True  spec computes False  *** CONTRADICTED ***
q2057  ("abc","bcd")   claims True  spec computes True   CONSISTENT
q2290  (1,)            claims 4     spec computes 4      CONSISTENT
```

All five examples satisfy their specification's stated constraints, and all
five exercise the stated operation.

**But "CONSISTENT" here means consistent *with the specification*, and the
specification is unverified.** An example can agree perfectly with a
specification that describes the wrong problem. So four examples are
`EXAMPLE_CONSISTENT_WITH_UNVERIFIED_SPEC`, not `VERIFIED`.

One further finding: **q1974's example is not independently constructed.** Its
specification embeds an illustrative aside — "for [3, 6, 4] the smallest is 3
and the largest is 6" — and the artifact's example reuses exactly those
numbers. The presentation gate's "lifted verbatim" check requires a 12-word run
and did not fire on a bracketed literal. The example therefore tests nothing the
specification did not already assert.

---

## 9G — no writes

```
Question rows              0 changed        ledger rows        0
content / boilerplate      0 changed        batches            1 (pre-existing)
hidden tests               0 written        remediation actions 17 (unchanged)
expected outputs           0 written        pre-images         7 (unchanged)
status / trust_state       0 changed        approvals          2 (unchanged)
oracle executions          0                migrations         none
roles / grants             0 changed        specifications     0 edited
```

Local verification artifacts were created in the scratchpad only.

---

## 9H — decision matrix

| Question | Spec digest | Authoritative source | Spec correctness | Example correctness | Style | Overall |
|---|---|---|---|---|---|---|
| q1940 | `3e837b04…` | NONE | NOT VERIFIABLE | consistent with unverified spec | acceptable | **UNVERIFIED** |
| q1974 | `ad2a9eb0…` | NONE | NOT VERIFIABLE | consistent, but lifted from spec aside | **meta-commentary + 5 negative-only terms** | **SPEC_STYLE_REVIEW_REQUIRED** |
| q2027 | `f6937c82…` | NONE | NOT VERIFIABLE | **CONTRADICTED by its own spec** | acceptable | **CONTRADICTED** |
| q2057 | `d8febbe3…` | NONE | NOT VERIFIABLE | consistent with unverified spec | acceptable | **UNVERIFIED** |
| q2290 | `37f42307…` | NONE | NOT VERIFIABLE | consistent with unverified spec | acceptable | **UNVERIFIED** |

`CONTRADICTED` for q2027 refers to its **artifact's example**, not its
specification: the specification is unverified like the rest, and the artifact
disagrees with it.

---

## 9I — next-gate decision

**1. Independently verified specifications: 0 of 5.**
**2. Contradicted: 0 specifications** (1 artifact example contradicts its spec).
**3. Unverified: 5 of 5.**
**4. Independently verified examples: 0 of 5.** Four are consistent with an
unverified specification; one is contradicted. Consistency with an unverified
source is not verification.

**5. Is the pilot safe to freeze? NO.**

**6. What evidence is missing —** exactly one thing, and no amount of further
engineering produces it:

> An authority, other than the assistant, stating what each of these five
> questions is supposed to ask.

Every other property has been established: digests intact, specifications
internally consistent, signatures agreeing, examples within constraints, four
examples consistent with their specification, both gates passing, production
untouched. The pipeline is sound. Its **input** is unattested.

Concretely, for each of the five, one of:
- an operator (you) reading the specification and confirming it, or
- a licensed/openly-licensed corpus entry, or
- any accessible authoritative source that distinguishes q1974's two readings.

**7. Minimum next engineering phase — and it is not engineering.**

The next step is **operator review of five specification files**, roughly ten
minutes of reading, recorded as a signed field in each file
(`verified_by`, `verified_at`, `verification_source`) with the digest it was
verified against. Nothing else can advance the pilot, and no further validator
changes that.

Two small engineering items are ready to follow *after* that, not before:

- **q2027's artifact must be regenerated** — its example is wrong under its own
  specification, and the presentation gate cannot catch it. Consider an
  example-execution check for the subset of problems whose operation can be
  expressed as a short reference — that is what caught it here, and it could be
  automated for exactly this class.
- **q1974's specification needs a style pass** — meta-commentary removed,
  contrastive negations rewritten as positive statements. This is an edit to a
  frozen specification and needs a new digest, so it must be a deliberate,
  recorded change, not a silent one.

**Not started, per the brief:** no Phase 10, no production batch, no
`reseed_statement`, no `declare_signature`, no hidden tests, no oracle.
