# P2.7 — q1436 suite expanded and verified; the quality gate is blocked by Judge0

The suite expansion is **done and proved**: 4 → 13 cases, the original four
byte-for-byte intact, every required category covered, one `SUITE_EXPANSION`
action, and the digest landing exactly on the value predicted before the write.

The quality gate then **reported PASS, and that PASS is false.** I am not
recording it.

> Judge0 runs **Python 3.8.1**. The q1436 starter and reference use
> `paths: list[list[str]]` — PEP 585, valid only on 3.9+. On 3.8 that raises
> `TypeError: 'type' object is not subscriptable` at class-definition time, so
> **every** mutant crashed on case 1 and the gate counted 13 crashes as 13
> kills. The reference solution crashes identically.

**q1436 is currently ungradeable**, and so are **775 of the 2,926** Python
starters in the bank.

---

## A. What was applied

One `SUITE_EXPANSION` on q1436, through `expand_hidden_tests --alias hiddentest`
as `learnlm_hidden_test_rw`:

```
before digest  0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
after  digest  4f5748806c5212f5c2be26ac915956c1ed4bfa6bfa6d12ba237aadaea7dd3002
```

The after-digest was **predicted before the write** and matched to the
character.

## B. Pre-apply proof

Nothing was written until all of this held:

| check | result |
|---|---|
| live digest == expected | ✔ |
| existing 4 preserved (stdin, expected_output, identity, every other key) | ✔ |
| 9 additions bind under v3, no warnings | ✔ |
| distinct case identities | 13 / 13, no collisions |
| normalized duplicates | none |
| required categories `singleton`, `duplicate_values` | present |
| substituted `unordered_edges`, `branching_convergent` | present |
| count ≥ 12 | 13 |
| reference #3 agrees with all 13 stored answers | ✔ |
| projected Tier-1 | **6/6 = 1.00** |
| projected Tier-2 effective | **6/6 = 1.00** (1 equivalent excluded) |

## C. The nine new cases, and what each is for

| # | input | answer | category | targets |
|---|---|---|---|---|
| 5 | `[["Zurich","Amsterdam"]]` | `Amsterdam` | alphabetical_trap | **alphabetically-greatest** — the sink sorts first |
| 6 | `[["B","C"],["A","B"]]` | `C` | terminal_edge_not_last | **last-edge destination** — last pair ends at B |
| 7 | `[["C","D"],["B","C"],["A","B"]]` | `D` | unordered_edges | chain supplied fully backwards |
| 8 | `[["B","D"],["A","B"],["D","E"]]` | `E` | unordered_edges | **skip-first-edge** — B is both source and destination |
| 9 | `[["Hub","A"],["Hub","B"],["A","End"],["B","End"]]` | `End` | duplicate_values | repeated city names across pairs |
| 10 | `[["Hub","A"],["Hub","B"],["Hub","C"],["A","End"],["B","End"],["C","End"]]` | `End` | branching_convergent | **most-frequent city** — Hub and End tie |
| 11 | `[["A","B"],["B","C"],["C","D"],["D","E"],["E","F"]]` | `F` | long_chain | longer than any worked example |
| 12 | `[["Yale","Xavier"],["Zeta","Yale"]]` | `Xavier` | alphabetical_trap | unordered **and** alphabetically inverted |
| 13 | `[["Delhi","Mumbai"],["Mumbai","Delhi Junction"]]` | `Delhi Junction` | duplicate_values | shared prefix, embedded space |

The existing four kept their inputs and answers exactly and gained labels only:
`typical_chain`, `unordered_edges`, `singleton`, `branching_convergent`.

No filler: case 8 is the only case that kills `t2-skip-first-edge`, cases 6/7/12
are the only ones that kill `t1-last-edge-destination`, and cases 5/12 are the
only ones that kill `t1-alphabetically-last`.

## D. Post-apply verification

```
digest          4f574880…  == predicted ✔
cases           13  (floor 12 ✔)
rewind          strip the added `category` keys and drop the 9 additions
                ⇒ 0b2a79f2…  == the before digest ✔
originals       cases 1-4 IDENTICAL
malformed       none          identities 13 distinct, 0 collisions
categories      alphabetical_trap, branching_convergent, duplicate_values,
                long_chain, singleton, terminal_edge_not_last, typical_chain,
                unordered_edges   (missing: none)
audit           exactly 1 SUITE_EXPANSION; post_digest == live ✔
pre-image       verifies; still holds the original 4 cases; rollback available
untouched       reference #3 APPROVED/active, hash unchanged
                q1436 DRAFT/UNVERIFIED, contract v3, 0 executions, 0 approvals
                q3309 PUBLISHED/ORACLE_VERIFIED at a88a4e19… ✔
                q963 at 8da0eb14… ✔   q264 / q266 / q1689 at pre-image ✔
```

Two checks failed on first run and **both were my verification script, not the
write** — I chased each to ground rather than adjusting the expectation:

1. The rewind reconstructed the original cases by hand as
   `{stdin, expected_output}`, but the stored cases also carry `explanation`.
   Rewinding by *removing exactly what the write added* returns `0b2a79f2…`
   exactly.
2. It asserted q963 should equal its pre-image. q963 received a
   `STATEMENT_REPAIR` earlier, so it legitimately differs; against the digest
   asserted in every prior phase (`8da0eb14…`) it is unchanged.

## E. The quality gate — a false PASS, refused

Structural validation after the expansion is genuinely clean:

```
hidden tests 13 · malformed 0 · duplicates 0 · missing categories NONE
canonical reference #3
```

The full run then reported `QUALITY_GATE = PASS`, tier 1 = 1.0, tier 2 = 1.0 —
with every one of the 13 mutants "KILLED by case 1, non-accepted status 11".

That is impossible, and the impossibility is what caught it:
`t2-single-successor-traversal` is **provably equivalent** to the reference
under the statement's guarantee (every walk terminates at the unique sink), and
carries a written equivalence argument. A provably-correct program cannot be
killed by a valid test case. So the kills were not about the mutants.

Diagnosis, in order:

1. The reference, built through the gate's own `quality_execution_plan` and run
   **locally**, returns `Rome`, exit 0. The contract and wrapper are fine.
2. The same executable through **Judge0** returns status 11 with
   `TypeError: 'type' object is not subscriptable` on the `def` line.
3. `import sys; print(sys.version)` on Judge0 → **3.8.1**.
4. Minimal probe `def f(x: list[list[str]]): return x` → status 11, same error.

PEP 585 subscripted builtin generics are 3.9+. Judge0 is 3.8.1. Every program
whose signature carries `list[...]` dies before its first statement.

**The gate is not wrong to count a crash as a kill** — its comment reasons that
a learner would see a failure, which is true. What it cannot see is that *all*
mutants crashed for a reason unrelated to their logic. The report has been
quarantined as `reports/q1436-quality.INVALID-judge0-py38.json` so it can never
be handed to `question_approve`; no valid `q1436-quality.json` exists.

## F. The larger finding

This is not a gate problem. It is a **live grading defect**:

```
questions with a Python starter                                    2926
starters using PEP 585 generics (list[...], dict[...], tuple[...])  775   (26%)
published or oracle-verified among them                            none
```

Any learner who submits the *provided starter* for one of those 775 questions
gets a runtime error regardless of whether their solution is correct. q1436 is
one of them. q3309 escaped only because its signature is `strStr(self,
haystack: str, needle: str) -> int` — plain builtins, no subscripting.

The annotation was added to q1436 by the `BOILERPLATE_REPAIR` that made the v3
contract bind. The annotation is correct modern Python; the executor is eight
years behind it.

## G. Regression

```
2582 passed, 2 warnings in 272.40s
```

No new code this phase — the deliverables are a plan file, an expanded suite,
and one audit row. No infrastructure failures.

## H. Where this stops, and why

The brief forbids modifying boilerplate, and says to improve the suite rather
than weaken the gate if a mutant survives. Neither applies here: no mutant
survived, and no test case can rescue a program that cannot start. The blocker
is the execution environment, so it is out of this phase's boundary and needs
your decision:

- **Change the annotations** — `List[List[str]]` with `from typing import List`,
  or `from __future__ import annotations` in the starter. A `BOILERPLATE_REPAIR`
  on q1436, and a much larger decision for the other 774.
- **Upgrade Judge0's Python** to 3.9+. Fixes all 775 at once and changes no
  grading truth, but is an infrastructure change with its own blast radius.

I have not chosen between them. Running the oracle now would spend Judge0
executions recording 13 runtime errors as q1436's answer key.

## FINAL STATUS

```
q1436 REFERENCE     = APPROVED + ACTIVE (#3, hash 2e055194…)
q1436 SUITE         = COMPLETE + VERIFIED (13 cases, digest 4f574880…)
q1436 QUALITY_GATE  = BLOCKED — reported PASS, refused as invalid (Judge0 py3.8)
q1436 ORACLE        = NOT STARTED
q1436 APPROVAL      = NOT STARTED
q1436 PROMOTION     = NOT STARTED
q1436 PUBLICATION   = NOT STARTED
RESEED              = NO
```

Per the brief, the next command is reported only after the gate genuinely
passes. It has not, so there is no next command — there is a decision.
