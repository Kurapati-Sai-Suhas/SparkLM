# P2.7 — q1436 migrated to v3, verified

**The pilot's repair work is complete.** Ten reviewed actions across five
questions; two questions now declare v3 and every stored case in both binds
through the canonical adapter. Nothing has been approved, promoted, oracled or
reseeded.

---

## A. Dry-run

Pre-migration state, all nine preconditions verified through
`learnlm_census_ro`:

```
q1436 live digest    333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
pre-image            0d442588…340508   verifies
contract column      'v1'
statement repair     present (Example 2 synchronised)
input repair         present (case 2 acyclic)
boilerplate repair   present (paths annotated)
batch                CAPTURED, frozen
q264                 untouched          q3309  at its verified v3 state
actions              9                  q1436 has no prior CONTRACT_REPAIR
```

Real-role dry-run through `learnlm_contract_rw`:

```
current digest  333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
projected after 0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
stored value    'v1'  ->  proposed 'v3'
declared        destCity(paths: list[list[str]])     arity 1

case 1  [[["London","New York"],["New York","Paris"],["Paris","Rome"]]]
case 2  [[["B","D"],["A","B"],["C","D"]]]
case 3  [[["A","Z"]]]
case 4  [[["A","B"],["A","C"],["B","D"],["C","D"]]]
```

Every case identity printed as unchanged, with the reason: **this command cannot
write `hidden_test_cases`** — it holds no privilege on that column.

## B. The migration

```
before digest   333e76c74c04ae4d1b2469227961d5f380b507108288d54f17c96cd8207c82f0
after digest    0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
```

## C. Post-digest and the exact change

The after-digest equals the approved plan's step-d projection, computed before
any of q1436's four repairs had been applied.

```
total actions      10        q1436 CONTRACT_REPAIR actions: exactly 1
question           1436      batch p27-pilot-1      operator Suhas
post_digest        0b2a79f29cbeba5e0fca5e0b3140326eef18fe6cbf35359b1aa1ad468509df4a
linked pre-image   0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508

execution_contract_version   'v3'   — and the harness resolves it as v3
```

**Proof that only the contract moved in this write**: recomputing q1436's state
digest with the contract put back to `v1` reproduces
`333e76c7…07c82f0`, the pre-contract digest, exactly.

Against the frozen pre-image, four of seven captured fields have now moved on
q1436 — `content`, `hidden_test_cases`, `boilerplate_code` and
`execution_contract_version` — each by its own reviewed, separately verified
action, each written by a different least-privilege role. `status`,
`trust_state` and `hidden_wrapper_code` are byte-identical to the capture.

## D. Adapter verification, four cases

```
declared  destCity(paths: list[list[str]])

case 1  OK  warnings=()  1 argument  list  == json.loads(stdin)
case 2  OK  warnings=()  1 argument  list  == json.loads(stdin)
case 3  OK  warnings=()  1 argument  list  == json.loads(stdin)
case 4  OK  warnings=()  1 argument  list  == json.loads(stdin)
```

No `undeclared_parameter_type`, one decoded list per case, no input mutated.
Judge0 was not executed and no learner or reference code was run.

This is the defect the milestone opened with, closed for this question: under v1
these four inputs were splatted into 3, 3, 1 and 4 positional arguments against
a one-parameter method. Under v3 each arrives as one list.

## E. Whole-bank safety

```
q264, q1689   byte-identical to their pre-images
q963  8da0eb14…   q17  704a1652…   q266  4df2af27…   q3309  8a342568…
batch         CAPTURED, frozen, 7 members

 1. q963  STATEMENT_REPAIR     6. q3309 INPUT_REPAIR
 2. q17   HIDDEN_TEST_REPAIR   7. q1436 INPUT_REPAIR
 3. q266  HIDDEN_TEST_REPAIR   8. q1436 BOILERPLATE_REPAIR
 4. q3309 STATEMENT_REPAIR     9. q3309 CONTRACT_REPAIR
 5. q1436 STATEMENT_REPAIR    10. q1436 CONTRACT_REPAIR
```

```
questions 2926 · PUBLISHED 0 · ORACLE_VERIFIED 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0

declaring v3        [1436, 3309]        distinct versions  ['v1', 'v3']
```

Exactly two questions declare v3 and they are the two that were repaired for it.
No question declares anything the harness does not know.

```
prior     b1943363379af63e627b139940e58737b1b3f1c30bc60ede59b148e5f7b819ac
current   8dcea5053a365916c15022a9cbb4ef42310b7e435ee3fce4208d8c80636edb24
```

Recomputing the whole-bank fingerprint with q1436's contract put back to `v1`
reproduces the prior baseline exactly, so
`q1436.execution_contract_version` is the only field in the bank that moved.

**New baseline:**

```
8dcea5053a365916c15022a9cbb4ef42310b7e435ee3fce4208d8c80636edb24
```

## F. Regression

```
2294 passed, 0 failed, 0 errors   (groups, common, learning — 3m03s)
contract + adapter + contract-version + role-contract suites   194 passed
contract-repair mutation (re-run this phase)   23 killed / 24,
                                               1 planted equivalent, 0 real
```

## G. Rollback readiness — ready, not executed

```
pre-image verifies      yes
pre-image contract      'v1'
rollback target         0d4425883fdff4b992d71493e91e1573f3967477ee392fa712a6f86555340508
live == recorded post   true (no divergence)
batch                   frozen
status / trust          DRAFT / UNVERIFIED      adaptive eligible: False
```

Rolling q1436 back restores the state before all four of its repairs at once.
Rollback has still never executed against real data — worth saying plainly now
that ten actions depend on it being real.

## H. Where the pilot stands, and the next phase

Every repair the adjudication authorised is done:

```
q963   STATEMENT_REPAIR                     COMPLETE
q17    HIDDEN_TEST_REPAIR                   COMPLETE
q266   HIDDEN_TEST_REPAIR                   COMPLETE
q3309  STATEMENT + INPUT + CONTRACT         COMPLETE
q1436  STATEMENT + INPUT + BOILERPLATE
       + CONTRACT                           COMPLETE
q264   KEEP — control, known key defect recorded, untouched
q1689  MANUAL_REVIEW — statement unrecoverable from repo evidence
```

**q3309 and q1436 are now executable as declared. Neither is trusted**, and the
distinction is the whole point of the trust boundary: both are still
`DRAFT / UNVERIFIED`, no reference solution exists for either, no oracle has run,
nothing is approved or promoted, and `adaptive_eligible` remains 0 across all 44
submissions.

Two things remain open from the adjudication and neither has moved:

- **q963's keys** are still wrong (cases 2 and 3), and its statement now
  disagrees with them visibly. `KEY_REPAIR_AFTER_ORACLE` is unstarted and
  requires an oracle.
- **q264, the control, has a known wrong key** (n=15 stored 30, correct 24),
  deliberately unrepaired so the batch kept an unmoved reference.

The natural next phase is the one this whole chain was built to make safe:
**an oracle run against the two v3 questions** — a reference solution, executed,
compared against the stored keys — followed by approval and, only then,
promotion. That is a larger authorisation than anything so far, because it is
the first step that can make a question *count* for a learner.

Before that, I would exercise **`preimage_rollback` on one repaired question**
deliberately. It has never run against real data, ten actions now assume it
works, and the pilot is the cheapest place to find out otherwise.

---

```
q3309 CONTRACT_REPAIR = COMPLETE + VERIFIED
q1436 CONTRACT_REPAIR = COMPLETE + VERIFIED
q963 KEY_REPAIR       = NOT STARTED
ORACLE                = NOT STARTED
APPROVAL              = NOT STARTED
PROMOTION             = NOT STARTED
BATCH                 = FROZEN
RESEED                = NO
```
