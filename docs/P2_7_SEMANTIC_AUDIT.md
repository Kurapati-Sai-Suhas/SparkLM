# P2.7 — Randomized semantic answer-key audit

**Status:** complete. READ-ONLY. Zero production writes. RESEED = NO.

Every conflict below was re-derived from the problem statement with an
independent implementation run locally. No Judge0, no Oracle, no writes.

---

## A. Population definition

Read-only census via `learnlm_census_ro` against production:

| | count |
|---|---:|
| total questions | 2926 |
| with hidden tests | 1785 |
| with no hidden tests | 1141 |
| structurally analysable (parseable Python starter) | 2818 |
| **graded + analysable — the working denominator** | **1677** |

Verdicts under canonical execution, over the 1,785 questions that have tests:

| verdict | count |
|---|---:|
| INVALID_INPUT | 43 |
| CONTRACT_MISMATCH | 170 |
| NEEDS_MANUAL_REVIEW | 457 |
| NEEDS_MIGRATION | 615 |
| SAFE | 500 |

All 2,926 are `status=DRAFT`, contract `v1`, `trust_state=UNVERIFIED`. Zero
declare v3.

**The semantic denominator is not any of these.** Auditable cases are counted
in §D and exclude everything that cannot be evaluated at all.

## B. The deterministic sample

Seed `20250815`, pilot 1779 excluded, stratified round-robin. Re-run in this
phase and produced byte-identical IDs to the previous phase:

```
q963  q1664 q17   q1716 q716  q266  q1265 q3318 q2201 q1896
q1100 q782  q3309 q1689 q1436 q622  q3339 q687  q381  q118
```

No replacements were needed and none were made.

## C. Per-question audit

`v` = verdict spread over that question's cases.

| q | title | cases | v | defects |
|---|---|---|---|---|
| 963 | Minimum Area Rectangle II | 4 | 1 agree, **2 conflict**, 1 unassessable | A, D, F |
| 1664 | Merge In Between Linked Lists | 4 | 3 ambiguous, 1 unassessable | D, E, C |
| 17 | Letter Combinations of a Phone Number | 4 | 2 agree, 2 unassessable | F |
| 1716 | Swap Nodes in Pairs | 5 | 5 agree | C, D |
| 716 | Max Stack | 4 | 4 unassessable | C, D |
| 266 | Palindrome Permutation | 4 | 4 agree | **B (all 4)** |
| 1265 | Print Immutable Linked List in Reverse | 4 | 4 agree | **B (all 4)** |
| 3318 | Min Stack | 4 | 4 unassessable | C |
| 2201 | Largest Number After Digit Swaps by Parity | 4 | 3 agree, **1 conflict** | A, C, D |
| 1896 | Find a Peak Element II | 4 | 3 agree, **1 conflict** | A, C, E |
| 1100 | K-Length Substrings, No Repeats | 2 | 1 agree, **1 conflict** | A, C |
| 782 | Transform to Chessboard | 4 | 2 agree, 2 unassessable | F |
| 3309 | First Occurrence in a String | 5 | 4 agree, 1 unassessable | C |
| 1689 | Reformat Phone Number | 4 | **4 conflict** | A, D |
| 1436 | Destination City | 4 | 3 agree, **1 conflict** | A, C |
| 622 | Design Circular Queue | 4 | 4 unassessable | C, D |
| 3339 | Is Subsequence | 5 | 3 agree, 2 unassessable | **B**, C |
| 687 | Longest Univalue Path | 4 | 3 agree, **1 conflict** | A, C, D |
| 381 | Insert Delete GetRandom | 3 | 3 unassessable | C, E |
| 118 | Pascal's Triangle | 1 | **1 conflict** | A, C, D |

### The twelve conflicts, each independently re-derived

| q | case | stored key | independently computed | why the key is wrong |
|---|---|---|---|---|
| 963 | 2 | `0` | `1` under **both** readings | the unit square (0,0),(0,1),(1,0),(1,1) is present |
| 963 | 3 | `2` | `1` under both readings | a smaller rectangle exists |
| 1100 | 1 | `2` | 4 occurrences / 3 distinct | neither reading yields 2 |
| 1689 | 1 | `180-055-50144` | `180-055-501-44` (LC) / `800-555-0144` (statement) | key matches no rule |
| 1689 | 2 | `4420356621555` | `442-035-662-15-55` / `442-035-6621555` | key is unformatted digits |
| 1689 | 3 | `134-441-2344-123` | `001-344-412-344-41-23` | key silently drops digits |
| 1689 | 4 | `123-456-7890-12345` | `123-456-789-012-345` | 5-digit final group is not a valid grouping |
| 1436 | 2 | `D` | none exists | `B→C→D→B` is a cycle; `D` has an outgoing edge |
| 1896 | 2 | `0 4` | only peak is `4 4` | `mat[0][4]=5` has neighbour `10`, so it is not a peak |
| 2201 | 4 | `8899` | `9978` | key sorts all digits, ignoring the parity constraint |
| 687 | 3 | `7` | 4 edges / 5 nodes | 7 identical nodes cannot give a path of 7 under either definition |
| 118 | 1 | `1` | `[1,1]` (getRow) / `[[1]]` (triangle) | row 1 of Pascal's triangle is `[1,1]` |

## D. Case statistics

| verdict | cases |
|---|---:|
| AGREE | 38 |
| CONFLICT | 12 |
| AMBIGUOUS | 3 |
| UNASSESSABLE | 24 |
| **total** | **77** |

- assessable cases (excl. UNASSESSABLE): **53**
- decided cases (also excl. AMBIGUOUS): **50**

## E. Conflict rate

| measure | value |
|---|---|
| conflicting cases / assessable | 12/53 = **22.6%** |
| conflicting cases / decided | 12/50 = **24.0%** |
| questions with ≥1 conflict / assessable questions | 8/16 = **50.0%** |

Four questions (716, 3318, 622, 381) are fully unassessable — all class-design
problems the harness cannot invoke.

By stratum (conflicts / assessable cases):

```
text_retyped           4/6   66.7%      type_unstable      1/9   11.1%
clean                  1/2   50.0%      blank_stdin        1/8   12.5%
non_string_test_field  2/5   40.0%      arity_mismatch     0/7    0.0%
variadic_starter       2/5   40.0%      boolean_casing     0/7    0.0%
unannotated            1/4   25.0%
```

**Every audited starter was Python**, so no per-language rate can be computed
from this sample.

## F. Confidence interval — and what it does not mean

95% Wilson intervals:

| quantity | point | interval |
|---|---|---|
| conflicting cases / assessable | 22.6% | **[13.5%, 35.5%]** |
| conflicting cases / decided | 24.0% | [14.3%, 37.4%] |
| questions with ≥1 conflict | 50.0% | [28.0%, 72.0%] |

**These describe the sample, not the bank.** The design is a stratified
round-robin with one question drawn per stratum per pass, so inclusion
probability varies by orders of magnitude: `variadic_starter` holds 2 questions
bank-wide and received 2 of 20 slots, while `arity_mismatch` holds 835 and also
received 2. The draws are unweighted, and questions appear in several strata at
once, which rules out a clean stratified estimator.

So: **no bank-wide conflict rate is claimed, and the interval must not be read
as one.** What the sample does support is weaker and still decisive — conflicts
appear in 6 of the 9 strata that produced assessable cases, *including the
`clean` stratum*, so semantic key errors are not confined to questions that
look structurally broken.

## G. Comparison with pilot 1779

1779 is **excluded** from every number above and is not merged into any
statistic.

| | conflicts / cases | rate |
|---|---|---|
| pilot 1779 | 4 / 10 | 40.0% |
| this sample | 12 / 53 | 22.6% [13.5%, 35.5%] |

1779's 40% sits just above the sample's interval; at the question level, 1779
having conflicts is the *typical* outcome — half the assessable sampled
questions do.

**Verdict: plausible systemic problem.** Not an isolated anomaly. The evidence
is that conflicts recur across independent strata and across unrelated problem
types, and that they were found by re-derivation from the statements rather
than by comparing two generated artefacts. It is not yet a *measured* bank-wide
rate, for the sampling reasons in §F.

## H. Mechanical vs semantic defects

| class | description | cases | questions |
|---|---|---:|---:|
| **A** | semantic answer-key error | 12 | 8 |
| **B** | mechanical output-format defect | 11 | 3 |
| **C** | execution-contract defect | 34 | 13 |
| **D** | statement/example defect | 24 | 9 |
| **E** | ambiguous specification | 9 | 3 |
| **F** | structurally broken hidden test | 5 | 3 |

Cases carry several classes; these are not a partition.

**The largest class is not semantic error — it is execution contract (13 of 20
questions).** Two of the mechanical findings are worth naming:

- **q1265** — the function prints and returns `None`, so the wrapper appends
  `None` to stdout. Verified by executing a *correct* solution through the real
  template: stored `'4\n3\n2\n1\n'` vs actual `'4\n3\n2\n1\nNone\n'`. **No
  correct submission can pass any of its 4 cases.**
- **q266, q3339** — keys stored as `True`/`False`; the wrapper emits
  `true`/`false`. Semantically correct, mechanically unpassable. 64 such
  questions bank-wide.

And the statement defects are substantial: **9 of 20 questions** have a
statement that contradicts its own title, its own example, or its own key —
q963 (statement says axis-parallel, title and key say any orientation, and the
worked example cites points absent from the input), q687 (says "number of
nodes", keys use edges), q1689 (says `XX-XXX-XXXX`, examples show `XXX-XXX-XXXX`
and country-code stripping), q118 (title is LC 118, statement is LC 119),
q716 (`top()` of `[5,1,3]` given as `1`), q2201 (a placeholder template with no
problem content at all).

## I. Recommended strategy: **C — Hybrid**

Not A alone, and emphatically not B alone.

**Against pure reseed (B):** regenerating keys means generating them *from the
statements*, and 9 of 20 sampled statements are themselves defective. A reseed
would launder statement defects into fresh, confidently-wrong keys carrying new
provenance — the failure this milestone exists to prevent, executed at scale.

**Against pure repair (A):** 12 conflicts across 8 questions cannot be repaired
mechanically; each needs a human to decide what the problem actually asks.
Meanwhile 170 CONTRACT_MISMATCH and 43 INVALID_INPUT questions have stored data
no adapter can interpret.

**The hybrid, in dependency order:**

1. **Mechanical repair — deterministic, no judgement.** 64 boolean-casing
   questions, 48 non-string test fields, and the print-returns-`None` family.
   These are transformations with a provably correct output; no LLM, no oracle.
2. **Contract migration — 615 NEEDS_MIGRATION.** Adopt v3 per question. Keys
   must be **re-derived by oracle**, not carried over: canonical execution
   passes different arguments than the ones that produced the stored values.
3. **Statement remediation FIRST for classes D and E.** This is the bottleneck
   and it gates everything downstream. A key cannot be correct against an
   incoherent statement.
4. **Annotate starters — 457 NEEDS_MANUAL_REVIEW.** Content work; converts
   questions into the adapter's declared-type path.
5. **Quarantine class-design and randomised-output questions.** 49 ambiguous
   entry points bank-wide. No current harness invokes them, and q381's key is
   literally `'5 or 7'`, which exact-match grading can never evaluate.

### Costs, as asked

- **Grading-truth rewrite volume** — mechanical repair touches ~112 questions
  with provably correct transformations. Contract migration re-derives keys for
  up to 615. Both are bounded by census counts, not by extrapolation.
- **Human adjudication** — the binding constraint. At the sampled rate, roughly
  half of assessable questions need a human decision, and 9 of 20 need a
  statement rewritten before any key can be judged.
- **Reference-solution burden** — every migrated question needs an approved
  reference before the oracle may mint its keys. Today the bank has **1**.
- **Hidden-test burden** — no sampled question meets the 12-case floor (§J), so
  test generation is required almost everywhere, and it must come after the
  contract is fixed.
- **Provenance** — repaired keys stay UNPROVENANCED until oracle-verified and
  human-adjudicated. Mechanical repair does not confer provenance; it only
  removes a defect that made a correct answer unpassable.
- **Risk of propagation** — highest in step 3 skipped, lowest in step 1.
- **Effect on existing learner submissions** — all 2,926 questions are DRAFT
  and none is ORACLE_VERIFIED, so no submission has ever been adaptive-eligible.
  Historical learner evidence is therefore unaffected by re-deriving keys, which
  is the single biggest thing making this remediation cheap. **Verify before
  relying on it.**
- **Rollback** — remediation must be batched, with the pre-image of every
  changed row captured and a per-batch digest, so any batch can be reverted
  without touching its neighbours. Nothing in the current tooling does this yet.

## J. Quality-gate interaction (P2.7h-1) — not wired

**Two blocking incompatibilities, plus one defect found in the gate itself.**

**1. The 12-case floor is met by zero sampled questions.** Case counts ranged
1–5. `MIN_HIDDEN_TESTS = 12`, so the gate would reject 20/20.

**2. Zero-argument questions cannot satisfy it in principle.**
`validate_case` rejects blank stdin (`'stdin' is empty`) and `validate_suite`
flags duplicate stdin — a zero-argument question has exactly one possible
input, so 12 non-duplicate cases are unreachable. This needs explicit
`NOT_APPLICABLE` semantics, which the module already has a constant for.

**3. NEW — the gate bypasses the shared execution seam.**
`hidden_test_quality.py:256` calls

```python
verdict = runner(mutant.source, mutant.language, case.get("stdin", ""))
```

It passes **raw stored stdin** and never calls `GradingService._build_executable`
or `prepare_stdin`. Wired as-is it would compute Tier-1/Tier-2 kill rates under
input semantics no learner ever experiences — a suite could pass the gate while
grading learners differently, which is precisely the grader/oracle divergence
this milestone eliminated everywhere else. **This must be fixed before the gate
is wired.**

**Tier-1 (100% kill) / Tier-2 (≥80%)** additionally require the question to be
*executable*. For 170 CONTRACT_MISMATCH + 43 INVALID_INPUT + the class-design
questions, mutants cannot run and the gate correctly reports `EXECUTION_ERROR`
as a BLOCKER. So the gate cannot meaningfully run until contract repair lands —
ordering, not a defect.

## K. Remaining blockers before answer-key remediation

1. Statement remediation for classes D and E — **gates everything else**.
2. 162 CONTRACT_MISMATCH + 43 INVALID_INPUT need human data repair.
3. 457 NEEDS_MANUAL_REVIEW need starter annotations.
4. 49 ambiguous entry points need a decision (quarantine, rewrite, or a
   class-design execution model).
5. v2's coercion defect — unfixable server-side; decision needed on changing
   `V2_PYTHON_WRAPPER`.
6. Reference-solution pipeline at scale: 1 reference exists; migration needs
   one per question.
7. Batched rollback tooling with pre-image capture — does not exist.
8. Confirm no learner evidence depends on current keys (expected true: 0
   PUBLISHED, 0 ORACLE_VERIFIED — verify before relying on it).

## L. Remaining blockers before reseed

**RESEED = NO.**

1. execution semantics — done for v1 via v3; **not** for v2;
2. semantic key error rate — **measured on this sample** (22.6% of assessable
   cases), but **not** a bank-wide rate, and the design does not support one;
3. CONTRACT_MISMATCH repair — not started;
4. INVALID_INPUT repair — not started;
5. NEEDS_MANUAL_REVIEW classification — not started;
6. answer-key remediation strategy — **recommended (C, hybrid), not approved**;
7. reference/oracle/adjudication workflow — pilot 1779 only;
8. hidden-test strategy — not started; no sampled question meets the floor;
9. P2.7h-1 wiring — blocked on the three items in §J;
10. small-batch remediation — not started;
11. post-batch validation — not started.

---

## Method notes

- Conflicts were established by **independent re-derivation** from the problem
  statement — brute force over point pairs for q963, occurrence *and* distinct
  counting for q1100, both grouping rules for q1689, edge *and* node definitions
  for q687, exhaustive peak checking for q1896, parity-constrained rearrangement
  for q2201, cycle detection for q1436. Where a statement admitted two readings,
  both were computed and the key was only called wrong if it matched neither.
- q1265's mechanical defect was confirmed by **executing** a correct solution
  through the real wrapper, not by reading it.
- Two errors in my own harness were caught and corrected before reporting: an
  integral-float formatting bug that would have reported q963 case 1 as a false
  conflict, and a `repr()` comparison that made all four q1265 cases look
  semantically wrong when only their format is.
- Production access was read-only throughout (`learnlm_census_ro`, no write
  privileges). No Judge0 call, no oracle run, no row modified.
