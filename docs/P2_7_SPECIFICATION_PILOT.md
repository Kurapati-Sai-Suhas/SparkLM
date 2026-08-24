# P2.7h-21 — Specification → artifact pilot

Proves the transformation **SPECIFICATION → ARTIFACT**, replacing the
TITLE → ARTIFACT path that Phase 5 measured at 60% correct. No production
write of any kind; bank fingerprint unchanged.

---

## The scorecard

| | rate | |
|---|---|---|
| deterministic validation | **5/5** | structure, signature, HTML, digests, manifest |
| conformance to specification | **5/5** | no requirement lost or substituted |
| semantic — faithful to its specification | **5/5** | every requirement expressed correctly |
| **semantic — fit to show a learner** | **1/5** | see below |

The first three numbers are the pipeline working. The fourth is the finding.

---

## The finding: conformance rewards transcription

Four of five statements are **correct and unusable**. The model discovered
that the safest way to pass a term-overlap check is to copy the specification,
so it reproduced the spec's internal field structure verbatim into the
learner-facing statement:

> **Objective** Given a list of positive integers, report the greatest common
> divisor of exactly two of them… **Required operation** Find the minimum
> element… **Input semantics** One parameter, nums…

Worse, q1974 carried the specification's own meta-commentary through to the
learner:

> "The greatest common divisor of the whole list is **NOT what is wanted** and
> is a different quantity."

That sentence exists to steer the generator away from the Phase 5 defect. It
is guidance about authoring, and a learner should never see it.

**This is a designed-in incentive, not a model failure.** Conformance scores an
artifact on retaining the specification's vocabulary; perfect transcription
scores perfectly. The check cannot distinguish "expressed the requirement" from
"pasted the requirement", and the cheapest way to satisfy it is the second.

Only **q1940** reads like a real problem statement — and it took three attempts,
so the improvement came from regeneration pressure, not from the prompt.

The fix is not to weaken conformance. It is to add a presentation check that
conformance cannot satisfy by copying: refuse statements containing
specification field labels, refuse meta-commentary addressed to the author, and
require a worked example the specification did not supply. That is the next
phase's work and it is listed at the end.

---

## What was built

### 7A — specification freeze

`groups/reseed_specification.py`. A specification is JSON carrying question id,
canonical identity, and eight prose fields: objective, required operation,
input semantics, output semantics, constraints, edge cases, load-bearing
requirements, method behaviour. Provenance must be `OPERATOR_SUPPLIED`;
anything else is refused by name, because a specification reconstructed from a
title is exactly what Phase 5 proved unsafe.

Canonicalisation fixes field order and collapses whitespace, so reformatting
does not move the digest but changing a single load-bearing word does. Below
240 characters of prose a specification is refused as "a title with extra
steps".

Five frozen, in `artifacts/pilot-slice-1/specifications/`, with a freeze
manifest recording digest, provenance, author, and the author's own confidence.

### 7B — the model is a formatter

`GenerationSpec` now carries `specification` and `specification_digest`, and
**`build_spec` refuses without one.** There is no title-only path left.

The prompt (`specification-formatter/v2`) states the specification is the
source of truth, instructs the model to reword and mark it up, and forbids
adding, removing or altering any requirement. Everything the model needs is in
the prompt, so anything it adds is something it made up.

### 7C — conformance is composed, and proven composed

`validate_artifact` now ends with `+ conformance_refusals(spec, statement)`.

Phase 5 lost a mutation exactly here: dropping a check from this expression
changed nothing, because every test called the checker directly. So the proof
was run:

```
conformance call removed from validate_artifact  ->  6 tests fail
  test_validate_artifact_calls_conformance
  test_a_changed_requirement_is_refused_through_the_composition  (x3)
  test_a_changed_adjacency_requirement_is_refused
  test_a_changed_extremal_condition_is_refused
```

Covered through the composition: missing requirement, substituted operation,
changed quantifier, changed extremal condition (the q1974 defect), changed
adjacency requirement (the q1970 defect), specification digest mismatch,
missing specification, empty specification, stale specification, legitimate
paraphrase.

**The limitation is a test, not a caveat** —
`test_KNOWN_LIMITATION_a_substitution_survives_if_the_term_does` shows a
statement whose operative verb changed from `count` to `sum` passing, because
`count` still appears elsewhere in the prose. Conformance detects requirement
*loss*. It is not a semantic equivalence proof, and human review stays
mandatory.

---

## Per-artifact results

All five: provider `openai/gpt-oss-120b`, prompt `specification-formatter/v2`,
`applicable=false` (no frozen batch exists — creating one is a production
write).

| q | spec digest | artifact digest | det. | conf. | semantic | remaining concern |
|---|---|---|---|---|---|---|
| 1940 | `3e837b04…` | `60778d2b…` | ✅ | ✅ | ✅ faithful, **reads well** | 3 attempts to get there |
| 1974 | `ad2a9eb0…` | `d9595db0…` | ✅ | ✅ | ✅ faithful | **leaks spec meta-commentary to the learner** |
| 2027 | `f6937c82…` | `8abd8274…` | ✅ | ✅ | ✅ faithful | spec field labels in the statement |
| 2057 | `d8febbe3…` | `c36815ba…` | ✅ | ✅ | ✅ faithful | spec field labels in the statement |
| 2290 | `37f42307…` | `60df2986…` | ✅ | ✅ | ✅ faithful | field labels; "one thousand million and seven" instead of 10⁹+7 |

Signatures declared, all correct against their specifications:

```
1940  sumOfDigitsOfStringAfterConvert(s: str, k: int) -> int
1974  findGreatestCommonDivisorOfArray(nums: list[int]) -> int
2027  removeColoredPiecesIfBothNeighborsAreTheSameColor(colors: str) -> bool
2057  checkWhetherTwoStringsAreAlmostEquivalent(word1: str, word2: str) -> bool
2290  countNumberOfWaysToPlaceHouses(n: int) -> int
```

**q1974 is the regression proof.** Phase 5 generated "the GCD of all elements"
from the title. Given a specification naming the smallest and largest, the
pipeline produced the correct problem, and conformance would have refused the
Phase 5 text.

### Slice composition

5 topics (Array, String, Math, Hash Table, Dynamic Programming), 3 easy and
2 medium, 5 input shapes (`list[int]`, `(str, int)`, `str`, `(str, str)`,
`int`), 5 patterns (number theory, simulation, combinatorial game, frequency
counting, DP with modular arithmetic). q2201 excluded throughout.

---

## What the pilot cost, and what that taught

The first run produced **1 of 5**. Getting to 5 took four real fixes, three of
them defects in my own work:

**The check imported negations.** `method_behaviour` says "does not print",
which made `print` a required word — so a statement that correctly never
mentioned printing was refused. Three artifacts lost to this. Fixed by
excluding `method_behaviour` from conformance entirely: it describes the
method's *shape*, which `validate_signature` already enforces structurally and
exactly.

**`"rate" in message` matched "Failed to gene*rate* JSON".** A malformed model
response was reported as a quota exhaustion and the retries that would have
fixed it were skipped. Now matched on word boundaries.

**The title heuristic outlived its purpose.** The title-overlap check exists
for title-only generation. A specification written in British English
("coloured", "neighbours") against a title reading "Colored … Neighbors" lost
4 of 7 title words and was rejected despite matching its specification
exactly. It is now disabled whenever a specification is attached — the
specification is the truth, and conformance checks against it directly.

**Specifications need a writing style.** Two were rejected for ordinary prose
words the vocabulary treats as requirements: "the **first** string" (meaning
word1) and "**First** replace every letter" (meaning "begin by"). Both were
fixed **in the specification, not the validator** — naming parameters instead
of positions, and dropping sequencing words. That is the intended workflow: the
validator teaches a controlled vocabulary through refusal. It is also a real
cost, and operators should be told about it before they write 1,141 of these.

A synonym table was added for genuinely interchangeable pairs
(`adjacent`/`neighbouring`, `concatenate`/`join`, `return`/`output`,
`minimum`/`smallest`, `maximum`/`largest`). It is deliberately short — a
generous one would let a real substitution through under cover of "near
enough" — and anything outside it needs an operator's recorded
`allow_omitted`.

---

## The caveat that outranks the scorecard

**The specifications were drafted by the assistant, not by an operator.** That
makes assistant recall the source of truth — the same fallible source Phase 5
measured at 60%. Every specification file records this in its `author` field
and carries the drafter's own `author_confidence`.

So this pilot proves the *transformation* is sound. It does **not** establish
that these five questions are correct, because nothing in the pipeline can:
conformance proves the statement matches the specification, and no check
anywhere proves the specification matches reality.

**Before any of these five is applied, a human must read each specification
against what the question should ask.** That is the step option (d) was chosen
for, and it has not happened yet.

---

## Regression

```
2918 passed, 2 warnings in 241s      (pytest --ignore=scripts)
reseed test surface: 204 passed      (generation, conformance, authoring, authority, ledger)
makemigrations --check: No changes detected
```

One self-inflicted detour worth recording: an earlier run reported 3 failures
and 20 errors, all in `test_reseed_authority.py`, with
`duplicate key … auth_permission … add_logentry`. That is the signature of two
pytest sessions sharing one `--reuse-db` database — I had started a second run
while the first was still going, and those tests create and drop real database
roles. Nothing was wrong with the code. Run alone, the suite is green.

## Production

```
questions 2926 · candidates 1141 · ledger rows 0 · batches 1 · actions 17 · submissions 44
q1940/1974/2027/2057/2290 — all DRAFT/UNVERIFIED, placeholder intact, 0 cases
bank fingerprint 3c886bc48a96cd107bf894ed56acc66f859286a0d636a896916ff155ca5f49f6  unchanged
```

No `reseed_statement`, no `declare_signature`, no hidden tests, no oracle, no
batch, no ledger row.

---

## Next, in order

1. **Human verification of the five specifications** — the gate this phase
   cannot pass on its own.
2. **A presentation validator** conformance cannot satisfy by copying: refuse
   specification field labels in a statement, refuse author-directed
   meta-commentary, require a worked example the specification did not supply.
3. Only then: a frozen production batch, pre-images, and the first application
   of an artifact through `reseed_statement` and `declare_signature`.
