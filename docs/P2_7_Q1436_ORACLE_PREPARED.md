# P2.7 — q3309 published and verified; q1436 prepared, and blocked on its suite

q3309 is **PUBLISHED + ORACLE_VERIFIED**, and the publication changed exactly
one column of one row. q1436 now has a reference solution awaiting human
review, a purpose-built quality spec — and a measured finding that stops the
phase where it should stop:

> **The current 4-case suite kills only 3 of 6 realistic wrong solutions.** A
> learner submitting `return paths[-1][1]` is marked correct by q1436 today.

The suite must be expanded before the oracle runs, exactly as q3309's was.

---

## A. q3309 post-publication verification

Read as `learnlm_census_ro`:

```
status / trust      PUBLISHED / ORACLE_VERIFIED     adaptive_eligible True
digest              a88a4e191e368f8d2a31b82b802ea3f8ea7c9315b9ae8bf3e6270309463bddd8  ✔
approval            #1  artifact b1df39f5…  ✔
                    approved 2026-08-19 17:07:13 by 1
                    promoted 2026-08-20 06:36:59 by 1
canonical reference #2 APPROVED active=True   hash 18ad8f39…  ✔
executions          24  {'SUCCESS': 24}
ORACLE_BACKED       12/12      agreeing runs [2]      blockers none
quality (frozen)    tier1 1.0 / tier2 1.0 — PASS
PUBLISHED           [3309]        ORACLE_VERIFIED  [3309]
```

**Only `status` moved.** Rather than asserting it, the check substitutes the
previous value back and requires the previous digest to return exactly:

```
rewind q3309.status → PENDING_REVIEW  ⇒  e7fbc767…  == the pre-publication digest ✔
rewind the same field in the whole-bank fingerprint
                                      ⇒  950b9850…  == the pre-publication fingerprint ✔
```

So the bank moved by exactly one column of one row. Current fingerprint:
`48ab66885eedcc0ac64cd591611caf378c22f102ad0af07c805b6fceaee54884`.

**Adaptive eligibility — what is and is not proved.** q3309 has **zero learner
submissions**, so the "before publication stays ineligible / after publication
is eligible" comparison is vacuous on production: 0 before, 0 after, 0
adaptive-eligible submissions in the entire bank. I did not manufacture a
submission on production to make the check non-vacuous.

The property itself is already proved on the safe non-production path the brief
allows — `groups/test_trust_boundary.py`, which solves real submissions against
a local database:

- `test_verifying_a_question_later_does_not_promote_past_submissions` — a day-1
  submission against an unverified question stays ineligible after the question
  is verified on day 20; the day-20 submission is eligible.
- `test_changing_question_status_never_rewrites_submissions` — the flag is not
  recomputed when status changes.
- `test_a_submission_against_a_verified_question_is_eligible` — the positive case.

So: q3309 is now in the state where new submissions **will** be eligible, and
nothing that already exists was rewritten. The first real confirmation will
come from the first submission recorded against it.

## B. Final q3309 status

```
status PUBLISHED · trust_state ORACLE_VERIFIED · adaptive_eligible True
approval #1 approved + promoted · reference #2 canonical · 24 executions
```

## C. q1436 current production state

```
digest        0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
status/trust  DRAFT / UNVERIFIED      adaptive_eligible False      contract v3
references 0 → now 1 (see D)   executions 0   approvals 0
actions       STATEMENT_REPAIR, INPUT_REPAIR, BOILERPLATE_REPAIR, CONTRACT_REPAIR
```

The digest is **identical to the value asserted unchanged in every phase since
the repair chain** — no drift. The four cases and their identities:

| # | stdin | expected | identity |
|---|---|---|---|
| 1 | `[["London","New York"],["New York","Paris"],["Paris","Rome"]]` | `Rome` | `27d637edaf67a4d5…` |
| 2 | `[["B","D"],["A","B"],["C","D"]]` | `D` | `991a0528a61ec16f…` |
| 3 | `[["A","Z"]]` | `Z` | `34928cbd31f148f5…` |
| 4 | `[["A","B"],["A","C"],["B","D"],["C","D"]]` | `D` | `9d4f0fb3d9288ece…` |

All four carry `category = None`. That matters — see F.

## D. The reference, and its lifecycle state

Authored at `remediation/q1436_reference_python.py`, created as
**ReferenceSolution #3**, `python`, and submitted:

```
DRAFT → IN_REVIEW        (is_active False, source_hash NOT yet set)
```

It is **not approved and not active**, per Step 4. Complete source:

```python
class Solution:
    def destCity(self, paths: list[list[str]]) -> str:
        """
        The destination city: the one that is never a source.
        ...
        """
        sources = {source for source, _destination in paths}
        for _source, destination in paths:
            if destination not in sources:
                return destination
        return ""
```

(The full file carries the reasoning docstring; the algorithm is the four lines
above.)

Mechanical checks:

```
public methods           ['destCity']                     — exactly one
declared signature       ('destCity', [('paths', 'list[list[str]]')])
starter signature        ('destCity', [('paths', 'list[list[str]]')])   — identical
non-variadic             yes (one positional parameter, no *args/**kwargs)
content sha256           2e0551949f7ca6ba6b5fa117b36e428b74e22e008c358937a0123e4ef889f10e
```

## E. Human-review findings

Against the repaired statement, its three worked examples, and all four hidden
cases:

| case | input | expected | reference | |
|---|---|---|---|---|
| 1 | London→NY→Paris→Rome | `Rome` | `Rome` | ✔ |
| **2** | `[["B","D"],["A","B"],["C","D"]]` | `D` | `D` | ✔ |
| 3 | `[["A","Z"]]` | `Z` | `Z` | ✔ |
| 4 | branching A→B, A→C, B→D, C→D | `D` | `D` | ✔ |

**On case 2 specifically — the defective cyclic/chain interpretation.** The
reference does not inherit it, and this is checked structurally rather than by
inspection: the answer is computed as a set difference over all edges, so it
cannot depend on edge order. Every permutation of every case was executed:

```
case 1: 1 distinct output across  6 permutations → {'Rome'}
case 2: 1 distinct output across  6 permutations → {'D'}
case 3: 1 distinct output across  1 permutation  → {'Z'}
case 4: 1 distinct output across 24 permutations → {'D'}
```

A chain-following implementation cannot have that property. Case 2's first edge
starts at `B`, which is not the start of anything, and case 4 is a tree rather
than a path — both break traversal-by-assumption, and neither affects a set
difference.

## F. Quality-gate design for q1436

The input contract, derived from the repaired statement rather than copied:

```
accepts_empty_input  false   the statement GUARANTEES a destination exists
is_sequence          true
has_size_bounds      false   the repaired statement states no bounds
allows_duplicates    true    cities repeat as sources and destinations
order_sensitive      FALSE   ← the opposite of q3309
numeric              false
overflow_sensitive   false
```

`order_sensitive = false` is the substantive difference from q3309 and it
changes which categories apply: `already_sorted` / `reverse_sorted` describe
nothing here, so both are **substituted** with recorded reasons —
`unordered_edges` (Example 2's out-of-order edges, the repaired defect) and
`branching_convergent` (a tree converging on one sink). Applicable required
generic categories reduce to **`singleton`** and **`duplicate_values`**.

Twelve mutants, none reused from q3309, each a complete runnable wrong program
(`quality/q1436_quality_spec.json`):

**Tier 1 — six wrong readings a learner can actually hold:** confusing the two
ends of the graph; last-edge destination; first-edge destination; most incoming
edges; alphabetically greatest city; single-successor chain walk.

**Tier 2 — six mechanical corruptions:** always empty; swapped pair order;
skip-first-edge off-by-one; first-edge-only; returns the candidate list;
inclusive source set.

**One mutant deliberately not claimed.** A faithful multi-successor traversal is
genuinely **equivalent** on any input satisfying the statement's guarantee —
every walk terminates at the unique sink — so asserting it as a killable Tier-1
mutant would be dishonest. Only its lossy single-successor form is included.

## G. Mutant results

Structural-only gate run (production read, `--structural-only`):

```
hidden tests 4 · malformed 0 · duplicates 0
missing categories: singleton, duplicate_values
canonical reference: NONE   (reference #3 is IN_REVIEW, by design)

BLOCKER: missing required categories: singleton, duplicate_values
BLOCKER: only 4 hidden tests; the floor is 12
QUALITY_GATE = FAIL
```

(The run also reports "no Tier-1/Tier-2 supplied" — that is `--structural-only`
passing an empty mutant list by design, not a spec problem. The spec loads: 6
Tier-1, 6 Tier-2, contract and substitutions all parsed.)

**The full gate was not executed through Judge0**, because it cannot pass: the
floor and the category coverage are structural blockers that no execution
result can clear. Instead the twelve mutants were executed offline against the
current four cases to measure what the suite actually discriminates:

```
KILLED    [T1] t1-source-not-destination
SURVIVED  [T1] t1-last-edge-destination
KILLED    [T1] t1-first-edge-destination
KILLED    [T1] t1-most-incoming-edges
SURVIVED  [T1] t1-alphabetically-last
SURVIVED  [T1] t1-longest-chain-endpoint
KILLED    [T2] t2-empty-string
KILLED    [T2] t2-swapped-pair-order
SURVIVED  [T2] t2-skip-first-edge
KILLED    [T2] t2-first-edge-only
KILLED    [T2] t2-returns-list
KILLED    [T2] t2-inclusive-source-set

Tier-1 kill rate: 3/6 = 0.50   (required 1.00)
Tier-2 kill rate: 5/6 = 0.83   (required 0.80)
```

**Three realistic wrong solutions pass all four hidden tests today**, including
`return paths[-1][1]` and `max(cities)` — the latter is right on every worked
example in the statement purely by coincidence of city names (Rome, D, Z, D are
each alphabetically greatest). This is a measured defect in q1436's
discriminating power, not a hypothetical one.

The suite expansion that would fix it is a write to `hidden_test_cases` —
grading truth — and it gets its own reviewed phase, exactly as q3309's
SUITE_EXPANSION did. I have not written it.

## H. Regression

```
2582 passed, 2 warnings in 250.73s
```

No new code was written this phase, so the testing requirements attach to
nothing new: the deliverables are a reference source, a JSON spec, and two
production rows created through existing, already-tested commands.

**Infrastructure, reported separately:** the local Postgres container stopped
twice during this phase (`PoolTimeout`; one run reported 1790 setup errors and
792 non-DB passes). Docker Desktop was restarted and the container brought back
up both times; the regression above is the clean run. Unrelated to any code.

## I. Production safety

Two intended writes, both through `--alias oracle` and both authorized by Step 3:
ReferenceSolution #3 created, then moved DRAFT → IN_REVIEW. Nothing else.

```
q3309 untouched (verified above, byte for byte via digest rewind)
q1436 digest 0b2a79f2… UNCHANGED — a reference row is not question state
q963 / q264 / q266 / q1689 untouched
no oracle executions, no approvals, no promotions, no publications
adaptive-eligible submissions, whole bank: 0
```

## J. The next q1436 commands

Not the oracle yet — the suite must be expanded first, or the oracle will spend
Judge0 executions producing evidence for four cases that a fifth of realistic
wrong answers already pass. In order:

1. **Human review of reference #3** (this phase's stop). Then:
   ```bash
   python manage.py reference_review approve 3 --operator <reviewer> --alias oracle --confirm
   python manage.py reference_review activate 3 --operator Suhas --alias oracle
   ```
2. **Expand and label the suite** — 4 → ≥12 cases carrying `singleton` and
   `duplicate_values`, plus the substituted `unordered_edges` and
   `branching_convergent`, with cases that kill the three surviving Tier-1
   mutants (a chain whose last edge is not the sink; a graph whose sink is not
   alphabetically greatest; a city with two outgoing edges). Via
   `expand_hidden_tests`, its own review.
3. **Then** the quality gate through Judge0, then the oracle:
   ```bash
   python manage.py oracle_execute --question 1436 --operator Suhas --alias oracle --confirm
   ```
