# P2.7 — q963 statement repair verified

**The first production grading-truth write in this milestone is complete and
verified.** Exactly one field of exactly one question changed, and that is
proven across all 2,926 questions rather than asserted.

---

## A. Remediation action — exactly one

```
total actions      1
question           963
batch              p27-pilot-1
action_class       STATEMENT_REPAIR
applied_by         Suhas
applied_at         2026-08-17 07:09:53
post_digest        8da0eb14d1a98d414d0aa3e9ba6518980db46b5065e3c8907e3e839fd27ab688
linked pre-image   06a9bb6b6d4ab57b608b3226bffc5bc4285f5d0b3820f0dd7f65edaf37eda59c
reason             adjudication record section B: any-orientation reading;
                   Example 1 explanation described Example 2 input;
                   Example 2 key 0 was false for its own input
```

**One point of precision on the before-digest.** The action row stores
`post_digest` only; the before-digest is carried by the linked
`QuestionPreImage`, which *is* the before state. That is the design rather than
an omission — storing it twice would create two sources for one fact, and the
pre-image is the one rollback actually restores from. Both values are recorded
and both match what the command reported.

## B. Before / after

| | |
|---|---|
| before (pre-image, unchanged) | `06a9bb6b…eda59c` |
| after (live) | `8da0eb14…7ab688` |

Both match the values you observed, and the after-digest matches the projection
computed during the dry-run — the write produced exactly what was previewed.

## C. Fields changed

**`content` only.** 860 → 838 bytes.

## D. Fields unchanged — byte-identical to the pre-image

```
status                       identical
trust_state                  identical
execution_contract_version   identical
boilerplate_code             identical
hidden_wrapper_code          identical
hidden_test_cases            identical
```

The stored keys are still `['2', '0', '2', 1]` — cases 2 and 3 remain wrong,
and case 4 is still the `int` rather than text. That is the intended
intermediate state: **q963's statement is now correct while its keys are not,
and they disagree visibly** until `KEY_REPAIR_AFTER_ORACLE`.

## E. The other six — all unchanged

```
q17     unchanged
q264    unchanged   <- SAFE control, byte-identical
q266    unchanged
q1436   unchanged
q1689   unchanged
q3309   unchanged
```

The control has now survived capture, freeze, and a live production write to a
sibling question.

## F. Batch still frozen

```
batch_key    p27-pilot-1
state        CAPTURED
frozen_at    2026-08-17 05:32:58 UTC
membership   [17, 264, 266, 963, 1436, 1689, 3309]
```

Unaltered by the repair.

## G. Grading-truth impact

```
questions 2926 · DRAFT 2926 · PUBLISHED 0
UNVERIFIED 2926 · ORACLE_VERIFIED 0 · declaring v3 0
ReferenceSolution 1 · OracleExecution 20 · QuestionApproval 0
CodeSubmission 44 · adaptive_eligible 0
```

Every count identical to the pre-repair baseline. No question changed status or
trust state, so nothing became adaptive-eligible and no learner evidence is
affected.

**The fingerprint changed, as expected:**

```
baseline (pre-repair)  1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
current                783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5
```

### Proving the change is confined to q963.content

A changed fingerprint on its own says only "something moved". To show *what*, I
recomputed the whole-bank fingerprint with q963's content substituted back from
the pre-image, leaving every other value exactly as it is in production now:

```
reconstructed          1981a6210171b461720b043d92bd5e688d35cc199fd46f49f061533d31b8246f
                       == the original baseline, exactly
```

Restoring that single field reproduces the pre-repair fingerprint bit for bit.
Since the fingerprint covers `id`, `hidden_test_cases`, `content`, `status`,
`trust_state` and `execution_contract_version` for **all 2,926 questions**,
this proves q963.content is the only field in the entire bank that moved —
not merely the only one among the seven in the batch.

**New baseline for subsequent phases:**

```
783354ae1d3a32056885706fbba3bc5ceaa9a01e5a84604cfbb93a6bfed086b5
```

## H. Rollback readiness — ready, not executed

```
pre-image verifies         yes
pre-image holds            the ORIGINAL 860-byte statement
live matches post_digest   true (no divergence since the repair)
restore target             06a9bb6b…eda59c
```

The immutable pre-image still holds the pre-repair statement, so q963 can be
restored to `06a9bb6b…eda59c` at any time. Rollback was **not** run.

Worth stating plainly: rollback has still never executed against real data. It
is mutation-tested and covered by local tests, but the first real invocation
will be its first real test.

## I. Exact next action

Nothing was done beyond q963's statement. The remaining approved work, in the
order the pipeline design fixed:

1. **q17** — `HIDDEN_TEST_REPAIR`, serialise two list values to canonical
   strings. Mechanical, no oracle. Needs a column grant on
   `hidden_test_cases`, which the remediation role deliberately does not hold.
2. **q266** — `HIDDEN_TEST_REPAIR`, boolean casing. Same grant, same class.
3. **q1436**, **q3309** — `CONTRACT_REPAIR`, plus the two input decisions you
   already made.
4. **q963** — `KEY_REPAIR_AFTER_ORACLE`. Requires a reference, oracle
   execution, and adjudication. Not authorised.
5. **q1689** — remains `MANUAL_REVIEW`.
6. **q264** — remains the control; its known key defect is recorded for a later
   batch.

Each of 1–3 needs its own column-level grant and its own review. The current
remediation role can change statements and nothing else, so none of them can
proceed by accident.

---

```
q963 STATEMENT_REPAIR = COMPLETE
q963 KEY_REPAIR       = NOT STARTED
ORACLE                = NOT STARTED
BATCH                 = FROZEN
RESEED                = NO
```
